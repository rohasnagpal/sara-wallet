from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import AddressBook
from app.core.session_auth import require_session

router = APIRouter(prefix="/directory", tags=["directory"])


class DirectoryEntry(BaseModel):
    nickname: str
    address: str
    chain: str = "evm"


@router.get("")
def list_entries(db: Session = Depends(get_db)):
    rows = db.query(AddressBook).order_by(AddressBook.nickname).all()
    return [{"id": r.id, "nickname": r.nickname, "address": r.address, "chain": r.chain} for r in rows]


@router.post("", dependencies=[Depends(require_session)])
def add_entry(body: DirectoryEntry, db: Session = Depends(get_db)):
    nick = body.nickname.strip().lower()
    if not nick:
        raise HTTPException(400, "Nickname required")
    row = db.query(AddressBook).filter(AddressBook.nickname == nick).first()
    if row:
        row.address = body.address
        row.chain = body.chain
    else:
        db.add(AddressBook(nickname=nick, address=body.address, chain=body.chain))
    db.commit()
    return {"status": "saved", "nickname": nick}


@router.delete("/{nickname}", dependencies=[Depends(require_session)])
def delete_entry(nickname: str, db: Session = Depends(get_db)):
    nick = nickname.strip().lower()
    row = db.query(AddressBook).filter(AddressBook.nickname == nick).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}
