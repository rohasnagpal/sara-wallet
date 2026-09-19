"""Payroll: employee/contractor payment profiles (people) and payroll runs.

A payroll run is a PaymentBatch(kind="payroll") — it goes through the exact
same approve/policy/execute pipeline as a manual batch payment
(CLAUDE_STAGES_3_TO_7.md: "Produce payroll runs as reviewable batches" /
"uses the same approval, policy and finality pipeline as other payments").

Each person's recurring salary instruction is a Schedule(kind=
"payroll_salary"), but — unlike a plain recurring_payment schedule —
payroll schedules are excluded from the generic background materializer
(app.services.schedules.materialize_due_schedules) so a person is only ever
paid through an explicit payroll run here, never twice.
"""
from datetime import datetime

from dateutil.rrule import rrulestr
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.amounts import to_base_units
from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import AddressBook, PaymentBatch, PaymentBatchItem, PayrollProfile, Schedule, ScheduleRun, Wallet
from app.db.session import get_db
from app.routers.schedules import _resolve_decimals
from app.services.schedules import _lock_fiat_amount, _next_after, _resolve_recipient

router = APIRouter(prefix="/payroll", tags=["payroll"], dependencies=[Depends(require_session)])


def _profile_row(db: Session, profile: PayrollProfile) -> dict:
    entry = db.query(AddressBook).filter(AddressBook.id == profile.counterparty_id).first()
    schedule = db.query(Schedule).filter(Schedule.id == profile.schedule_id).first() if profile.schedule_id else None
    return {
        "id": profile.id, "counterparty_id": profile.counterparty_id,
        "display_name": (entry.display_name or entry.nickname) if entry else None,
        "type": entry.type if entry else None,
        "schedule_id": profile.schedule_id,
        "amount_raw": schedule.amount_raw if schedule else None,
        "fiat_amount": schedule.fiat_amount if schedule else None,
        "fiat_currency": schedule.fiat_currency if schedule else None,
        "network": schedule.network if schedule else None,
        "token": schedule.token if schedule else None,
        "next_run_at": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None,
        "active": profile.active,
    }


class PayrollPersonBody(BaseModel):
    counterparty_id: int
    wallet_id: int
    network: str
    token: str
    amount: str | None = None
    fiat_amount: str | None = None
    fiat_currency: str | None = Field(None, max_length=10)
    rrule: str = Field(..., max_length=500)
    timezone: str = "UTC"
    start_date: datetime


@router.post("/people")
def create_payroll_person(body: PayrollPersonBody, db: Session = Depends(get_db)):
    entry = db.query(AddressBook).filter(AddressBook.id == body.counterparty_id).first()
    if not entry:
        raise HTTPException(404, "Directory entry not found")
    if entry.type not in ("employee", "contractor"):
        raise HTTPException(400, "Directory entry must be type employee or contractor")
    if db.query(PayrollProfile).filter(
        PayrollProfile.counterparty_id == body.counterparty_id, PayrollProfile.active == True  # noqa: E712
    ).first():
        raise HTTPException(400, "This counterparty already has an active payroll profile")
    if not db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first():
        raise HTTPException(404, "EVM wallet not found")
    if bool(body.amount) == bool(body.fiat_amount):
        raise HTTPException(400, "Provide exactly one of amount (fixed crypto) or fiat_amount (fiat-locked)")
    if body.fiat_amount and not body.fiat_currency:
        raise HTTPException(400, "fiat_currency is required when fiat_amount is set")
    try:
        rrulestr(body.rrule, dtstart=body.start_date)
    except Exception as exc:
        raise HTTPException(400, f"Invalid rrule: {exc}")

    amount_raw = None
    if body.amount:
        decimals = _resolve_decimals(body.network, body.token)
        try:
            amount_raw = str(to_base_units(body.amount, decimals, body.token))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    schedule = Schedule(
        kind="payroll_salary", wallet_id=body.wallet_id, network=body.network.lower(), token=body.token.upper(),
        counterparty_id=body.counterparty_id, amount_raw=amount_raw, fiat_amount=body.fiat_amount,
        fiat_currency=body.fiat_currency, rrule=body.rrule, timezone=body.timezone,
        start_date=body.start_date, next_run_at=body.start_date, active=True,
    )
    db.add(schedule)
    db.flush()
    profile = PayrollProfile(counterparty_id=body.counterparty_id, schedule_id=schedule.id, active=True)
    db.add(profile)
    db.flush()
    append_audit(db, "payroll.person_added", "payroll_profile", resource_id=str(profile.id),
                 details={"counterparty_id": body.counterparty_id})
    db.commit()
    return _profile_row(db, profile)


