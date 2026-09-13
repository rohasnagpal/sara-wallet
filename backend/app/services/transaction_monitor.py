"""Receipt, confirmation, reorganisation and valuation tracking for EVM sends."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import logging

from sqlalchemy.orm import Session
from web3.exceptions import TransactionNotFound

from app.core.audit import append_audit
from app.core.config import settings
from app.core.events import publish
from app.db.models import Transaction, Wallet

log = logging.getLogger("sara.transactions")


def _confirmations_required(network: str) -> int:
    if network == "polygon":
        return max(1, settings.EVM_CONFIRMATIONS_POLYGON)
    return max(1, settings.EVM_CONFIRMATIONS_DEFAULT)


def _hex(value) -> str | None:
    if value is None:
        return None
    return value.hex() if hasattr(value, "hex") else str(value)


def _snapshot_valuation(tx: Transaction, occurred_at: datetime) -> None:
    if tx.amount_raw is None or tx.decimals is None or tx.fiat_usd_value is not None:
        return
    try:
        from app.tools.market.coingecko import get_historical_price
        amount = Decimal(tx.amount_raw) / (Decimal(10) ** tx.decimals)
        usd = get_historical_price(tx.token or "", occurred_at, "usd")
        inr = get_historical_price(tx.token or "", occurred_at, "inr")
        if usd and usd.get("price") is not None:
            tx.fiat_usd_value = str(amount * Decimal(str(usd["price"])))
            tx.valuation_source = usd.get("source")
        if inr and inr.get("price") is not None:
            tx.fiat_inr_value = str(amount * Decimal(str(inr["price"])))
            tx.valuation_source = tx.valuation_source or inr.get("source")
        if tx.fiat_usd_value is not None or tx.fiat_inr_value is not None:
            tx.valued_at = occurred_at
    except Exception:
        # Price availability must never prevent receipt finality from being
        # recorded. A later cycle can fill the still-null snapshot.
        log.warning("Could not value transaction %s", tx.tx_hash, exc_info=True)


def _receipt_for(w3, tx_hash: str):
    try:
        return w3.eth.get_transaction_receipt(tx_hash)
    except TransactionNotFound:
        return None


def check_transaction(db: Session, tx: Transaction) -> bool:
    if tx.chain != "evm" or not tx.tx_hash or not tx.network:
        return False
    from app.chains.evm import get_web3

    now = datetime.utcnow()
    w3 = get_web3(tx.network)
    receipt = _receipt_for(w3, tx.tx_hash)
    tx.last_checked_at = now

    if receipt is None:
        if tx.block_hash:
            old_block_hash = tx.block_hash
            tx.status = "submitted"
            tx.block_number = None
            tx.block_hash = None
            tx.confirmations = 0
            tx.confirmed_at = None
            publish(
                db, "transaction.reorg_detected",
                {"tx_hash": tx.tx_hash, "network": tx.network, "old_block_hash": old_block_hash},
                aggregate_type="transaction", aggregate_id=str(tx.id),
                event_key=f"tx:{tx.network}:{tx.tx_hash}:reorg:{old_block_hash}",
            )
            append_audit(
                db, "transaction.reorg_detected", "transaction", resource_id=str(tx.id),
                details={"tx_hash": tx.tx_hash, "network": tx.network, "old_block_hash": old_block_hash},
                actor_type="system", actor_id="transaction-monitor",
            )
        db.commit()
        return True

    block_number = int(receipt["blockNumber"])
    block_hash = _hex(receipt["blockHash"])
    previous_block_hash = tx.block_hash
    tx.block_number = block_number
    tx.block_hash = block_hash
    tx.confirmations = max(0, int(w3.eth.block_number) - block_number + 1)

    wallet = db.query(Wallet).filter(Wallet.id == tx.wallet_id).first()
    try:
        chain_tx = w3.eth.get_transaction(tx.tx_hash)
        tx.from_address = chain_tx.get("from") or (wallet.address if wallet else None)
    except Exception:
        tx.from_address = tx.from_address or (wallet.address if wallet else None)

    gas_used = int(receipt.get("gasUsed") or 0)
    gas_price = int(receipt.get("effectiveGasPrice") or 0)
    if gas_used and gas_price:
        tx.fee_raw = str(gas_used * gas_price)
        tx.fee_token = {"polygon": "POL"}.get(tx.network, "ETH")

    try:
        occurred_at = datetime.utcfromtimestamp(int(w3.eth.get_block(block_number)["timestamp"]))
    except Exception:
        occurred_at = tx.timestamp or now

    if int(receipt.get("status", 1)) == 0:
        changed = tx.status != "failed"
        tx.status = "failed"
        tx.failure_reason = "EVM receipt status is 0"
        if changed:
            publish(
                db, "transaction.failed", {"tx_hash": tx.tx_hash, "network": tx.network},
                aggregate_type="transaction", aggregate_id=str(tx.id),
                event_key=f"tx:{tx.network}:{tx.tx_hash}:failed",
            )
            append_audit(
                db, "transaction.failed", "transaction", resource_id=str(tx.id),
                details={"tx_hash": tx.tx_hash, "network": tx.network},
                actor_type="system", actor_id="transaction-monitor",
            )
    elif tx.confirmations >= _confirmations_required(tx.network):
        changed = tx.status != "confirmed" or previous_block_hash != block_hash
        tx.status = "confirmed"
        tx.failure_reason = None
        tx.confirmed_at = tx.confirmed_at or now
        _snapshot_valuation(tx, occurred_at)
        if changed:
            publish(
                db, "transaction.confirmed",
                {"tx_hash": tx.tx_hash, "network": tx.network, "block_number": block_number,
                 "confirmations": tx.confirmations},
                aggregate_type="transaction", aggregate_id=str(tx.id),
                event_key=f"tx:{tx.network}:{tx.tx_hash}:confirmed:{block_hash}",
            )
            append_audit(
                db, "transaction.confirmed", "transaction", resource_id=str(tx.id),
                details={"tx_hash": tx.tx_hash, "network": tx.network,
                         "block_number": block_number, "block_hash": block_hash},
                actor_type="system", actor_id="transaction-monitor",
            )
    else:
        tx.status = "submitted"

    db.commit()
    return True


def check_transactions(db: Session, *, limit: int = 100) -> int:
    rows = (
        db.query(Transaction)
        .filter(Transaction.chain == "evm", Transaction.status.in_(("submitted", "confirmed")))
        .order_by(Transaction.id)
        .limit(limit)
        .all()
    )
    checked = 0
    for tx in rows:
        # Once well beyond the configured observation depth, the receipt is
        # treated as final and no longer polled on every cycle.
        if tx.status == "confirmed" and tx.confirmations >= settings.TRANSACTION_MONITOR_DEPTH:
            continue
        try:
            checked += int(check_transaction(db, tx))
        except Exception:
            db.rollback()
            log.warning("Transaction check failed for %s", tx.tx_hash, exc_info=True)
    return checked
