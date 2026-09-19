import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import AddressBook
from app.core.session_auth import require_session

router = APIRouter(prefix="/directory", tags=["directory"])

# The single "who do I know" list — unifies what used to be a separate
# address book (chat nicknames) and counterparties (vendor/employee/
# contractor) system. See AddressBook's own docstring in app/db/models.py.
TYPES = ("vendor", "customer", "employee", "contractor", "friend", "other")


class DirectoryEntry(BaseModel):
    nickname: str
    address: str
    chain: str = "evm"
    type: str = "friend"
    display_name: str | None = Field(None, max_length=160)
    tags: list[str] = []
    notes: str | None = Field(None, max_length=2000)


class RenameDirectoryEntry(BaseModel):
    nickname: str


def _row(r: AddressBook) -> dict:
    return {
        "id": r.id, "nickname": r.nickname, "address": r.address, "chain": r.chain,
        "type": r.type, "display_name": r.display_name or r.nickname.removesuffix(".sara"),
        "tags": json.loads(r.tags or "[]"), "notes": r.notes, "active": r.active,
    }


@router.get("")
def list_entries(type: str | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(AddressBook).filter(AddressBook.chain == "evm")
    if type is not None:
        query = query.filter(AddressBook.type == type)
    if active is not None:
        query = query.filter(AddressBook.active == active)
    rows = query.order_by(AddressBook.nickname).all()
    return [_row(r) for r in rows]


@router.post("", dependencies=[Depends(require_session)])
def add_entry(body: DirectoryEntry, db: Session = Depends(get_db)):
    nick = body.nickname.strip().lower()
    if not nick:
        raise HTTPException(400, "Nickname required")
    # Directory nicknames are free, local, unverified aliases - they must
    # never shadow a bare name a "send to X" could also resolve as a paid,
    # on-chain Sara Name (app.tools.names.sara_names), or nobody would ever
    # buy one. Namespacing every directory entry under ".sara" reserves the
    # bare label exclusively for the real registry.
    if not nick.endswith(".sara"):
        nick = nick + ".sara"
    if body.chain.lower() != "evm":
        raise HTTPException(400, "Sara now supports EVM addresses only")
    if body.type not in TYPES:
        raise HTTPException(400, f"type must be one of {TYPES}")
    from web3 import Web3
    if not Web3.is_address(body.address):
        raise HTTPException(400, "Enter a valid EVM address")
    row = db.query(AddressBook).filter(AddressBook.nickname == nick).first()
    if row:
        row.address = body.address
        row.chain = "evm"
    else:
        row = AddressBook(nickname=nick, address=body.address, chain="evm")
        db.add(row)
    row.type = body.type
    row.display_name = (body.display_name or "").strip() or None
    row.tags = json.dumps(body.tags[:20])
    row.notes = body.notes
    db.commit()
    db.refresh(row)
    return _row(row)


@router.patch("/{entry_id}", dependencies=[Depends(require_session)])
def rename_entry(entry_id: int, body: RenameDirectoryEntry, db: Session = Depends(get_db)):
    row = db.query(AddressBook).filter(AddressBook.id == entry_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    nick = body.nickname.strip().lower()
    if not nick:
        raise HTTPException(400, "Nickname required")
    # Same ".sara" namespacing rule as add_entry — a rename must not be able
    # to produce a bare label that could shadow a paid Sara Name either.
    if not nick.endswith(".sara"):
        nick = nick + ".sara"
    conflict = db.query(AddressBook).filter(AddressBook.nickname == nick, AddressBook.id != entry_id).first()
    if conflict:
        raise HTTPException(400, f'"{nick}" is already used by another saved address')
    row.nickname = nick
    db.commit()
    return {"status": "renamed", "nickname": nick}


@router.delete("/{nickname}", dependencies=[Depends(require_session)])
def delete_entry(nickname: str, db: Session = Depends(get_db)):
    nick = nickname.strip().lower()
    row = db.query(AddressBook).filter(AddressBook.nickname == nick).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}
