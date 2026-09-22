import io
import json
from datetime import datetime
from decimal import Decimal

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from web3 import Web3

from app.core.amounts import to_base_units
from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import PaymentBatch, PaymentBatchItem, Wallet
from app.db.session import get_db
from app.services import batch_engine

router = APIRouter(prefix="/payment-batches", tags=["payment-batches"], dependencies=[Depends(require_session)])

_KINDS = ("payment", "airdrop", "payroll", "recurring")
_CSV_MAX_BYTES = 2 * 1024 * 1024
_CSV_MAX_ROWS = 5000
_CSV_REQUIRED_COLUMNS = {"recipient_address", "amount"}
_LOCAL_OWNER = "local-owner"


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = [t.strip() for t in tags if t.strip()]
    if len(cleaned) > 20 or any(len(t) > 40 for t in cleaned):
        raise HTTPException(400, "Use at most 20 tags of 40 characters each")
    return list(dict.fromkeys(cleaned))


def _batch_or_404(db: Session, batch_id: int) -> PaymentBatch:
    row = db.query(PaymentBatch).filter(PaymentBatch.id == batch_id).first()
    if not row:
        raise HTTPException(404, "Payment batch not found")
    return row


def _resolve_decimals(batch: PaymentBatch) -> int:
    from app.core.assets import NETWORKS
    from app.tools.market.paraswap import resolve_token
    native = NETWORKS.get(batch.network, {}).get("native")
    if batch.token.upper() == native:
        return 18
    resolved = resolve_token(batch.token, batch.network)
    if not resolved:
        raise HTTPException(400, f"{batch.token} could not be resolved on {batch.network}")
    return resolved[1]


def _item_row(item: PaymentBatchItem) -> dict:
    amount = format(Decimal(item.amount_raw) / (Decimal(10) ** item.decimals), "f")
    return {
        "id": item.id, "row_index": item.row_index, "recipient_address": item.recipient_address,
        "counterparty_id": item.counterparty_id, "amount": amount, "amount_raw": item.amount_raw,
        "decimals": item.decimals, "reference": item.reference, "note": item.note,
        "tags": json.loads(item.tags) if item.tags else [], "status": item.status,
        "tx_hash": item.tx_hash, "transaction_id": item.transaction_id, "failure_reason": item.failure_reason,
    }


def _batch_row(db: Session, batch: PaymentBatch, *, with_items: bool = False) -> dict:
    data = {
        "id": batch.id, "kind": batch.kind, "status": batch.status, "wallet_id": batch.wallet_id,
        "network": batch.network, "token": batch.token, "memo": batch.memo,
        "execution_date": batch.execution_date.isoformat() if batch.execution_date else None,
        "payroll_period": batch.payroll_period, "schedule_id": batch.schedule_id,
        "created_by": batch.created_by, "approved_by": batch.approved_by,
        "approved_at": batch.approved_at.isoformat() if batch.approved_at else None,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }
    items = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).order_by(PaymentBatchItem.row_index).all()
    total_raw = sum((int(i.amount_raw) for i in items), 0)
    decimals = items[0].decimals if items else 0
    data["item_count"] = len(items)
    data["total_amount"] = format(Decimal(total_raw) / (Decimal(10) ** decimals), "f") if items else "0"
    if with_items:
        data["items"] = [_item_row(i) for i in items]
    return data


class CreateBatchBody(BaseModel):
    kind: str = "payment"
    wallet_id: int
    network: str
    token: str
    memo: str | None = Field(None, max_length=500)
    execution_date: datetime | None = None


