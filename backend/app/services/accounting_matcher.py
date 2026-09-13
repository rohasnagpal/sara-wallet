"""Deterministic tx_hash-based matching of internal transfers and swap legs.

Every match here is exact — grouped by (network, tx_hash) — never a fuzzy
amount/time heuristic. An unmatched leg (the other side not yet indexed, or
genuinely external) is simply left "unknown" rather than guessed at, per
CLAUDE_STAGES_3_TO_7.md Stage 4.2: "surface uncertain matches for review
instead of guessing."

Internal transfers between Sara's own wallets and swaps both produce two
Transaction rows sharing the same (network, tx_hash) — one written at send
time (app.routers.chat._record_submitted_transaction) and one written later
by the activity indexer (app.services.activity_indexer) when it picks up
the same on-chain event from the other side. That shared tx_hash is what
makes this matching exact instead of heuristic.
"""
from __future__ import annotations

from collections import defaultdict
import uuid

from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.events import publish
from app.db.models import AccountingClassification, Transaction, Wallet


def get_or_create_classification(db: Session, transaction_id: int) -> AccountingClassification:
    row = db.query(AccountingClassification).filter_by(transaction_id=transaction_id).first()
    if row is None:
        row = AccountingClassification(transaction_id=transaction_id)
        db.add(row)
        db.flush()
    return row


def match_internal_transfers_and_swaps(db: Session) -> int:
    own_wallet_ids = {w.id for w in db.query(Wallet.id).all()}
    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in db.query(Transaction).filter(
        Transaction.status == "confirmed", Transaction.tx_hash.isnot(None)
    ).all():
        groups[(tx.network, tx.tx_hash)].append(tx)

    matched = 0
    for (network, tx_hash), rows in groups.items():
        outgoing = [t for t in rows if t.direction == "outgoing" and t.wallet_id in own_wallet_ids]
        incoming = [t for t in rows if t.direction == "incoming" and t.wallet_id in own_wallet_ids]
        if not outgoing or not incoming:
            continue  # only one side indexed so far, or the other side is external — not a guess
        out_tx, in_tx = outgoing[0], incoming[0]
        out_class = get_or_create_classification(db, out_tx.id)
        in_class = get_or_create_classification(db, in_tx.id)
        if out_class.match_group_id and in_class.match_group_id:
            continue  # already matched by a previous run

        group_id = out_class.match_group_id or in_class.match_group_id or uuid.uuid4().hex
        if out_tx.category == "swap" or out_class.classification == "swap":
            out_class.classification = "swap"
            in_class.classification = "swap"
        else:
            out_class.classification = "transfer"
            in_class.classification = "transfer"
            out_class.is_internal_transfer = True
            in_class.is_internal_transfer = True

        out_class.match_group_id = group_id
        in_class.match_group_id = group_id
        out_class.match_confidence = "confirmed"
        in_class.match_confidence = "confirmed"
        matched += 1

        publish(
            db, "accounting.legs_matched",
            {"network": network, "tx_hash": tx_hash, "outgoing_transaction_id": out_tx.id, "incoming_transaction_id": in_tx.id},
            aggregate_type="accounting_classification", aggregate_id=group_id,
            event_key=f"accounting:match:{network}:{tx_hash}",
        )
        append_audit(
            db, "accounting.legs_matched", "accounting_classification", resource_id=group_id,
            details={"network": network, "tx_hash": tx_hash}, actor_type="system", actor_id="accounting-matcher",
        )
    db.commit()
    return matched
