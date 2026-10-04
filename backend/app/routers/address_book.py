import json
import re

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
    name: str = Field(..., min_length=1, max_length=160)
    handle: str = Field(..., min_length=1, max_length=32)
    address: str
    chain: str = "evm"
    type: str = "friend"
    tags: list[str] = []
    notes: str | None = Field(None, max_length=2000)


class RenameDirectoryEntry(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    handle: str = Field(..., min_length=1, max_length=32)


def _clean_name(value: str) -> str:
    name = " ".join(value.strip().split())
    if not name:
        raise HTTPException(400, "Name required")
    return name


def _clean_handle(value: str) -> str:
    handle = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", handle):
        raise HTTPException(400, "Handle may contain lowercase letters, numbers and internal hyphens only")
    return handle


def _row(r: AddressBook) -> dict:
    return {
        "id": r.id, "handle": r.nickname, "name": r.display_name or r.nickname,
        "address": r.address, "chain": r.chain,
        "type": r.type, "display_name": r.display_name or r.nickname,
        "tags": json.loads(r.tags or "[]"), "notes": r.notes, "active": r.active,
    }


@router.get("")
def list_entries(type: str | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(AddressBook).filter(AddressBook.chain == "evm")
    if type is not None:
        query = query.filter(AddressBook.type == type)
    if active is not None:
        query = query.filter(AddressBook.active == active)
    rows = query.order_by(AddressBook.display_name, AddressBook.nickname).all()
    return [_row(r) for r in rows]


@router.post("", dependencies=[Depends(require_session)])
def add_entry(body: DirectoryEntry, db: Session = Depends(get_db)):
    name = _clean_name(body.name)
    handle = _clean_handle(body.handle)
    if body.chain.lower() != "evm":
        raise HTTPException(400, "Sara now supports EVM addresses only")
    if body.type not in TYPES:
        raise HTTPException(400, f"type must be one of {TYPES}")
    from web3 import Web3
    if not Web3.is_address(body.address):
        raise HTTPException(400, "Enter a valid EVM address")
    row = db.query(AddressBook).filter(AddressBook.nickname == handle).first()
    if row:
        raise HTTPException(400, f'"{handle}" is already used by another saved address')
    row = AddressBook(nickname=handle, address=body.address, chain="evm")
    db.add(row)
    row.type = body.type
    row.display_name = name
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
    name = _clean_name(body.name)
    handle = _clean_handle(body.handle)
    conflict = db.query(AddressBook).filter(AddressBook.nickname == handle, AddressBook.id != entry_id).first()
    if conflict:
        raise HTTPException(400, f'"{handle}" is already used by another saved address')
    row.nickname = handle
    row.display_name = name
    db.commit()
    return {"status": "renamed", "handle": handle, "name": name}


@router.delete("/{nickname}", dependencies=[Depends(require_session)])
def delete_entry(nickname: str, db: Session = Depends(get_db)):
    handle = nickname.strip().lower()
    row = db.query(AddressBook).filter(AddressBook.nickname == handle).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}
