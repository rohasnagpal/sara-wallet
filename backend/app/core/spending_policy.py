"""Spending-policy evaluation for business payment operations.

Scope: governs money movement that goes through the batch engine
(app.services.batch_engine) — manual batch payments, airdrops, recurring
schedules and payroll runs. Ad-hoc chat/safety sends are not in scope for
Stage 3; extending policy enforcement to those flows is future work.

Policies are evaluated twice per item, per CLAUDE_STAGES_3_TO_7.md: once
during batch preparation (app.services.batch_engine.validate_batch) and
again immediately before signing (app.services.batch_engine.execute_batch),
since balances/other policies/time-of-day can all change between the two.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import PaymentBatch, PaymentBatchItem, SpendingPolicy, Transaction

_PERIOD_DELTAS = {"day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}


@dataclass
class PolicyResult:
    allowed: bool
    denial_reasons: list[str] = field(default_factory=list)
    matched_policy_ids: list[int] = field(default_factory=list)


def _matches(policy: SpendingPolicy, *, wallet_id: int, network: str, token: str,
             counterparty_id: int | None, destination_address: str, principal_id: str) -> bool:
    if policy.wallet_id is not None and policy.wallet_id != wallet_id:
        return False
    if policy.principal_id and policy.principal_id != principal_id:
        return False
    if policy.network and policy.network.lower() != (network or "").lower():
        return False
    if policy.token and policy.token.upper() != (token or "").upper():
        return False
    if policy.counterparty_id is not None and policy.counterparty_id != counterparty_id:
        return False
    if policy.destination_address and policy.destination_address.lower() != (destination_address or "").lower():
        return False
    return True


def _within_window(policy: SpendingPolicy, when: datetime) -> bool:
    if not policy.window_start or not policy.window_end:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = when.astimezone(ZoneInfo(policy.timezone)).strftime("%H:%M")
    if policy.window_start <= policy.window_end:
        return policy.window_start <= current <= policy.window_end
    return current >= policy.window_start or current <= policy.window_end  # window wraps midnight


def _cumulative_raw(db: Session, policy: SpendingPolicy, when: datetime, principal_id: str) -> int:
    delta = _PERIOD_DELTAS.get(policy.period)
    if delta is None:
        return 0
    since = when - delta
    if policy.counterparty_id is None:
        query = db.query(Transaction).filter(
            Transaction.direction == "outgoing", Transaction.status.in_(("submitted", "confirmed")),
            Transaction.timestamp >= since, Transaction.wallet_id == policy.wallet_id if policy.wallet_id is not None else True,
        )
        if policy.network:
            query = query.filter(Transaction.network == policy.network.lower())
        if policy.token:
            query = query.filter(Transaction.token == policy.token.upper())
        if policy.destination_address:
            query = query.filter(Transaction.to_address == policy.destination_address)
        return sum(int(row.amount_raw or 0) for row in query.all())

    rows = (
        db.query(PaymentBatchItem, PaymentBatch)
        .join(PaymentBatch, PaymentBatch.id == PaymentBatchItem.batch_id)
        .filter(PaymentBatchItem.status.in_(("submitted", "confirmed")))
        .filter(PaymentBatchItem.updated_at >= since)
        .all()
    )
    total = 0
    for item, batch in rows:
        if policy.principal_id and batch.created_by != principal_id:
            continue
        if policy.wallet_id is not None and policy.wallet_id != batch.wallet_id:
            continue
        if policy.network and policy.network.lower() != (batch.network or "").lower():
            continue
        if policy.token and policy.token.upper() != (batch.token or "").upper():
            continue
        if policy.counterparty_id is not None and policy.counterparty_id != item.counterparty_id:
            continue
        if policy.destination_address and policy.destination_address.lower() != (item.recipient_address or "").lower():
            continue
        try:
            total += int(item.amount_raw)
        except (TypeError, ValueError):
            continue
    return total


def evaluate(
    db: Session, *, wallet_id: int, network: str, token: str, counterparty_id: int | None,
    destination_address: str, amount_raw: int, when: datetime | None = None,
    principal_id: str = "local-owner",
) -> PolicyResult:
    when = when or datetime.now(timezone.utc)
    policies = db.query(SpendingPolicy).filter(SpendingPolicy.active == True).all()  # noqa: E712
    matched = [
        p for p in policies
        if _matches(p, wallet_id=wallet_id, network=network, token=token,
                    counterparty_id=counterparty_id, destination_address=destination_address,
                    principal_id=principal_id)
    ]
    reasons: list[str] = []
    for policy in matched:
        if not _within_window(policy, when):
            reasons.append(
                f"policy '{policy.name}' only permits sends between {policy.window_start} and {policy.window_end}"
            )
            continue
        if policy.max_amount_raw is not None and amount_raw > int(policy.max_amount_raw):
            reasons.append(f"policy '{policy.name}' caps a single payment at {policy.max_amount_raw} base units")
        if policy.period and policy.period_limit_raw is not None:
            cumulative = _cumulative_raw(db, policy, when, principal_id)
            if cumulative + amount_raw > int(policy.period_limit_raw):
                reasons.append(
                    f"policy '{policy.name}' caps cumulative spend per {policy.period} at "
                    f"{policy.period_limit_raw} base units ({cumulative} already used in this window)"
                )
    return PolicyResult(
        allowed=not reasons, denial_reasons=reasons, matched_policy_ids=[p.id for p in matched],
    )
