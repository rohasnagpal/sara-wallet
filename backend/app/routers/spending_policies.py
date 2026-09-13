import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.amounts import to_base_units
from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import SpendingPolicy
from app.db.session import get_db

router = APIRouter(prefix="/spending-policies", tags=["spending-policies"], dependencies=[Depends(require_session)])

_PERIODS = ("day", "week", "month")
_TIME_RE = re.compile(r"^[0-2][0-9]:[0-5][0-9]$")


class PolicyBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    wallet_id: int | None = None
    principal_id: str | None = Field(None, max_length=160)
    network: str | None = None
    token: str | None = None
    counterparty_id: int | None = None
    destination_address: str | None = None
    max_amount: str | None = None       # user-facing decimal amount
    decimals: int = 6                   # precision max_amount/period_limit are expressed at
    period: str | None = None
    period_limit: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    timezone: str = "UTC"
    require_dual_control: bool = False
    active: bool = True


def _row(row: SpendingPolicy) -> dict:
    return {
        "id": row.id, "name": row.name, "wallet_id": row.wallet_id, "principal_id": row.principal_id,
        "network": row.network,
        "token": row.token, "counterparty_id": row.counterparty_id, "destination_address": row.destination_address,
        "max_amount_raw": row.max_amount_raw, "period": row.period, "period_limit_raw": row.period_limit_raw,
        "window_start": row.window_start, "window_end": row.window_end, "timezone": row.timezone,
        "require_dual_control": row.require_dual_control, "active": row.active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _validate(body: PolicyBody) -> None:
    if body.period is not None and body.period not in _PERIODS:
        raise HTTPException(400, f"period must be one of {_PERIODS}")
    if (body.window_start is None) != (body.window_end is None):
        raise HTTPException(400, "window_start and window_end must be set together")
    for value in (body.window_start, body.window_end):
        if value is not None and not _TIME_RE.match(value):
            raise HTTPException(400, "window_start/window_end must be HH:MM")
    if body.period and body.period_limit is None:
        raise HTTPException(400, "period_limit is required when period is set")
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, "timezone must be a valid IANA timezone, such as Asia/Kolkata")


@router.get("")
def list_policies(active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(SpendingPolicy)
    if active is not None:
        query = query.filter(SpendingPolicy.active == active)
    return {"policies": [_row(r) for r in query.order_by(SpendingPolicy.id).all()]}


@router.post("")
def create_policy(body: PolicyBody, db: Session = Depends(get_db)):
    _validate(body)
    row = SpendingPolicy(
        name=body.name.strip(), wallet_id=body.wallet_id, principal_id=body.principal_id, network=body.network,
        token=(body.token.upper() if body.token else None), counterparty_id=body.counterparty_id,
        destination_address=body.destination_address,
        max_amount_raw=str(to_base_units(body.max_amount, body.decimals, "policy")) if body.max_amount else None,
        period=body.period,
        period_limit_raw=str(to_base_units(body.period_limit, body.decimals, "policy")) if body.period_limit else None,
        window_start=body.window_start, window_end=body.window_end, timezone=body.timezone,
        require_dual_control=body.require_dual_control, active=body.active,
    )
    db.add(row)
    db.flush()
    append_audit(db, "spending_policy.created", "spending_policy", resource_id=str(row.id), details={"name": row.name})
    db.commit()
    return _row(row)


@router.patch("/{policy_id}")
def update_policy(policy_id: int, body: PolicyBody, db: Session = Depends(get_db)):
    row = db.query(SpendingPolicy).filter(SpendingPolicy.id == policy_id).first()
    if not row:
        raise HTTPException(404, "Policy not found")
    _validate(body)
    row.name = body.name.strip()
    row.wallet_id = body.wallet_id
    row.principal_id = body.principal_id
    row.network = body.network
    row.token = body.token.upper() if body.token else None
    row.counterparty_id = body.counterparty_id
    row.destination_address = body.destination_address
    row.max_amount_raw = str(to_base_units(body.max_amount, body.decimals, "policy")) if body.max_amount else None
    row.period = body.period
    row.period_limit_raw = str(to_base_units(body.period_limit, body.decimals, "policy")) if body.period_limit else None
    row.window_start = body.window_start
    row.window_end = body.window_end
    row.timezone = body.timezone
    row.require_dual_control = body.require_dual_control
    row.active = body.active
    append_audit(db, "spending_policy.updated", "spending_policy", resource_id=str(row.id), details={"name": row.name})
    db.commit()
    return _row(row)


@router.delete("/{policy_id}")
def delete_policy(policy_id: int, db: Session = Depends(get_db)):
    row = db.query(SpendingPolicy).filter(SpendingPolicy.id == policy_id).first()
    if not row:
        raise HTTPException(404, "Policy not found")
    db.delete(row)
    append_audit(db, "spending_policy.deleted", "spending_policy", resource_id=str(policy_id), details={})
    db.commit()
    return {"deleted": policy_id}
