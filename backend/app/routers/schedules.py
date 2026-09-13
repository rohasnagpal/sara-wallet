from datetime import datetime

from dateutil.rrule import rrulestr
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from web3 import Web3

from app.core.amounts import to_base_units
from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import Schedule, ScheduleRun, Wallet
from app.db.session import get_db

router = APIRouter(prefix="/schedules", tags=["schedules"], dependencies=[Depends(require_session)])

_KINDS = ("recurring_payment", "payroll_salary")


def _resolve_decimals(network: str, token: str) -> int:
    from app.core.assets import NETWORKS
    from app.tools.market.paraswap import resolve_token
    native = NETWORKS.get(network, {}).get("native")
    if token.upper() == native:
        return 18
    resolved = resolve_token(token, network)
    if not resolved:
        raise HTTPException(400, f"{token} could not be resolved on {network}")
    return resolved[1]


def _row(row: Schedule) -> dict:
    return {
        "id": row.id, "kind": row.kind, "wallet_id": row.wallet_id, "network": row.network, "token": row.token,
        "counterparty_id": row.counterparty_id, "recipient_address": row.recipient_address,
        "amount_raw": row.amount_raw, "fiat_amount": row.fiat_amount, "fiat_currency": row.fiat_currency,
        "memo": row.memo, "rrule": row.rrule, "timezone": row.timezone,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "active": row.active, "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class ScheduleBody(BaseModel):
    kind: str = "recurring_payment"
    wallet_id: int
    network: str
    token: str
    counterparty_id: int | None = None
    recipient_address: str | None = None
    amount: str | None = None
    fiat_amount: str | None = None
    fiat_currency: str | None = Field(None, max_length=10)
    memo: str | None = Field(None, max_length=500)
    rrule: str = Field(..., max_length=500)
    timezone: str = "UTC"
    start_date: datetime
    end_date: datetime | None = None


def _validate(body: ScheduleBody, db: Session) -> None:
    if body.kind not in _KINDS:
        raise HTTPException(400, f"kind must be one of {_KINDS}")
    if not db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first():
        raise HTTPException(404, "EVM wallet not found")
    from app.core.assets import network_enabled
    if not network_enabled(body.network):
        raise HTTPException(400, f"network '{body.network}' is disabled")
    if not body.recipient_address and not body.counterparty_id:
        raise HTTPException(400, "recipient_address or counterparty_id is required")
    if body.recipient_address and not Web3.is_address(body.recipient_address):
        raise HTTPException(400, "Invalid recipient address")
    if bool(body.amount) == bool(body.fiat_amount):
        raise HTTPException(400, "Provide exactly one of amount (fixed crypto) or fiat_amount (fiat-locked)")
    if body.fiat_amount and not body.fiat_currency:
        raise HTTPException(400, "fiat_currency is required when fiat_amount is set")
    try:
        rrulestr(body.rrule, dtstart=body.start_date)
    except Exception as exc:
        raise HTTPException(400, f"Invalid rrule: {exc}")


@router.post("")
def create_schedule(body: ScheduleBody, db: Session = Depends(get_db)):
    if body.recipient_address and not Web3.is_address(body.recipient_address):
        from app.tools.names.resolver import resolve_recipient_input
        resolved = resolve_recipient_input(db, body.recipient_address, body.network)
        if not resolved:
            raise HTTPException(400, "Invalid recipient address, and no matching address book entry or Sara Name found")
        body.recipient_address = resolved.address
    _validate(body, db)
    amount_raw = None
    if body.amount:
        decimals = _resolve_decimals(body.network, body.token)
        try:
            amount_raw = str(to_base_units(body.amount, decimals, body.token))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    row = Schedule(
        kind=body.kind, wallet_id=body.wallet_id, network=body.network.lower(), token=body.token.upper(),
        counterparty_id=body.counterparty_id, recipient_address=body.recipient_address,
        amount_raw=amount_raw, fiat_amount=body.fiat_amount, fiat_currency=body.fiat_currency,
        memo=body.memo, rrule=body.rrule, timezone=body.timezone, start_date=body.start_date,
        end_date=body.end_date, next_run_at=body.start_date, active=True,
    )
    db.add(row)
    db.flush()
    append_audit(db, "schedule.created", "schedule", resource_id=str(row.id),
                 details={"kind": row.kind, "wallet_id": row.wallet_id, "rrule": row.rrule})
    db.commit()
    return _row(row)


@router.get("")
def list_schedules(kind: str | None = None, active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(Schedule)
    if kind is not None:
        query = query.filter(Schedule.kind == kind)
    if active is not None:
        query = query.filter(Schedule.active == active)
    return {"schedules": [_row(r) for r in query.order_by(Schedule.id).all()]}


@router.get("/{schedule_id}")
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    row = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not row:
        raise HTTPException(404, "Schedule not found")
    return _row(row)


@router.get("/{schedule_id}/runs")
def list_schedule_runs(schedule_id: int, db: Session = Depends(get_db)):
    if not db.query(Schedule).filter(Schedule.id == schedule_id).first():
        raise HTTPException(404, "Schedule not found")
    runs = db.query(ScheduleRun).filter(ScheduleRun.schedule_id == schedule_id).order_by(ScheduleRun.occurrence_date.desc()).all()
    return {"runs": [{
        "id": r.id, "occurrence_date": r.occurrence_date.isoformat(), "status": r.status,
        "batch_id": r.batch_id, "skip_reason": r.skip_reason,
    } for r in runs]}


class ScheduleUpdateBody(BaseModel):
    memo: str | None = None
    amount: str | None = None
    end_date: datetime | None = None
    active: bool | None = None


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleUpdateBody, db: Session = Depends(get_db)):
    row = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not row:
        raise HTTPException(404, "Schedule not found")
    if body.memo is not None:
        row.memo = body.memo
    if body.amount is not None:
        if row.fiat_amount:
            raise HTTPException(400, "This schedule is fiat-denominated; edit fiat_amount by recreating the schedule")
        decimals = _resolve_decimals(row.network, row.token)
        try:
            row.amount_raw = str(to_base_units(body.amount, decimals, row.token))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if body.end_date is not None:
        row.end_date = body.end_date
    if body.active is not None:
        row.active = body.active
    append_audit(db, "schedule.updated", "schedule", resource_id=str(row.id), details=body.model_dump(exclude_unset=True))
    db.commit()
    return _row(row)


@router.delete("/{schedule_id}")
def deactivate_schedule(schedule_id: int, db: Session = Depends(get_db)):
    row = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not row:
        raise HTTPException(404, "Schedule not found")
    row.active = False
    append_audit(db, "schedule.deactivated", "schedule", resource_id=str(row.id), details={})
    db.commit()
    return {"id": row.id, "active": False}