@router.post("")
def create_batch(body: CreateBatchBody, db: Session = Depends(get_db)):
    if body.kind not in _KINDS:
        raise HTTPException(400, f"kind must be one of {_KINDS}")
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    from app.core.assets import network_enabled
    if not network_enabled(body.network):
        raise HTTPException(400, f"network '{body.network}' is disabled")
    batch = PaymentBatch(
        kind=body.kind, status="draft", wallet_id=body.wallet_id, network=body.network.lower(),
        token=body.token.upper(), memo=body.memo, execution_date=body.execution_date,
        created_by=_LOCAL_OWNER,
    )
    db.add(batch)
    db.flush()
    append_audit(db, "payment_batch.created", "payment_batch", resource_id=str(batch.id),
                 details={"kind": batch.kind, "wallet_id": batch.wallet_id, "network": batch.network, "token": batch.token},
                 actor_id=batch.created_by)
    db.commit()
    return _batch_row(db, batch)


@router.get("")
def list_batches(status: str | None = None, kind: str | None = None, db: Session = Depends(get_db)):
    query = db.query(PaymentBatch)
    if status is not None:
        query = query.filter(PaymentBatch.status == status)
    if kind is not None:
        # Comma-separated so a tab can ask for just its own kinds
        # (Batches: "payment,airdrop"; Schedules: "recurring").
        query = query.filter(PaymentBatch.kind.in_([k.strip() for k in kind.split(",") if k.strip()]))
    rows = query.order_by(PaymentBatch.id.desc()).all()
    return {"batches": [_batch_row(db, b) for b in rows]}


@router.get("/{batch_id}")
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = _batch_or_404(db, batch_id)
    return _batch_row(db, batch, with_items=True)


class ItemBody(BaseModel):
    recipient_address: str
    amount: str
    counterparty_id: int | None = None
    reference: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)
    tags: list[str] = Field(default_factory=list)


