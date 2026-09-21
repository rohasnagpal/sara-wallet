"""Deterministic FIFO cost-basis lot builder and disposal calculator.

Not tax advice — see CLAUDE_STAGES_3_TO_7.md Stage 4.4. `rebuild_lots` is a
pure function of (a) confirmed Transaction history and (b) each
transaction's AccountingClassification for one (token, network) pair across
every Sara wallet together, not per-wallet: an internal transfer must move
a lot's cost basis between wallets rather than realising a gain, so lots
have to be tracked globally for the pair to do that correctly.

Cost basis rule, applied uniformly to every acquisition regardless of how it
arrived (swap-buy, airdrop, payroll receipt, invoice receipt, plain
receive): cost basis = the transaction's own fiat_usd_value, i.e. fair
market value at receipt (app.services.transaction_monitor already snapshots
this once, deterministically, at confirmation time).

FIFO is the only method implemented; `Disposal.method` exists so another
method can be added later without a schema change (Stage 4 requirement).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import AccountingClassification, CostLot, Disposal, Transaction, Wallet

_SOURCE_BY_CLASSIFICATION = {
    "swap": "swap_in",
    "airdrop": "airdrop",
    "payroll": "payroll_receipt",
    "invoice_receipt": "invoice_receipt",
}


def rebuild_lots(db: Session, token: str, network: str) -> dict:
    token = token.upper()
    network = network.lower()

    existing_lot_ids = [row.id for row in db.query(CostLot.id).filter_by(token=token, network=network).all()]
    if existing_lot_ids:
        db.query(Disposal).filter(Disposal.lot_id.in_(existing_lot_ids)).delete(synchronize_session=False)
        db.query(CostLot).filter(CostLot.id.in_(existing_lot_ids)).delete(synchronize_session=False)
        db.flush()

    own_wallet_ids = {w.id for w in db.query(Wallet.id).all()}
    txs = (
        db.query(Transaction)
        .filter(Transaction.token == token, Transaction.network == network,
                Transaction.status == "confirmed", Transaction.wallet_id.in_(own_wallet_ids))
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
        .all()
    )
    class_by_tx = {
        c.transaction_id: c
        for c in db.query(AccountingClassification).filter(
            AccountingClassification.transaction_id.in_([t.id for t in txs])
        ).all()
    }
    by_hash: dict[str, list[Transaction]] = defaultdict(list)
    for t in txs:
        if t.tx_hash:
            by_hash[t.tx_hash].append(t)

    open_lots: dict[int, list[CostLot]] = defaultdict(list)
    warnings: list[str] = []
    lots_created = 0
    disposals_created = 0

    def _new_lot(wallet_id: int, acquired_at: datetime, quantity_raw: int, decimals: int,
                 cost_usd: Decimal, source: str, acquisition_transaction_id: int | None) -> CostLot:
        lot = CostLot(
            wallet_id=wallet_id, token=token, network=network,
            acquisition_transaction_id=acquisition_transaction_id, acquired_at=acquired_at,
            quantity_raw=str(quantity_raw), remaining_raw=str(quantity_raw), decimals=decimals,
            acquisition_cost_usd=str(cost_usd), source=source,
        )
        db.add(lot)
        db.flush()
        open_lots[wallet_id].append(lot)
        return lot

    def _consume_fifo(wallet_id: int, need_raw: int, decimals: int, when: datetime) -> list[tuple[CostLot, int]]:
        allocations: list[tuple[CostLot, int]] = []
        remaining_need = need_raw
        lots = open_lots[wallet_id]
        i = 0
        while remaining_need > 0 and i < len(lots):
            lot = lots[i]
            available = int(lot.remaining_raw)
            if available <= 0:
                i += 1
                continue
            take = min(available, remaining_need)
            lot.remaining_raw = str(available - take)
            allocations.append((lot, take))
            remaining_need -= take
            if int(lot.remaining_raw) == 0:
                i += 1
        if remaining_need > 0:
            warnings.append(
                f"Some {token} you sent has no matching purchase record (wallet {wallet_id}, {network}), so its cost "
                f"was treated as $0 and your profit may look higher than it really is. This usually means older "
                f"transactions haven't been imported."
            )
            synthetic = _new_lot(wallet_id, when, remaining_need, decimals, Decimal(0), "unknown_opening_balance", None)
            synthetic.remaining_raw = "0"
            allocations.append((synthetic, remaining_need))
        return allocations

    for t in txs:
        if t.amount_raw is None:
            warnings.append(f"Transaction {t.id} is missing its exact amount, so it was left out.")
            continue
        quantity_raw = int(t.amount_raw)
        classification = class_by_tx.get(t.id)
        is_internal = bool(classification and classification.is_internal_transfer)

        if t.direction == "incoming":
            if is_internal:
                continue  # the paired outgoing leg's move already relocated the lot
            if t.fiat_usd_value is not None:
                cost_usd = Decimal(t.fiat_usd_value)
            else:
                warnings.append(f"Transaction {t.id} has no dollar value, so it was recorded as costing $0.")
                cost_usd = Decimal(0)
            classification_name = (classification.classification if classification else None) or t.category
            source = _SOURCE_BY_CLASSIFICATION.get(classification_name, "transfer_in")
            _new_lot(t.wallet_id, t.timestamp, quantity_raw, t.decimals, cost_usd, source, t.id)
            lots_created += 1

        elif t.direction == "outgoing":
            if is_internal:
                dest = next(
                    (s for s in by_hash.get(t.tx_hash, [])
                     if s.direction == "incoming" and s.wallet_id != t.wallet_id and s.wallet_id in own_wallet_ids),
                    None,
                )
                if dest is not None:
                    for lot, take in _consume_fifo(t.wallet_id, quantity_raw, t.decimals, t.timestamp):
                        share = (Decimal(take) / Decimal(lot.quantity_raw)) * Decimal(lot.acquisition_cost_usd) \
                            if int(lot.quantity_raw) else Decimal(0)
                        moved = CostLot(
                            wallet_id=dest.wallet_id, token=token, network=network,
                            acquisition_transaction_id=lot.acquisition_transaction_id, acquired_at=lot.acquired_at,
                            quantity_raw=str(take), remaining_raw=str(take), decimals=lot.decimals,
                            acquisition_cost_usd=str(share), source=lot.source,
                        )
                        db.add(moved)
                        db.flush()
                        open_lots[dest.wallet_id].append(moved)
                    continue
                warnings.append(
                    f"Transaction {t.id} looks like a move between your own wallets, but the other side wasn't "
                    f"found, so it was counted as a sale."
                )

            allocations = _consume_fifo(t.wallet_id, quantity_raw, t.decimals, t.timestamp)
            if t.fiat_usd_value is not None:
                proceeds = Decimal(t.fiat_usd_value)
            else:
                warnings.append(f"Transaction {t.id} has no dollar value, so the sale was recorded as $0.")
                proceeds = Decimal(0)

            fee_usd = None
            if t.fee_raw and t.fee_token:
                try:
                    from app.tools.market.coingecko import get_historical_price
                    fee_price = get_historical_price(t.fee_token, t.timestamp, "usd")
                    if fee_price and fee_price.get("price") is not None:
                        fee_usd = (Decimal(t.fee_raw) / (Decimal(10) ** 18)) * Decimal(str(fee_price["price"]))
                except Exception:
                    fee_usd = None
                if fee_usd is None:
                    warnings.append(f"Transaction {t.id}: couldn't get the price of the {t.fee_token} network fee, so the fee isn't included.")

            total_take = sum(take for _, take in allocations) or 1
            for lot, take in allocations:
                lot_unit_cost = (
                    Decimal(lot.acquisition_cost_usd) / Decimal(lot.quantity_raw) if int(lot.quantity_raw) else Decimal(0)
                )
                cost_basis = lot_unit_cost * take
                share_ratio = Decimal(take) / Decimal(total_take)
                proportional_proceeds = proceeds * share_ratio
                proportional_fee = fee_usd * share_ratio if fee_usd is not None else None
                realized = proportional_proceeds - cost_basis - (proportional_fee or Decimal(0))
                db.add(Disposal(
                    disposal_transaction_id=t.id, lot_id=lot.id, quantity_raw=str(take),
                    proceeds_usd=str(proportional_proceeds), cost_basis_usd=str(cost_basis),
                    fee_usd=str(proportional_fee) if proportional_fee is not None else None,
                    realized_gain_usd=str(realized), method="FIFO",
                ))
                disposals_created += 1

    db.commit()
    return {
        "token": token, "network": network,
        "lots_created": lots_created, "disposals_created": disposals_created, "warnings": warnings,
    }
