"""The batch payment engine: validation, maker/checker approval and
sequential on-chain execution shared by batch payments, airdrops, payroll
runs and materialized recurring obligations (CLAUDE_STAGES_3_TO_7.md
Stage 3 — "reuse the batch engine" / "produce payroll runs as reviewable
batches").

Two-person control uses server-derived principal identifiers. The HTTP layer
maps the local session to ``local-owner`` and checker API credentials to an
``approver:<id>`` principal; caller-supplied display names are never trusted.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import uuid

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app.core import spending_policy
from app.tools.risk import screening as risk_screening
from app.core.audit import append_audit
from app.core.events import publish
from app.db.models import BatchApproval, PaymentBatch, PaymentBatchItem, Wallet


class BatchValidationError(Exception):
    def __init__(self, message: str, result: dict):
        super().__init__(message)
        self.result = result


class SelfApprovalError(Exception):
    pass


class BatchExecutionError(Exception):
    pass


def _wallet_or_raise(db: Session, wallet_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not wallet:
        raise ValueError("wallet not found")
    return wallet


def validate_batch(db: Session, batch: PaymentBatch) -> dict:
    """Validates every item's address/asset/amount and duplicate rows, then
    (if the wallet resolves) the sum of all items against on-chain
    balance/gas. Never silently drops a bad row — every item ends up either
    'validated' or 'draft' with failure_reason populated, and batch-level
    problems (insufficient funds for the whole set) are reported separately
    from per-item problems."""
    from web3 import Web3
    from app.core.assets import NETWORKS, token_enabled

    items = (
        db.query(PaymentBatchItem)
        .filter(PaymentBatchItem.batch_id == batch.id)
        .order_by(PaymentBatchItem.row_index)
        .all()
    )
    batch_errors: list[str] = []
    item_errors: dict[int, list[str]] = {}
    seen_recipients: dict[str, int] = {}

    if not items:
        batch_errors.append("batch has no items")

    token_ok = token_enabled(batch.token, batch.network)
    if not token_ok:
        batch_errors.append(f"{batch.token} is disabled or unsupported on {batch.network}")

    for item in items:
        errs: list[str] = []
        addr = (item.recipient_address or "").strip()
        if not Web3.is_address(addr):
            errs.append("invalid recipient address")
        else:
            key = addr.lower()
            if key in seen_recipients:
                errs.append(f"duplicate recipient (also row {seen_recipients[key]})")
            else:
                seen_recipients[key] = item.row_index
        try:
            amount_raw = int(item.amount_raw)
            if amount_raw <= 0:
                errs.append("amount must be positive")
        except (TypeError, ValueError):
            errs.append("invalid amount")
        if errs:
            item.status = "draft"
            item.failure_reason = "; ".join(errs)
            item_errors[item.id] = errs
        else:
            item.status = "validated"
            item.failure_reason = None

    if token_ok and not item_errors and items:
        native = NETWORKS.get(batch.network, {}).get("native")
        total_raw = sum(int(i.amount_raw) for i in items)
        decimals = items[0].decimals
        try:
            wallet = _wallet_or_raise(db, batch.wallet_id)
            if batch.token.upper() == native:
                from app.chains.evm import get_native_transfer_preview_raw

                preview = get_native_transfer_preview_raw(wallet.address, total_raw, batch.network)
                if not preview["has_funds"]:
                    batch_errors.append(
                        f"insufficient {preview['unit']}: {preview['balance']:.6f} available, "
                        f"{preview['total']:.6f} required for all items plus gas"
                    )
            else:
                from app.tools.market.paraswap import resolve_token

                resolved = resolve_token(batch.token, batch.network)
                if not resolved:
                    batch_errors.append(f"{batch.token} could not be resolved on {batch.network}")
                else:
                    token_address, resolved_decimals = resolved
                    from app.chains.evm import _get_erc20_balance_raw, get_erc20_transfer_preview_raw

                    balance_raw = _get_erc20_balance_raw(token_address, wallet.address, batch.network)
                    if balance_raw < total_raw:
                        batch_errors.append(
                            f"insufficient {batch.token}: {balance_raw} base units available, "
                            f"{total_raw} required across {len(items)} item(s)"
                        )
                    sample = get_erc20_transfer_preview_raw(
                        token_address, resolved_decimals, wallet.address, int(items[0].amount_raw),
                        items[0].recipient_address, batch.network,
                    )
                    est_total_gas = sample["gas_fee"] * len(items)
                    if sample["native_balance"] < est_total_gas:
                        batch_errors.append(
                            f"insufficient {sample['native_unit']} for gas: ~{est_total_gas:.6f} "
                            f"estimated for {len(items)} item(s), {sample['native_balance']:.6f} available"
                        )
        except Exception as exc:
            batch_errors.append(f"could not verify on-chain balance/gas: {exc}")

    db.commit()
    return {"ok": not batch_errors and not item_errors, "item_errors": item_errors, "batch_errors": batch_errors}


def _payload_hash(batch: PaymentBatch, items: list[PaymentBatchItem]) -> str:
    material = {
        "wallet_id": batch.wallet_id,
        "network": batch.network,
        "token": batch.token,
        "items": [
            {"recipient_address": i.recipient_address.lower(), "amount_raw": i.amount_raw, "decimals": i.decimals}
            for i in sorted(items, key=lambda x: x.row_index)
        ],
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def approve_batch(db: Session, batch: PaymentBatch, actor: str, *, reason: str | None = None) -> BatchApproval:
    result = validate_batch(db, batch)
    if not result["ok"]:
        raise BatchValidationError("batch has unresolved validation errors", result)

    items = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
    payload_hash = _payload_hash(batch, items)

    require_dual = False
    for item in items:
        policy_result = spending_policy.evaluate(
            db, wallet_id=batch.wallet_id, network=batch.network, token=batch.token,
            counterparty_id=item.counterparty_id, destination_address=item.recipient_address,
            amount_raw=int(item.amount_raw),
        )
        if policy_result.require_dual_control:
            require_dual = True

    if require_dual and actor == batch.created_by:
        denial_reason = "self-approval is not permitted for this batch (a matching spending policy requires dual control)"
        approval = BatchApproval(batch_id=batch.id, action="denied", actor=actor,
                                  payload_hash=payload_hash, reason=denial_reason)
        db.add(approval)
        append_audit(db, "payment_batch.approval_denied", "payment_batch", resource_id=str(batch.id),
                     details={"actor": actor, "reason": denial_reason}, actor_id=actor)
        db.commit()
        raise SelfApprovalError(denial_reason)

    batch.approved_by = actor
    batch.approved_at = datetime.utcnow()
    batch.approval_payload_hash = payload_hash
    batch.status = "approved"
    for item in items:
        item.status = "approved"
    approval = BatchApproval(batch_id=batch.id, action="approved", actor=actor,
                              payload_hash=payload_hash, reason=reason)
    db.add(approval)
    publish(
        db, "payment_batch.approved", {"batch_id": batch.id, "actor": actor},
        aggregate_type="payment_batch", aggregate_id=str(batch.id),
        event_key=f"payment_batch:{batch.id}:approved:{payload_hash}",
    )
    append_audit(db, "payment_batch.approved", "payment_batch", resource_id=str(batch.id),
                 details={"actor": actor, "payload_hash": payload_hash}, actor_id=actor)
    db.commit()
    return approval


def invalidate_approval(db: Session, batch: PaymentBatch, reason: str, *, actor: str = "system") -> None:
    """Called whenever a material field changes on a batch that already has
    (or is awaiting) approval — editing an approved batch must not silently
    keep executing under the old approval."""
    if batch.status not in ("awaiting_approval", "approved"):
        return
    items = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
    payload_hash = batch.approval_payload_hash or _payload_hash(batch, items)
    approval = BatchApproval(batch_id=batch.id, action="invalidated", actor=actor,
                              payload_hash=payload_hash, reason=reason)
    db.add(approval)
    batch.approved_by = None
    batch.approved_at = None
    batch.approval_payload_hash = None
    batch.status = "draft"
    for item in items:
        if item.status in ("validated", "awaiting_approval", "approved"):
            item.status = "draft"
    publish(db, "payment_batch.approval_invalidated", {"batch_id": batch.id, "reason": reason},
            aggregate_type="payment_batch", aggregate_id=str(batch.id))
    append_audit(db, "payment_batch.approval_invalidated", "payment_batch", resource_id=str(batch.id),
                 details={"reason": reason}, actor_id=actor)
    db.commit()


def execute_batch(db: Session, batch: PaymentBatch, wallet: Wallet, private_key: str, *, actor: str = "owner") -> dict:
    """Signs and broadcasts every not-yet-submitted item sequentially as
    separate transactions (never described as one atomic on-chain
    transaction). A compare-and-swap lease on execution_lock_token rejects a
    concurrent/double-click call; per-item status makes a re-call after a
    crash resume rather than resend."""
    stale_before = datetime.utcnow() - timedelta(minutes=5)
    if batch.status not in ("approved", "executing"):
        raise BatchExecutionError("batch must be approved before execution")

    lock_token = uuid.uuid4().hex
    claimed = db.execute(
        update(PaymentBatch)
        .where(
            PaymentBatch.id == batch.id,
            or_(
                and_(PaymentBatch.status == "approved", PaymentBatch.execution_lock_token.is_(None)),
                and_(PaymentBatch.status == "executing", PaymentBatch.execution_lock_acquired_at < stale_before),
            ),
        )
        .values(execution_lock_token=lock_token, execution_lock_acquired_at=datetime.utcnow(), status="executing")
    )
    db.commit()
    if claimed.rowcount == 0:
        raise BatchExecutionError("batch is already executing (another call is in progress)")
    db.refresh(batch)

    from app.core.assets import NETWORKS
    from app.chains.evm import (
        broadcast_raw_transaction, prepare_erc20_transfer_raw, prepare_native_transfer_raw,
    )
    from app.routers.chat import _record_submitted_transaction

    native = NETWORKS.get(batch.network, {}).get("native")
    is_native = batch.token.upper() == native
    token_address = None
    if not is_native:
        from app.tools.market.paraswap import resolve_token

        resolved = resolve_token(batch.token, batch.network)
        if not resolved:
            batch.status = "failed"
            batch.execution_lock_token = None
            db.commit()
            raise BatchExecutionError(f"{batch.token} could not be resolved on {batch.network}")
        token_address, _decimals = resolved

    try:
        items = (
            db.query(PaymentBatchItem)
            .filter(PaymentBatchItem.batch_id == batch.id)
            .order_by(PaymentBatchItem.row_index)
            .all()
        )
        for item in items:
            if item.status in ("submitted", "confirmed", "cancelled"):
                continue  # already broadcast (restart recovery) or intentionally skipped

            policy_result = spending_policy.evaluate(
                db, wallet_id=batch.wallet_id, network=batch.network, token=batch.token,
                counterparty_id=item.counterparty_id, destination_address=item.recipient_address,
                amount_raw=int(item.amount_raw),
            )
            if not policy_result.allowed:
                item.status = "failed"
                item.failure_reason = "; ".join(policy_result.denial_reasons)
                append_audit(db, "payment_batch_item.policy_denied", "payment_batch_item",
                             resource_id=str(item.id), details={"reasons": policy_result.denial_reasons}, actor_id=actor)
                db.commit()
                continue

            try:
                risk_screening.enforce_mandatory_screening(db, item.recipient_address, batch.network)
            except ValueError as exc:
                item.status = "failed"
                item.failure_reason = str(exc)
                append_audit(db, "payment_batch_item.risk_screening_denied", "payment_batch_item",
                             resource_id=str(item.id), details={"reason": str(exc)}, actor_id=actor)
                db.commit()
                continue

            amount_float = float(Decimal(item.amount_raw) / (Decimal(10) ** item.decimals))
            try:
                if item.status == "broadcasting" and item.signed_tx_raw:
                    prepared = {"tx_hash": item.tx_hash, "raw_transaction": item.signed_tx_raw}
                elif is_native:
                    prepared = prepare_native_transfer_raw(
                        private_key, item.recipient_address, int(item.amount_raw), batch.network,
                    )
                else:
                    prepared = prepare_erc20_transfer_raw(
                        private_key, token_address, item.decimals, item.recipient_address,
                        int(item.amount_raw), batch.network,
                    )
                item.tx_hash = prepared["tx_hash"]
                item.signed_tx_raw = prepared["raw_transaction"]
                item.reserved_nonce = prepared.get("nonce", item.reserved_nonce)
                item.status = "broadcasting"
                db.commit()  # durable recovery point before the network side effect
                tx_hash = broadcast_raw_transaction(batch.network, prepared["raw_transaction"])
            except Exception as exc:
                item.failure_reason = str(exc)[:500]
                # Once raw bytes are durable, a transport error is ambiguous:
                # the node may have accepted the transaction. Preserve the
                # exact signed bytes and retry that transaction, never sign a
                # replacement with a fresh nonce.
                item.status = "broadcasting" if item.signed_tx_raw else "failed"
                append_audit(db, "payment_batch_item.send_failed", "payment_batch_item",
                             resource_id=str(item.id), details={"error": item.failure_reason}, actor_id=actor)
                db.commit()
                continue

            category = {"payroll": "payroll", "airdrop": "airdrop"}.get(batch.kind, "batch_payment")
            row = _record_submitted_transaction(
                db, wallet_id=wallet.id, network=batch.network, tx_hash=tx_hash,
                from_address=wallet.address, to_address=item.recipient_address,
                amount=amount_float, amount_raw=int(item.amount_raw), decimals=item.decimals,
                token=batch.token.upper(), category=category, reference=item.reference,
            )
            item.tx_hash = tx_hash
            item.transaction_id = row.id
            item.status = "submitted"
            item.signed_tx_raw = None
            db.commit()

        has_ambiguous_broadcast = db.query(PaymentBatchItem).filter(
            PaymentBatchItem.batch_id == batch.id, PaymentBatchItem.status == "broadcasting"
        ).first() is not None
        batch.status = "approved" if has_ambiguous_broadcast else "completed"
    finally:
        if batch.status == "executing":
            batch.status = "approved"
        batch.execution_lock_token = None
        batch.execution_lock_acquired_at = None
        db.commit()

    items = db.query(PaymentBatchItem).filter(PaymentBatchItem.batch_id == batch.id).all()
    summary = {
        "batch_id": batch.id,
        "status": batch.status,
        "submitted": sum(1 for i in items if i.status in ("submitted", "confirmed")),
        "failed": sum(1 for i in items if i.status == "failed"),
        "total": len(items),
    }
    publish(db, "payment_batch.executed", summary, aggregate_type="payment_batch", aggregate_id=str(batch.id))
    append_audit(db, "payment_batch.executed", "payment_batch", resource_id=str(batch.id), details=summary, actor_id=actor)
    db.commit()
    return summary
