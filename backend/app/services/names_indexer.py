"""Block-cursor event indexer for the Sara Names registry (Stage 7
reliability). Recovers Sara's own name-tracking state after downtime by
replaying on-chain events instead of depending solely on each router call
having already succeeded — and since it reads real logs, it also picks up
anything Sara's own wallets did outside the app (e.g. via `cast`).

Scope: syncs local SaraName rows only for nodes whose current owner (or,
for a transfer, new owner) is one of THIS Sara instance's own wallets —
recovery of Sara's own operational state, not a general-purpose indexer for
arbitrary third parties' names (the registry emits ample events for that;
building a schema to track everyone's names is out of scope here).

Only ever advances the cursor past a block once it has
SARA_NAME_AMOY_CONFIRMATIONS confirmations — a reorg within that depth is
simply not indexed yet, never shown as final and then silently wrong.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.events import publish
from app.db.models import DomainEvent, IndexerCursor, SaraName, Wallet
from app.tools.names import sara_names

EXPIRY_REMINDER_WINDOW_DAYS = 30

CONTRACT_KEY = "sara_names_registry"
_EVENT_NAMES = ("NameRegistered", "NameRenewed", "NameTransferred", "SubnameCreated", "SubnameRevoked")


def _node_hex(node_bytes: bytes) -> str:
    return "0x" + node_bytes.hex()


def _get_cursor(db: Session, start_block: int) -> IndexerCursor:
    row = db.query(IndexerCursor).filter_by(contract=CONTRACT_KEY).first()
    if not row:
        row = IndexerCursor(contract=CONTRACT_KEY, last_block=start_block)
        db.add(row)
        db.flush()
    return row


def _wallet_for(db: Session, address: str) -> Wallet | None:
    return db.query(Wallet).filter(Wallet.chain == "evm", Wallet.address.ilike(address)).first()


def _apply_event(db: Session, log, event_name: str, own_addresses: set[str]) -> bool:
    args = log["args"]
    node = _node_hex(args["node"])
    tx_hash = log["transactionHash"].hex()

    if event_name == "NameRegistered":
        owner = args["owner"]
        if owner.lower() not in own_addresses:
            return False
        wallet = _wallet_for(db, owner)
        row = db.query(SaraName).filter(SaraName.node == node).first()
        if not row:
            row = SaraName(node=node, label=args["label"], wallet_id=wallet.id if wallet else 0)
            db.add(row)
        row.status = "registered"
        row.register_tx_hash = tx_hash
        row.expiry = datetime.utcfromtimestamp(args["expiry"])
        return True

    if event_name == "NameRenewed":
        row = db.query(SaraName).filter(SaraName.node == node).first()
        if not row:
            return False  # not one of ours (or not seen yet) — nothing local to update
        row.status = "renewed"
        row.last_renew_tx_hash = tx_hash
        row.expiry = datetime.utcfromtimestamp(args["newExpiry"])
        return True

    if event_name == "NameTransferred":
        to_addr = args["to"]
        row = db.query(SaraName).filter(SaraName.node == node).first()
        if to_addr.lower() in own_addresses:
            wallet = _wallet_for(db, to_addr)
            if not row:
                # We didn't originate this name locally (transferred in from
                # elsewhere) — label is unknown from this event alone; the
                # node hex remains the real identity key regardless.
                row = SaraName(node=node, label="", wallet_id=wallet.id if wallet else 0, status="registered")
                db.add(row)
            row.wallet_id = wallet.id if wallet else row.wallet_id
            row.status = "registered"
            row.last_transfer_tx_hash = tx_hash
            return True
        if row:
            row.status = "transferred_away"
            row.last_transfer_tx_hash = tx_hash
            return True
        return False

    if event_name == "SubnameCreated":
        owner = args["owner"]
        if owner.lower() not in own_addresses:
            return False
        wallet = _wallet_for(db, owner)
        row = db.query(SaraName).filter(SaraName.node == node).first()
        if not row:
            row = SaraName(
                node=node, label=args["label"], parent_node=_node_hex(args["parentNode"]),
                wallet_id=wallet.id if wallet else 0, status="registered",
            )
            db.add(row)
        return True

    if event_name == "SubnameRevoked":
        row = db.query(SaraName).filter(SaraName.node == node).first()
        if row:
            row.status = "transferred_away"
            return True
        return False

    return False


def sync_events(db: Session, *, max_blocks_per_run: int = 5000) -> dict:
    if not sara_names.is_configured():
        return {"synced": False, "reason": "Sara Names is not configured on this instance"}
    from app.core.config import settings

    try:
        w3 = sara_names.get_web3()
        latest_block = w3.eth.block_number
    except Exception as exc:
        return {"synced": False, "reason": f"could not reach the Amoy RPC: {exc}"}

    safe_block = latest_block - settings.SARA_NAME_AMOY_CONFIRMATIONS
    if safe_block <= 0:
        return {"synced": False, "reason": "chain too young for the configured confirmation depth"}

    cursor = _get_cursor(db, start_block=max(0, safe_block - 1))
    from_block = cursor.last_block + 1
    if from_block > safe_block:
        return {"synced": True, "processed_blocks": 0, "from_block": from_block, "to_block": safe_block, "updated": 0}
    to_block = min(safe_block, from_block + max_blocks_per_run - 1)

    own_addresses = {w.address.lower() for w in db.query(Wallet).filter(Wallet.chain == "evm").all()}
    contract = sara_names._contract(w3)

    updated = 0
    for event_name in _EVENT_NAMES:
        event = getattr(contract.events, event_name)
        for log in event().get_logs(from_block=from_block, to_block=to_block):
            if _apply_event(db, log, event_name, own_addresses):
                updated += 1

    cursor.last_block = to_block
    append_audit(
        db, "sara_names_indexer.synced", "indexer_cursor", resource_id=str(cursor.id),
        details={"from_block": from_block, "to_block": to_block, "updated": updated},
        actor_type="system", actor_id="names-indexer",
    )
    db.commit()
    return {"synced": True, "processed_blocks": to_block - from_block + 1, "from_block": from_block, "to_block": to_block, "updated": updated}


def check_expiring_names(db: Session, *, now: datetime | None = None) -> int:
    """Publishes a 'sara_name.expiring_soon' domain event for each locally-
    tracked, still-owned name expiring within EXPIRY_REMINDER_WINDOW_DAYS.
    Reuses the existing alert pipeline (app.services.alerts already
    delivers to Telegram/email/webhook) rather than a new notification
    channel — never auto-renews, per Stage 7.3 ("without auto-renewing
    unless the user has separately created and approved a recurring
    payment policy"). The event_key is keyed to (node, expiry) so it fires
    exactly once per expiry timestamp, not once per foundation cycle."""
    now = now or datetime.utcnow()
    cutoff = now + timedelta(days=EXPIRY_REMINDER_WINDOW_DAYS)
    rows = (
        db.query(SaraName)
        .filter(SaraName.status.in_(("registered", "renewed")))
        .filter(SaraName.expiry.isnot(None))
        .filter(SaraName.expiry <= cutoff)
        .filter(SaraName.expiry > now)
        .all()
    )
    reminded = 0
    for row in rows:
        event_key = f"sara_name:expiry_reminder:{row.node}:{row.expiry.isoformat()}"
        # publish() itself never checks for a pre-existing event_key (the DB
        # unique constraint is the final guard, by design elsewhere in this
        # codebase) — but this function runs every foundation cycle for the
        # same still-unexpired names, so it must pre-check here itself or
        # every repeat cycle would hit an IntegrityError on commit.
        if db.query(DomainEvent.id).filter(DomainEvent.event_key == event_key).first():
            continue
        publish(
            db, "sara_name.expiring_soon",
            {"label": row.label, "node": row.node, "expiry": row.expiry.isoformat(),
             "days_left": (row.expiry - now).days},
            aggregate_type="sara_name", aggregate_id=row.node, event_key=event_key,
        )
        reminded += 1
    if reminded:
        db.commit()
    return reminded


def cursor_lag(db: Session) -> dict | None:
    """Reads current chain height vs. the saved cursor — used by the
    /api/system/foundation monitoring endpoint, never blocks on writing."""
    if not sara_names.is_configured():
        return None
    cursor = db.query(IndexerCursor).filter_by(contract=CONTRACT_KEY).first()
    if not cursor:
        return {"last_block": None, "current_block": None, "lag_blocks": None}
    try:
        current_block = sara_names.get_web3().eth.block_number
    except Exception:
        return {"last_block": cursor.last_block, "current_block": None, "lag_blocks": None}
    return {"last_block": cursor.last_block, "current_block": current_block, "lag_blocks": max(0, current_block - cursor.last_block)}
