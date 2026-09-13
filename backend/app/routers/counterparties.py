import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from web3 import Web3

from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import Counterparty
from app.db.session import get_db

router = APIRouter(prefix="/counterparties", tags=["counterparties"], dependencies=[Depends(require_session)])

_TYPES = ("vendor", "employee", "contractor")


class CounterpartyBody(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=160)
    type: str = "vendor"
    addresses: dict[str, str] = {}
    default_network: str | None = None
    default_token: str | None = Field(None, max_length=20)
    external_reference: str | None = Field(None, max_length=200)
    tags: list[str] = []
    notes: str | None = Field(None, max_length=2000)


def _validate_addresses(addresses: dict[str, str]) -> dict[str, str]:
    cleaned = {}
    for network, address in addresses.items():
        if not Web3.is_address(address):
            raise HTTPException(400, f"Invalid address for network '{network}'")
        cleaned[network.lower()] = address
    return cleaned


def _row(row: Counterparty) -> dict:
    return {
        "id": row.id, "display_name": row.display_name, "type": row.type,
        "addresses": json.loads(row.addresses or "{}"), "default_network": row.default_network,
        "default_token": row.default_token, "external_reference": row.external_reference,
        "tags": json.loads(row.tags or "[]"), "notes": row.notes, "active": row.active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
def list_counterparties(type: str | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(Counterparty)
    if type is not None:
        query = query.filter(Counterparty.type == type)
    if active is not None:
        query = query.filter(Counterparty.active == active)
    rows = query.order_by(Counterparty.display_name).all()
    return {"counterparties": [_row(r) for r in rows]}


@router.get("/{counterparty_id}")
def get_counterparty(counterparty_id: int, db: Session = Depends(get_db)):
    row = db.query(Counterparty).filter(Counterparty.id == counterparty_id).first()
    if not row:
        raise HTTPException(404, "Counterparty not found")
    return _row(row)


@router.post("")
def create_counterparty(body: CounterpartyBody, db: Session = Depends(get_db)):
    if body.type not in _TYPES:
        raise HTTPException(400, f"type must be one of {_TYPES}")
    addresses = _validate_addresses(body.addresses)
    row = Counterparty(
        display_name=body.display_name.strip(), type=body.type, addresses=json.dumps(addresses),
        default_network=body.default_network, default_token=(body.default_token or None),
        external_reference=body.external_reference, tags=json.dumps(body.tags[:20]), notes=body.notes,
    )
    db.add(row)
    db.flush()
    append_audit(db, "counterparty.created", "counterparty", resource_id=str(row.id),
                 details={"display_name": row.display_name, "type": row.type})
    db.commit()
    return _row(row)


@router.patch("/{counterparty_id}")
def update_counterparty(counterparty_id: int, body: CounterpartyBody, db: Session = Depends(get_db)):
    row = db.query(Counterparty).filter(Counterparty.id == counterparty_id).first()
    if not row:
        raise HTTPException(404, "Counterparty not found")
    if body.type not in _TYPES:
        raise HTTPException(400, f"type must be one of {_TYPES}")
    addresses = _validate_addresses(body.addresses)
    row.display_name = body.display_name.strip()
    row.type = body.type
    row.addresses = json.dumps(addresses)
    row.default_network = body.default_network
    row.default_token = body.default_token or None
    row.external_reference = body.external_reference
    row.tags = json.dumps(body.tags[:20])
    row.notes = body.notes
    append_audit(db, "counterparty.updated", "counterparty", resource_id=str(row.id), details={"display_name": row.display_name})
    db.commit()
    return _row(row)


@router.delete("/{counterparty_id}")
def deactivate_counterparty(counterparty_id: int, db: Session = Depends(get_db)):
    """Deactivates rather than deletes — batches, schedules and transaction
    history may still reference this counterparty, and that accounting
    evidence must not be silently orphaned."""
    row = db.query(Counterparty).filter(Counterparty.id == counterparty_id).first()
    if not row:
        raise HTTPException(404, "Counterparty not found")
    row.active = False
    append_audit(db, "counterparty.deactivated", "counterparty", resource_id=str(row.id), details={})
    db.commit()
    return {"id": row.id, "active": False}
