import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from decimal import Decimal

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
    decimals: int | None = None         # normally derived from the token (USDC 6, ETH/POL 18)
    period: str | None = None
    period_limit: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    timezone: str = "UTC"
    active: bool = True


def _token_decimals(token: str | None) -> int | None:
    """Amount precision for the tokens a policy can put a limit on, or None."""
    from app.core.assets import NETWORKS
    symbol = (token or "").upper()
    if symbol == "USDC":
        return 6
    if symbol in {n["native"] for n in NETWORKS.values()}:
        return 18
    return None


def _decimals(body: PolicyBody) -> int:
    if body.decimals is not None:
        return body.decimals
    decimals = _token_decimals(body.token)
    if decimals is None:
        raise HTTPException(400, "Amount limits need a token: choose USDC, ETH or POL")
    return decimals


def _display(raw: str | None, token: str | None) -> str | None:
    """Human-readable amount ("1000") for a stored base-unit limit, when the
    token (and so the precision) is known."""
    decimals = _token_decimals(token)
    if raw is None or decimals is None:
        return None
    return format((Decimal(raw) / (Decimal(10) ** decimals)).normalize(), "f")


def _row(row: SpendingPolicy) -> dict:
    return {
        "max_amount": _display(row.max_amount_raw, row.token),
        "period_limit": _display(row.period_limit_raw, row.token),
        "id": row.id, "name": row.name, "wallet_id": row.wallet_id, "principal_id": row.principal_id,
        "network": row.network,
        "token": row.token, "counterparty_id": row.counterparty_id, "destination_address": row.destination_address,
        "max_amount_raw": row.max_amount_raw, "period": row.period, "period_limit_raw": row.period_limit_raw,
        "window_start": row.window_start, "window_end": row.window_end, "timezone": row.timezone,
        "active": row.active,
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
    if body.period and not body.period_limit:
        raise HTTPException(400, "Enter a cumulative cap for the period you chose")
    if body.period_limit and not body.period:
        raise HTTPException(400, "Choose a cap period (day, week or month) for the cumulative cap")
    if body.max_amount or body.period_limit:
        _decimals(body)
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
        max_amount_raw=str(to_base_units(body.max_amount, _decimals(body), "policy")) if body.max_amount else None,
        period=body.period,
        period_limit_raw=str(to_base_units(body.period_limit, _decimals(body), "policy")) if body.period_limit else None,
        window_start=body.window_start, window_end=body.window_end, timezone=body.timezone,
        active=body.active,
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
    row.max_amount_raw = str(to_base_units(body.max_amount, _decimals(body), "policy")) if body.max_amount else None
    row.period = body.period
    row.period_limit_raw = str(to_base_units(body.period_limit, _decimals(body), "policy")) if body.period_limit else None
    row.window_start = body.window_start
    row.window_end = body.window_end
    row.timezone = body.timezone
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