@router.get("/people")
def list_payroll_people(active: bool | None = None, db: Session = Depends(get_db)):
    query = db.query(PayrollProfile)
    if active is not None:
        query = query.filter(PayrollProfile.active == active)
    return {"people": [_profile_row(db, p) for p in query.order_by(PayrollProfile.id).all()]}


@router.delete("/people/{profile_id}")
def deactivate_payroll_person(profile_id: int, db: Session = Depends(get_db)):
    profile = db.query(PayrollProfile).filter(PayrollProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Payroll profile not found")
    profile.active = False
    if profile.schedule_id:
        schedule = db.query(Schedule).filter(Schedule.id == profile.schedule_id).first()
        if schedule:
            schedule.active = False
    append_audit(db, "payroll.person_deactivated", "payroll_profile", resource_id=str(profile.id), details={})
    db.commit()
    return {"id": profile.id, "active": False}


class PayrollRunBody(BaseModel):
    wallet_id: int
    network: str
    token: str
    period_label: str = Field(..., max_length=40)
    profile_ids: list[int] | None = None  # None = every active profile matching wallet/network/token


@router.post("/runs")
def create_payroll_run(body: PayrollRunBody, db: Session = Depends(get_db)):
    """Groups every due, active payroll profile for one period into a single
    reviewable batch. Each person is guarded against being paid twice for the
    same period_label by a unique ScheduleRun occurrence key, exactly like a
    plain recurring schedule's restart-safety guarantee."""
    if not db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first():
        raise HTTPException(404, "EVM wallet not found")

    query = db.query(PayrollProfile).filter(PayrollProfile.active == True)  # noqa: E712
    if body.profile_ids is not None:
        query = query.filter(PayrollProfile.id.in_(body.profile_ids))
    profiles = query.all()
    if not profiles:
        raise HTTPException(400, "No matching active payroll profiles")

    batch = PaymentBatch(
        kind="payroll", status="draft", wallet_id=body.wallet_id, network=body.network.lower(),
        token=body.token.upper(), payroll_period=body.period_label, created_by="local-owner",
    )
    db.add(batch)
    db.flush()

    row_index = 0
    skipped: list[dict] = []
    now = datetime.utcnow()
    for profile in profiles:
        schedule = db.query(Schedule).filter(Schedule.id == profile.schedule_id).first()
        if not schedule or schedule.network != batch.network or schedule.token != batch.token:
            skipped.append({"profile_id": profile.id, "reason": "schedule network/token does not match this run"})
            continue
        occurrence_key = f"payroll:{schedule.id}:{body.period_label}"
        if db.query(ScheduleRun).filter_by(schedule_id=schedule.id, occurrence_key=occurrence_key).first():
            skipped.append({"profile_id": profile.id, "reason": f"already paid for period {body.period_label}"})
            continue
        try:
            if schedule.fiat_amount:
                amount_raw, decimals = _lock_fiat_amount(schedule)
            else:
                amount_raw, decimals = int(schedule.amount_raw), _resolve_decimals(schedule.network, schedule.token)
            recipient = _resolve_recipient(db, schedule)
            if not recipient:
                raise ValueError("no resolvable recipient address")
        except Exception as exc:
            skipped.append({"profile_id": profile.id, "reason": str(exc)})
            continue

        run = ScheduleRun(schedule_id=schedule.id, occurrence_key=occurrence_key, occurrence_date=now,
                           status="materialized", batch_id=batch.id)
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            skipped.append({"profile_id": profile.id, "reason": f"already paid for period {body.period_label}"})
            continue

        db.add(PaymentBatchItem(
            batch_id=batch.id, row_index=row_index, recipient_address=recipient,
            counterparty_id=schedule.counterparty_id, amount_raw=str(amount_raw), decimals=decimals,
            reference=f"payroll:{body.period_label}",
        ))
        row_index += 1
        nxt = _next_after(schedule, now)
        schedule.next_run_at = nxt or schedule.next_run_at
        db.commit()

    append_audit(db, "payroll.run_created", "payment_batch", resource_id=str(batch.id),
                 details={"period_label": body.period_label, "items": row_index, "skipped": len(skipped)})
    db.commit()
    return {"batch_id": batch.id, "items": row_index, "skipped": skipped}


@router.get("/runs")
def list_payroll_runs(db: Session = Depends(get_db)):
    from app.routers.payment_batches import _batch_row
    rows = db.query(PaymentBatch).filter(PaymentBatch.kind == "payroll").order_by(PaymentBatch.id.desc()).all()
    return {"runs": [_batch_row(db, r) for r in rows]}
