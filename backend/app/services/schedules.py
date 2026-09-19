"""Recurring payment and payroll-salary schedule materialization.

Schedules never sign anything themselves — "scheduling is not blanket
signing authority" (CLAUDE_STAGES_3_TO_7.md Stage 3.4). This module only
turns a due occurrence into a reviewable PaymentBatch; approval and
execution still go through app.services.batch_engine like any manually
created batch, requiring an unlocked wallet and (where a policy demands it)
a second actor.
"""
from __future__ import annotations

from datetime import datetime

from dateutil.rrule import rrulestr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.events import publish
from app.db.models import AddressBook, PaymentBatch, PaymentBatchItem, Schedule, ScheduleRun

# A run of missed periods (the app was off) must not fan out into one send
# per missed period — only the single most-recent due occurrence is
# materialized; earlier ones are recorded as skipped for audit, per the
# doc's "must not cause duplicate catch-up sends" requirement.
_MAX_OCCURRENCES_PER_CYCLE = 500


def _occurrence_key(schedule_id: int, occurrence: datetime) -> str:
    return f"{schedule_id}:{occurrence.isoformat()}"


def _rule_for(schedule: Schedule):
    return rrulestr(schedule.rrule, dtstart=schedule.start_date)


def _due_occurrences(schedule: Schedule, now: datetime) -> list[datetime]:
    occurrences = _rule_for(schedule).between(schedule.next_run_at, now, inc=True)
    if schedule.end_date:
        occurrences = [o for o in occurrences if o <= schedule.end_date]
    return occurrences[:_MAX_OCCURRENCES_PER_CYCLE]


def _next_after(schedule: Schedule, after: datetime) -> datetime | None:
    nxt = _rule_for(schedule).after(after, inc=False)
    if schedule.end_date and nxt and nxt > schedule.end_date:
        return None
    return nxt


def _lock_fiat_amount(schedule: Schedule) -> tuple[int, int]:
    """Fiat-denominated payroll locks a crypto amount at materialization
    time; Stage 3 never re-prices a locked amount later. Fails closed
    (raises) rather than materializing a run with a guessed amount if a
    price can't be sourced."""
    from app.core.amounts import to_base_units
    from app.core.assets import NETWORKS
    from app.tools.market.coingecko import get_price
    from app.tools.market.paraswap import resolve_token

    native = NETWORKS.get(schedule.network, {}).get("native")
    if schedule.token.upper() == native:
        decimals = 18
    else:
        resolved = resolve_token(schedule.token, schedule.network)
        if not resolved:
            raise ValueError(f"{schedule.token} could not be resolved on {schedule.network}")
        decimals = resolved[1]
    quote = get_price(schedule.token, schedule.fiat_currency or "usd")
    if not quote or not quote.get("price"):
        raise ValueError(f"no current price available for {schedule.token}/{schedule.fiat_currency or 'usd'}")
    crypto_amount = float(schedule.fiat_amount) / quote["price"]
    return to_base_units(crypto_amount, decimals, schedule.token), decimals


def _resolve_recipient(db: Session, schedule: Schedule) -> str | None:
    if schedule.recipient_address:
        return schedule.recipient_address
    if not schedule.counterparty_id:
        return None
    # counterparty_id now references AddressBook (the unified directory) —
    # see AddressBook's docstring. AddressBook holds one address (Sara only
    # supports EVM, so it's valid on any of the EVM networks Sara supports
    # regardless of which one this schedule pays on), unlike the old
    # Counterparty.addresses per-network dict this replaced.
    entry = db.query(AddressBook).filter(AddressBook.id == schedule.counterparty_id).first()
    return entry.address if entry else None


def _materialize_one(db: Session, schedule: Schedule, occurrence: datetime) -> PaymentBatch:
    if schedule.fiat_amount:
        amount_raw, decimals = _lock_fiat_amount(schedule)
    else:
        from app.core.assets import NETWORKS
        from app.tools.market.paraswap import resolve_token

        native = NETWORKS.get(schedule.network, {}).get("native")
        decimals = 18 if schedule.token.upper() == native else (resolve_token(schedule.token, schedule.network) or (None, 6))[1]
        amount_raw = int(schedule.amount_raw)

    recipient = _resolve_recipient(db, schedule)
    if not recipient:
        raise ValueError("schedule has no resolvable recipient address")

    kind = "payroll" if schedule.kind == "payroll_salary" else "recurring"
    batch = PaymentBatch(
        kind=kind, status="draft", wallet_id=schedule.wallet_id, network=schedule.network,
        token=schedule.token, memo=schedule.memo, execution_date=occurrence,
        payroll_period=occurrence.strftime("%Y-%m-%d") if kind == "payroll" else None,
        schedule_id=schedule.id, created_by="schedule",
    )
    db.add(batch)
    db.flush()
    db.add(PaymentBatchItem(
        batch_id=batch.id, row_index=0, recipient_address=recipient,
        counterparty_id=schedule.counterparty_id, amount_raw=str(amount_raw), decimals=decimals,
        reference=f"schedule:{schedule.id}:{occurrence.date().isoformat()}",
    ))
    return batch


def materialize_due_schedules(db: Session, now: datetime | None = None) -> int:
    """Materializes due recurring_payment schedules only. payroll_salary
    schedules are intentionally excluded — they're only ever paid through an
    explicit payroll run (app.routers.payroll.create_payroll_run), so a
    person can't be paid both by this background sweep and by a run for the
    same period."""
    now = now or datetime.utcnow()
    created = 0
    for schedule in db.query(Schedule).filter(
        Schedule.active == True, Schedule.kind == "recurring_payment"  # noqa: E712
    ).all():
        occurrences = _due_occurrences(schedule, now)
        if not occurrences:
            continue
        latest = occurrences[-1]

        for occurrence in occurrences[:-1]:
            key = _occurrence_key(schedule.id, occurrence)
            if db.query(ScheduleRun).filter_by(schedule_id=schedule.id, occurrence_key=key).first():
                continue
            db.add(ScheduleRun(
                schedule_id=schedule.id, occurrence_key=key, occurrence_date=occurrence, status="skipped",
                skip_reason="missed occurrence; only the latest due occurrence is materialized",
            ))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()

        key = _occurrence_key(schedule.id, latest)
        run = ScheduleRun(schedule_id=schedule.id, occurrence_key=key, occurrence_date=latest, status="materialized")
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue  # another cycle/worker already materialized this exact occurrence

        try:
            batch = _materialize_one(db, schedule, latest)
        except Exception as exc:
            run.status = "skipped"
            run.skip_reason = f"materialization failed: {exc}"
            db.commit()
            continue

        run.batch_id = batch.id
        nxt = _next_after(schedule, latest)
        if nxt is None:
            schedule.active = False
            schedule.next_run_at = schedule.end_date or latest
        else:
            schedule.next_run_at = nxt
        publish(
            db, "schedule.materialized",
            {"schedule_id": schedule.id, "batch_id": batch.id, "occurrence": latest.isoformat()},
            aggregate_type="schedule", aggregate_id=str(schedule.id),
            event_key=f"schedule:{schedule.id}:materialized:{key}",
        )
        append_audit(db, "schedule.materialized", "schedule", resource_id=str(schedule.id),
                     details={"batch_id": batch.id, "occurrence": latest.isoformat()},
                     actor_type="system", actor_id="schedules")
        db.commit()
        created += 1
    return created