@router.post("/{batch_id}/items")
def add_item(batch_id: int, body: ItemBody, db: Session = Depends(get_db)):
    from app.tools.names.resolver import resolve_recipient_input

    batch = _batch_or_404(db, batch_id)
    resolved = resolve_recipient_input(db, body.recipient_address, batch.network)
    if not resolved:
        raise HTTPException(400, "Invalid recipient address, and no matching address book entry or Sara Name found")
    decimals = _resolve_decimals(batch)
    try:
        amount_raw = to_base_units(body.amount, decimals, batch.token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    existing = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
    next_row_index = (max((i.row_index for i in existing), default=-1)) + 1
    item = PaymentBatchItem(
        batch_id=batch.id, row_index=next_row_index, recipient_address=resolved.address,
        counterparty_id=body.counterparty_id, amount_raw=str(amount_raw), decimals=decimals,
        reference=body.reference, note=body.note, tags=json.dumps(_clean_tags(body.tags)), status="draft",
    )
    db.add(item)
    if batch.status in ("awaiting_approval", "approved"):
        batch_engine.invalidate_approval(db, batch, "item added after approval")
    append_audit(db, "payment_batch_item.added", "payment_batch_item", resource_id=str(batch.id),
                 details={"recipient_address": resolved.address, "resolved_from": resolved.source,
                          "input_label": resolved.input_label, "amount_raw": str(amount_raw)})
    db.commit()
    row = _item_row(item)
    row["resolved_via"] = resolved.source
    row["resolved_from_input"] = resolved.input_label
    return row


@router.patch("/{batch_id}/items/{item_id}")
def update_item(batch_id: int, item_id: int, body: ItemBody, db: Session = Depends(get_db)):
    from app.tools.names.resolver import resolve_recipient_input

    batch = _batch_or_404(db, batch_id)
    item = db.query(PaymentBatchItem).filter(PaymentBatchItem.id == item_id, PaymentBatchItem.batch_id == batch.id).first()
    if not item:
        raise HTTPException(404, "Batch item not found")
    resolved = resolve_recipient_input(db, body.recipient_address, batch.network)
    if not resolved:
        raise HTTPException(400, "Invalid recipient address, and no matching address book entry or Sara Name found")
    decimals = _resolve_decimals(batch)
    try:
        amount_raw = to_base_units(body.amount, decimals, batch.token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    item.recipient_address = resolved.address
    item.counterparty_id = body.counterparty_id
    item.amount_raw = str(amount_raw)
    item.decimals = decimals
    item.reference = body.reference
    item.note = body.note
    item.tags = json.dumps(_clean_tags(body.tags))
    item.status = "draft"
    item.failure_reason = None
    if batch.status in ("awaiting_approval", "approved"):
        batch_engine.invalidate_approval(db, batch, f"item {item_id} edited after approval")
    append_audit(db, "payment_batch_item.updated", "payment_batch_item", resource_id=str(item.id), details={})
    db.commit()
    return _item_row(item)


@router.delete("/{batch_id}/items/{item_id}")
def delete_item(batch_id: int, item_id: int, db: Session = Depends(get_db)):
    batch = _batch_or_404(db, batch_id)
    item = db.query(PaymentBatchItem).filter(PaymentBatchItem.id == item_id, PaymentBatchItem.batch_id == batch.id).first()
    if not item:
        raise HTTPException(404, "Batch item not found")
    if item.status in ("submitted", "confirmed"):
        raise HTTPException(400, "Cannot delete an item that has already been broadcast")
    db.delete(item)
    if batch.status in ("awaiting_approval", "approved"):
        batch_engine.invalidate_approval(db, batch, f"item {item_id} removed after approval")
    append_audit(db, "payment_batch_item.deleted", "payment_batch_item", resource_id=str(item_id), details={})
    db.commit()
    return {"deleted": item_id}


def _read_csv(raw: bytes) -> "pd.DataFrame":
    """File-level checks: size, encoding, parseable, row cap, required
    columns. Anything wrong here is a 400 — there's nothing to show a
    per-row preview for."""
    if len(raw) > _CSV_MAX_BYTES:
        raise HTTPException(400, f"CSV file exceeds the {_CSV_MAX_BYTES // (1024 * 1024)}MB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "CSV must be UTF-8 encoded")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, engine="python")
    except Exception as exc:
        raise HTTPException(400, f"Could not parse CSV: {exc}")
    if len(df) > _CSV_MAX_ROWS:
        raise HTTPException(400, f"CSV has {len(df)} rows; the limit is {_CSV_MAX_ROWS}")
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = _CSV_REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise HTTPException(400, f"CSV is missing required column(s): {', '.join(sorted(missing))}")
    return df


def _import_rows(db: Session, batch: PaymentBatch, df: "pd.DataFrame") -> tuple[int, list[dict]]:
    """Adds one draft item per valid row (no commit) and returns
    (imported, row_errors). Optional columns: reference, note, tags
    (comma- or semicolon-separated within the cell, e.g. "vendor;q1"). Rows
    are checked for a valid address, no duplicate recipient, an exactly
    representable amount, and the active spending policies."""
    from app.core import spending_policy

    decimals = _resolve_decimals(batch)
    existing = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
    next_row_index = (max((i.row_index for i in existing), default=-1)) + 1
    seen = {i.recipient_address.lower() for i in existing if Web3.is_address(i.recipient_address)}
    row_errors: list[dict] = []
    imported = 0
    for idx, record in df.iterrows():
        line_no = int(idx) + 2  # header is line 1
        address = str(record.get("recipient_address", "")).strip()
        amount_str = str(record.get("amount", "")).strip()
        reference = str(record.get("reference", "")).strip() or None
        note = str(record.get("note", "")).strip() or None
        raw_tags = str(record.get("tags", "")).strip()
        try:
            tags = _clean_tags([t for chunk in raw_tags.split(",") for t in chunk.split(";")]) if raw_tags else []
        except HTTPException as exc:
            row_errors.append({"row": line_no, "error": exc.detail})
            continue
        if not Web3.is_address(address):
            row_errors.append({"row": line_no, "error": "invalid recipient_address"})
            continue
        key = address.lower()
        if key in seen:
            row_errors.append({"row": line_no, "error": f"duplicate recipient_address {address}"})
            continue
        try:
            amount_raw = to_base_units(amount_str, decimals, batch.token)
        except ValueError as exc:
            row_errors.append({"row": line_no, "error": str(exc)})
            continue
        policy = spending_policy.evaluate(
            db, wallet_id=batch.wallet_id, network=batch.network, token=batch.token,
            counterparty_id=None, destination_address=address, amount_raw=amount_raw,
        )
        if not policy.allowed:
            row_errors.append({"row": line_no, "error": "blocked by spending policy: " + "; ".join(policy.denial_reasons)})
            continue
        db.add(PaymentBatchItem(
            batch_id=batch.id, row_index=next_row_index, recipient_address=address,
            amount_raw=str(amount_raw), decimals=decimals, reference=reference,
            note=note, tags=json.dumps(tags), status="draft",
        ))
        seen.add(key)
        next_row_index += 1
        imported += 1
    return imported, row_errors


@router.post("/import")
async def import_batch(
    file: UploadFile = File(...),
    kind: str = Form("payment"),
    wallet_id: int = Form(...),
    network: str = Form(...),
    token: str = Form(...),
    memo: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Upload a CSV and get back either a validated draft batch ready to
    send, or a list of everything that's wrong. All-or-nothing: if any row
    (or the batch as a whole — balance, gas, disabled token) fails, nothing
    is created, so a partial list can never be sent by accident. Required
    columns: recipient_address, amount."""
    if kind not in ("payment", "airdrop"):
        raise HTTPException(400, "kind must be 'payment' or 'airdrop'")
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    from app.core.assets import network_enabled
    if not network_enabled(network):
        raise HTTPException(400, f"network '{network}' is disabled")
    memo = (memo or "").strip() or None
    if memo and len(memo) > 500:
        raise HTTPException(400, "Memo is limited to 500 characters")
    df = _read_csv(await file.read(_CSV_MAX_BYTES + 1))

    batch = PaymentBatch(
        kind=kind, status="draft", wallet_id=wallet_id, network=network.lower(),
        token=token.strip().upper(), memo=memo, created_by=_LOCAL_OWNER,
    )
    db.add(batch)
    db.flush()
    try:
        if kind == "airdrop":
            from app.tools.market.paraswap import trusted_symbols
            if batch.token not in trusted_symbols(batch.network):
                raise HTTPException(400, f"{batch.token} is not a trusted token on {batch.network} for airdrops")
        imported, row_errors = _import_rows(db, batch, df)
        # _import_rows only db.add()s items; the session has autoflush=False
        # (app/db/session.py), so validate_batch's own query for this batch's
        # items would otherwise see none of them and always fail with
        # "batch has no items" — flush before any read of what was just added.
        db.flush()
    except Exception:
        db.rollback()
        raise
    if row_errors or not imported:
        db.rollback()
        return {"ok": False, "row_errors": row_errors, "batch_errors": [] if row_errors else ["The CSV has no rows."]}

    result = batch_engine.validate_batch(db, batch)
    if not result["ok"]:
        items = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
        rows = {i.id: i.row_index + 2 for i in items}
        errors = [{"row": rows.get(int(item_id), 0), "error": "; ".join(errs)} for item_id, errs in result["item_errors"].items()]
        db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).delete()
        db.delete(batch)
        db.commit()
        return {"ok": False, "row_errors": errors, "batch_errors": result["batch_errors"]}

    append_audit(db, "payment_batch.created", "payment_batch", resource_id=str(batch.id),
                 details={"kind": batch.kind, "wallet_id": batch.wallet_id, "network": batch.network,
                          "token": batch.token, "imported": imported, "source": "csv_upload"},
                 actor_id=batch.created_by)
    db.commit()
    return {"ok": True, "batch": _batch_row(db, batch, with_items=True)}


@router.post("/{batch_id}/validate")
def validate_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = _batch_or_404(db, batch_id)
    result = batch_engine.validate_batch(db, batch)
    return {**result, "batch": _batch_row(db, batch, with_items=True)}


class ApproveBody(BaseModel):
    reason: str | None = Field(None, max_length=500)


@router.post("/{batch_id}/approve")
def approve_batch(batch_id: int, body: ApproveBody, db: Session = Depends(get_db)):
    batch = _batch_or_404(db, batch_id)
    try:
        batch_engine.approve_batch(db, batch, _LOCAL_OWNER, reason=body.reason)
    except batch_engine.BatchValidationError as exc:
        raise HTTPException(400, {"message": str(exc), **exc.result})
    return _batch_row(db, batch, with_items=True)


class ExecuteBody(BaseModel):
    passphrase: str


@router.post("/{batch_id}/execute")
def execute_batch(batch_id: int, body: ExecuteBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    batch = _batch_or_404(db, batch_id)
    wallet = db.query(Wallet).filter(Wallet.id == batch.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    key = decrypt_key(wallet.encrypted_key)
    try:
        summary = batch_engine.execute_batch(db, batch, wallet, key, actor=_LOCAL_OWNER)
    except batch_engine.BatchExecutionError as exc:
        raise HTTPException(409, str(exc))
    finally:
        key = None
    return {**summary, "batch": _batch_row(db, batch, with_items=True)}


@router.post("/{batch_id}/send")
def send_batch(batch_id: int, body: ExecuteBody, db: Session = Depends(get_db)):
    """Approve (if still a draft) and execute in one step, after the wallet
    passphrase is confirmed. Approval re-validates the batch, and spending
    policies are evaluated again before every item is signed."""
    from app.tools.wallet.lock import confirm_passphrase

    batch = _batch_or_404(db, batch_id)
    if batch.status not in ("draft", "approved", "executing"):
        raise HTTPException(409, f"This batch is {batch.status} and can't be sent")
    if batch.status == "draft":
        if not confirm_passphrase(body.passphrase):
            raise HTTPException(401, "Incorrect passphrase")
        try:
            batch_engine.approve_batch(db, batch, _LOCAL_OWNER)
        except batch_engine.BatchValidationError as exc:
            raise HTTPException(400, {"message": str(exc), **exc.result})
    return execute_batch(batch_id, body, db)


@router.delete("/{batch_id}")
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    """Removes a batch that was never used: a CSV-upload draft or a cancelled
    one. Anything approved, executing or (partly) sent is history and stays.
    Batches made by Schedules and Payroll are cancelled, never deleted — each
    is tied to a record saying that occurrence was already generated, so
    deleting one would let it be regenerated."""
    from app.db.models import BatchApproval

    batch = _batch_or_404(db, batch_id)
    if batch.kind not in ("payment", "airdrop"):
        raise HTTPException(409, "Batches from Schedules and Payroll can be cancelled but not deleted")
    if batch.status not in ("draft", "cancelled"):
        raise HTTPException(409, f"A batch in status '{batch.status}' can't be deleted")
    items = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
    if any(i.status in ("submitted", "confirmed") or i.tx_hash for i in items):
        raise HTTPException(409, "This batch already broadcast transactions and is kept as a record")
    db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).delete()
    db.query(BatchApproval).filter(BatchApproval.batch_id == batch.id).delete()
    db.delete(batch)
    append_audit(db, "payment_batch.deleted", "payment_batch", resource_id=str(batch_id),
                 details={"kind": batch.kind, "status": batch.status, "items": len(items)})
    db.commit()
    return {"deleted": batch_id}


@router.post("/{batch_id}/cancel")
def cancel_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = _batch_or_404(db, batch_id)
    if batch.status in ("executing", "completed"):
        raise HTTPException(400, f"Cannot cancel a batch in status '{batch.status}'")
    batch.status = "cancelled"
    for item in db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id):
        if item.status not in ("submitted", "confirmed"):
            item.status = "cancelled"
    append_audit(db, "payment_batch.cancelled", "payment_batch", resource_id=str(batch.id), details={})
    db.commit()
    return _batch_row(db, batch, with_items=True)
