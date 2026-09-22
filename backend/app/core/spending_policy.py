"""Spending-policy evaluation for business payment operations.

Scope: governs the batch engine (app.services.batch_engine) — manual batch
payments, airdrops, recurring schedules and payroll runs — and chat sends,
swaps and bridges (app.routers.chat._spending_policy_denial), deployed-token
transfers and x402 payments. Sara Names fees are not yet
covered.

Policies are evaluated twice per item, per CLAUDE_STAGES_3_TO_7.md: once
during batch preparation (app.services.batch_engine.validate_batch) and
again immediately before signing (app.services.batch_engine.execute_batch),
since balances/other policies/time-of-day can all change between the two.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import AddressBook, SpendingPolicy, Transaction

_PERIOD_DELTAS = {"day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}


@dataclass
class PolicyResult:
    allowed: bool
    denial_reasons: list[str] = field(default_factory=list)
    matched_policy_ids: list[int] = field(default_factory=list)


def resolve_counterparty_id(db: Session, destination_address: str | None) -> int | None:
    """Looks up the Directory (AddressBook) entry for a raw destination
    address, if one exists — so a vendor-scoped policy applies no matter
    which payment path (chat send/swap/bridge, x402, token transfer, batch
    or CSV import) sends to that same address, without every one of those
    call sites having to resolve it themselves. A caller that already knows
    the counterparty (e.g. a batch item explicitly tagged with one) should
    keep passing that; this is only consulted when it doesn't."""
    if not destination_address:
        return None
    entry = (
        db.query(AddressBook)
        .filter(AddressBook.chain == "evm", func.lower(AddressBook.address) == destination_address.strip().lower())
        .first()
    )
    return entry.id if entry else None


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
    """Sums every outgoing, submitted/confirmed payment this policy's window
    covers. Transaction is the single ledger every payment path writes to
    exactly once it's broadcast — chat sends/swaps/bridges, batches
    (including CSV imports), payroll, token transfers, x402 and Sara Names —
    so summing it here (rather than separately re-deriving the same total
    from PaymentBatchItem for counterparty-scoped policies, as before) is
    both simpler and correctly counts spend regardless of which of those
    paths it went through. A counterparty-scoped policy is resolved to that
    Directory entry's address and matched the same way any other
    destination-address-scoped policy is."""
    delta = _PERIOD_DELTAS.get(policy.period)
    if delta is None:
        return 0
    since = when - delta
    query = db.query(Transaction).filter(
        Transaction.direction == "outgoing", Transaction.status.in_(("submitted", "confirmed")),
        Transaction.timestamp >= since,
    )
    if policy.wallet_id is not None:
        query = query.filter(Transaction.wallet_id == policy.wallet_id)
    if policy.network:
        query = query.filter(Transaction.network == policy.network.lower())
    if policy.token:
        query = query.filter(Transaction.token == policy.token.upper())
    if policy.destination_address:
        query = query.filter(func.lower(Transaction.to_address) == policy.destination_address.lower())
    rows = query.all()
    if policy.counterparty_id is not None:
        entry = db.query(AddressBook).filter(AddressBook.id == policy.counterparty_id).first()
        counterparty_address = entry.address.strip().lower() if entry else None
        rows = [r for r in rows if counterparty_address and (r.to_address or "").strip().lower() == counterparty_address]
    # Sara is single-user (principal_id always defaults to "local-owner"
    # everywhere it's threaded through); Transaction carries no notion of
    # who/what initiated it, so a policy scoped to a different principal_id
    # already never matches in _matches() and this is a no-op in practice.
    return sum(int(row.amount_raw or 0) for row in rows)


def evaluate(
    db: Session, *, wallet_id: int, network: str, token: str, counterparty_id: int | None,
    destination_address: str, amount_raw: int, when: datetime | None = None,
    principal_id: str = "local-owner",
) -> PolicyResult:
    when = when or datetime.now(timezone.utc)
    if counterparty_id is None:
        # Every current caller either doesn't know the counterparty or
        # leaves this at its default of None — without this, a policy
        # scoped to a vendor/Directory entry silently never applied outside
        # the one call site that happened to pass a real counterparty_id.
        counterparty_id = resolve_counterparty_id(db, destination_address)
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
