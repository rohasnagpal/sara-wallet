import csv
import hashlib
import io
import json
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import AccountingClassification, Counterparty, CostLot, Disposal, Transaction, Wallet
from app.db.session import get_db
from app.routers.payments import _csv_safe
from app.services import accounting_matcher, cost_basis

router = APIRouter(prefix="/accounting", tags=["accounting"], dependencies=[Depends(require_session)])

_CLASSIFICATIONS = (
    "income", "expense", "transfer", "swap", "fee", "payroll", "invoice_receipt",
    "airdrop", "acquisition", "disposal", "unknown",
)
_INCOME_CLASSIFICATIONS = {"income", "payroll", "invoice_receipt", "airdrop"}
_EXPENSE_CLASSIFICATIONS = {"expense", "payroll", "fee"}


def _get_or_create_classification(db: Session, transaction_id: int) -> AccountingClassification:
    return accounting_matcher.get_or_create_classification(db, transaction_id)


def _amount(tx: Transaction) -> str:
    if tx.amount_raw is not None and tx.decimals is not None:
        return format(Decimal(tx.amount_raw) / (Decimal(10) ** tx.decimals), "f")
    return str(tx.amount or 0)


def _tx_row(tx: Transaction, classification: AccountingClassification | None, counterparty_name: str | None) -> dict:
    return {
        "id": tx.id, "wallet_id": tx.wallet_id, "network": tx.network, "token": tx.token,
        "tx_hash": tx.tx_hash, "direction": tx.direction, "status": tx.status,
        "amount": _amount(tx), "amount_raw": tx.amount_raw, "decimals": tx.decimals,
        "fee_raw": tx.fee_raw, "fee_token": tx.fee_token,
        "fiat_usd_value": tx.fiat_usd_value, "fiat_inr_value": tx.fiat_inr_value,
        "valuation_source": tx.valuation_source, "valued_at": tx.valued_at.isoformat() if tx.valued_at else None,
        "category": tx.category, "reference": tx.reference, "note": tx.note,
        "timestamp": tx.timestamp.isoformat() if tx.timestamp else None,
        "classification": classification.classification if classification else "unknown",
        "counterparty_id": classification.counterparty_id if classification else None,
        "counterparty_name": counterparty_name,
        "project": classification.project if classification else None,
        "client": classification.client if classification else None,
        "is_internal_transfer": classification.is_internal_transfer if classification else False,
        "match_group_id": classification.match_group_id if classification else None,
        "match_confidence": classification.match_confidence if classification else None,
        "accounting_notes": classification.notes if classification else None,
    }


def _filtered_transactions(
    db: Session, *, start_date: datetime | None, end_date: datetime | None, wallet_id: int | None,
    token: str | None, network: str | None, classification: str | None, counterparty_id: int | None,
    client: str | None, project: str | None, status: str | None = "confirmed",
) -> list[tuple[Transaction, AccountingClassification | None]]:
    query = db.query(Transaction, AccountingClassification).outerjoin(
        AccountingClassification, AccountingClassification.transaction_id == Transaction.id
    )
    if status is not None:
        query = query.filter(Transaction.status == status)
    if start_date is not None:
        query = query.filter(Transaction.timestamp >= start_date)
    if end_date is not None:
        query = query.filter(Transaction.timestamp <= end_date)
    if wallet_id is not None:
        query = query.filter(Transaction.wallet_id == wallet_id)
    if token is not None:
        query = query.filter(Transaction.token == token.upper())
    if network is not None:
        query = query.filter(Transaction.network == network.lower())
    if classification is not None:
        query = query.filter(AccountingClassification.classification == classification)
    if counterparty_id is not None:
        query = query.filter(AccountingClassification.counterparty_id == counterparty_id)
    if client is not None:
        query = query.filter(AccountingClassification.client == client)
    if project is not None:
        query = query.filter(AccountingClassification.project == project)
    return query.order_by(Transaction.timestamp.desc(), Transaction.id.desc()).all()


@router.get("/transactions")
def list_transactions(
    start_date: datetime | None = None, end_date: datetime | None = None, wallet_id: int | None = None,
    token: str | None = None, network: str | None = None, classification: str | None = None,
    counterparty_id: int | None = None, client: str | None = None, project: str | None = None,
    limit: int = Query(200, ge=1, le=2000), db: Session = Depends(get_db),
):
    rows = _filtered_transactions(
        db, start_date=start_date, end_date=end_date, wallet_id=wallet_id, token=token, network=network,
        classification=classification, counterparty_id=counterparty_id, client=client, project=project,
    )[:limit]
    counterparty_names = {c.id: c.display_name for c in db.query(Counterparty).all()}
    return {"transactions": [
        _tx_row(tx, cls, counterparty_names.get(cls.counterparty_id) if cls else None) for tx, cls in rows
    ]}


class ClassificationUpdate(BaseModel):
    classification: str | None = None
    counterparty_id: int | None = None
    project: str | None = Field(None, max_length=160)
    client: str | None = Field(None, max_length=160)
    notes: str | None = Field(None, max_length=2000)


@router.patch("/transactions/{transaction_id}")
def update_classification(transaction_id: int, body: ClassificationUpdate, db: Session = Depends(get_db)):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")
    if body.classification is not None and body.classification not in _CLASSIFICATIONS:
        raise HTTPException(400, f"classification must be one of {_CLASSIFICATIONS}")
    row = _get_or_create_classification(db, transaction_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(row, key, value)
    append_audit(db, "accounting_classification.updated", "accounting_classification",
                 resource_id=str(row.id), details=changes)
    db.commit()
    return _tx_row(tx, row, None)


@router.post("/transactions/match")
def match_transactions(db: Session = Depends(get_db)):
    matched = accounting_matcher.match_internal_transfers_and_swaps(db)
    return {"matched_pairs": matched}


@router.get("/lots")
def list_lots(token: str | None = None, network: str | None = None, wallet_id: int | None = None,
              open_only: bool = True, db: Session = Depends(get_db)):
    query = db.query(CostLot)
    if token is not None:
        query = query.filter(CostLot.token == token.upper())
    if network is not None:
        query = query.filter(CostLot.network == network.lower())
    if wallet_id is not None:
        query = query.filter(CostLot.wallet_id == wallet_id)
    rows = query.order_by(CostLot.acquired_at).all()
    if open_only:
        rows = [r for r in rows if int(r.remaining_raw) > 0]
    return {"lots": [{
        "id": r.id, "wallet_id": r.wallet_id, "token": r.token, "network": r.network,
        "acquisition_transaction_id": r.acquisition_transaction_id, "acquired_at": r.acquired_at.isoformat(),
        "quantity": format(Decimal(r.quantity_raw) / (Decimal(10) ** r.decimals), "f"),
        "remaining": format(Decimal(r.remaining_raw) / (Decimal(10) ** r.decimals), "f"),
        "acquisition_cost_usd": r.acquisition_cost_usd, "source": r.source,
    } for r in rows]}


class RebuildBody(BaseModel):
    token: str
    network: str


@router.post("/lots/rebuild")
def rebuild_lots(body: RebuildBody, db: Session = Depends(get_db)):
    result = cost_basis.rebuild_lots(db, body.token, body.network)
    append_audit(db, "cost_lots.rebuilt", "cost_lot", details=result)
    db.commit()
    return result


@router.get("/reports/pnl")
def pnl_report(
    token: str, network: str, start_date: datetime | None = None, end_date: datetime | None = None,
    wallet_id: int | None = None, db: Session = Depends(get_db),
):
    token = token.upper()
    network = network.lower()
    disposal_query = (
        db.query(Disposal, Transaction)
        .join(Transaction, Transaction.id == Disposal.disposal_transaction_id)
        .filter(Transaction.token == token, Transaction.network == network)
    )
    if start_date is not None:
        disposal_query = disposal_query.filter(Transaction.timestamp >= start_date)
    if end_date is not None:
        disposal_query = disposal_query.filter(Transaction.timestamp <= end_date)
    if wallet_id is not None:
        disposal_query = disposal_query.filter(Transaction.wallet_id == wallet_id)
    disposal_rows = disposal_query.order_by(Transaction.timestamp).all()

    realized_total = sum((Decimal(d.realized_gain_usd) for d, _ in disposal_rows), Decimal(0))
    warnings: list[str] = []
    synthetic_lot_ids = {
        r.id for r in db.query(CostLot).filter(CostLot.token == token, CostLot.network == network,
                                                 CostLot.source == "unknown_opening_balance").all()
    }
    if any(d.lot_id in synthetic_lot_ids for d, _ in disposal_rows):
        warnings.append("one or more disposals used a zero-cost synthetic lot because acquisition history was incomplete")

    lot_query = db.query(CostLot).filter(CostLot.token == token, CostLot.network == network)
    if wallet_id is not None:
        lot_query = lot_query.filter(CostLot.wallet_id == wallet_id)
    open_lots = [lot for lot in lot_query.all() if int(lot.remaining_raw) > 0]

    unrealized_total = Decimal(0)
    current_price = None
    if open_lots:
        from app.tools.market.coingecko import get_price
        quote = get_price(token, "usd")
        if quote and quote.get("price"):
            current_price = Decimal(str(quote["price"]))
            for lot in open_lots:
                remaining_qty = Decimal(lot.remaining_raw) / (Decimal(10) ** lot.decimals)
                remaining_cost = (
                    Decimal(lot.remaining_raw) / Decimal(lot.quantity_raw) * Decimal(lot.acquisition_cost_usd)
                    if int(lot.quantity_raw) else Decimal(0)
                )
                unrealized_total += remaining_qty * current_price - remaining_cost
        else:
            warnings.append(f"could not source a current price for {token}; unrealized gain/loss omitted")

    return {
        "method": "FIFO", "currency": "USD", "token": token, "network": network,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "realized_gain_usd": str(realized_total), "unrealized_gain_usd": str(unrealized_total) if open_lots else None,
        "current_price_usd": str(current_price) if current_price is not None else None,
        "disposal_count": len(disposal_rows), "open_lot_count": len(open_lots),
        "disposal_transaction_ids": [d.disposal_transaction_id for d, _ in disposal_rows],
        "warnings": warnings,
        "disclaimer": "Not tax advice. FIFO cost-basis calculation for informational purposes only.",
    }


@router.get("/reports/income-expense")
def income_expense_report(
    start_date: datetime | None = None, end_date: datetime | None = None, wallet_id: int | None = None,
    token: str | None = None, network: str | None = None, counterparty_id: int | None = None,
    client: str | None = None, project: str | None = None, db: Session = Depends(get_db),
):
    rows = _filtered_transactions(
        db, start_date=start_date, end_date=end_date, wallet_id=wallet_id, token=token, network=network,
        classification=None, counterparty_id=counterparty_id, client=client, project=project,
    )
    income_total = Decimal(0)
    expense_total = Decimal(0)
    by_classification: dict[str, Decimal] = {}
    income_ids: list[int] = []
    expense_ids: list[int] = []
    warnings: list[str] = []
    for tx, cls in rows:
        if cls and cls.is_internal_transfer:
            continue  # internal transfers never count as income/expense
        classification = (cls.classification if cls else None) or "unknown"
        value = Decimal(tx.fiat_usd_value) if tx.fiat_usd_value is not None else None
        if value is None:
            if classification in _INCOME_CLASSIFICATIONS or classification in _EXPENSE_CLASSIFICATIONS:
                warnings.append(f"transaction {tx.id} has no USD valuation and was excluded from totals")
            continue
        if tx.direction == "incoming" and classification in _INCOME_CLASSIFICATIONS:
            income_total += value
            income_ids.append(tx.id)
            by_classification[classification] = by_classification.get(classification, Decimal(0)) + value
        elif tx.direction == "outgoing" and classification in _EXPENSE_CLASSIFICATIONS:
            expense_total += value
            expense_ids.append(tx.id)
            by_classification[classification] = by_classification.get(classification, Decimal(0)) - value
    return {
        "currency": "USD",
        "start_date": start_date.isoformat() if start_date else None, "end_date": end_date.isoformat() if end_date else None,
        "income_total_usd": str(income_total), "expense_total_usd": str(expense_total),
        "net_usd": str(income_total - expense_total),
        "by_classification": {k: str(v) for k, v in by_classification.items()},
        "income_transaction_ids": income_ids, "expense_transaction_ids": expense_ids,
        "warnings": warnings,
    }


@router.get("/reports/data-quality")
def data_quality_report(db: Session = Depends(get_db)):
    unpriced = [t.id for t in db.query(Transaction).filter(
        Transaction.status == "confirmed", Transaction.fiat_usd_value.is_(None)
    ).all()]
    uncategorised = [
        t.id for t, cls in db.query(Transaction, AccountingClassification)
        .outerjoin(AccountingClassification, AccountingClassification.transaction_id == Transaction.id)
        .filter(Transaction.status == "confirmed").all()
        if cls is None or cls.classification == "unknown"
    ]
    failed = [t.id for t in db.query(Transaction).filter(Transaction.status == "failed").all()]
    incomplete = [t.id for t in db.query(Transaction).filter(
        Transaction.status == "confirmed", Transaction.amount_raw.is_(None)
    ).all()]

    seen: dict[tuple, list[int]] = {}
    for t in db.query(Transaction).filter(Transaction.status == "confirmed").all():
        key = (t.network, t.tx_hash, t.direction, t.wallet_id)
        seen.setdefault(key, []).append(t.id)
    duplicated = [ids for ids in seen.values() if len(ids) > 1]

    return {
        "unpriced_transaction_ids": unpriced, "uncategorised_transaction_ids": uncategorised,
        "failed_transaction_ids": failed, "incomplete_transaction_ids": incomplete,
        "duplicated_transaction_id_groups": duplicated,
        "counts": {
            "unpriced": len(unpriced), "uncategorised": len(uncategorised), "failed": len(failed),
            "incomplete": len(incomplete), "duplicated_groups": len(duplicated),
        },
    }


_EXPORT_COLUMNS = (
    "id", "wallet_id", "network", "token", "tx_hash", "direction", "status", "amount", "amount_raw", "decimals",
    "fee_raw", "fee_token", "fiat_usd_value", "fiat_inr_value", "valuation_source", "classification",
    "counterparty_name", "project", "client", "reference", "category", "note", "accounting_notes",
    "timestamp", "row_hash",
)


def _cell(row: dict, col: str):
    value = row.get(col)
    return "" if value is None else value


def _row_hash(row: dict) -> str:
    canonical = json.dumps({k: v for k, v in row.items() if k != "row_hash"}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _export_rows(db: Session, *, start_date, end_date, wallet_id, token, network) -> list[dict]:
    rows = _filtered_transactions(
        db, start_date=start_date, end_date=end_date, wallet_id=wallet_id, token=token, network=network,
        classification=None, counterparty_id=None, client=None, project=None, status=None,
    )
    counterparty_names = {c.id: c.display_name for c in db.query(Counterparty).all()}
    out = []
    for tx, cls in rows:
        row = _tx_row(tx, cls, counterparty_names.get(cls.counterparty_id) if cls else None)
        row["row_hash"] = _row_hash(row)
        out.append(row)
    return out


@router.get("/exports/transactions.csv")
def export_csv(
    start_date: datetime | None = None, end_date: datetime | None = None, wallet_id: int | None = None,
    token: str | None = None, network: str | None = None, db: Session = Depends(get_db),
):
    rows = _export_rows(db, start_date=start_date, end_date=end_date, wallet_id=wallet_id, token=token, network=network)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_csv_safe(_cell(row, col)) for col in _EXPORT_COLUMNS])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sara-transactions.csv"},
    )


@router.get("/exports/transactions.xlsx")
def export_xlsx(
    start_date: datetime | None = None, end_date: datetime | None = None, wallet_id: int | None = None,
    token: str | None = None, network: str | None = None, db: Session = Depends(get_db),
):
    rows = _export_rows(db, start_date=start_date, end_date=end_date, wallet_id=wallet_id, token=token, network=network)
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(list(_EXPORT_COLUMNS))
    for row in rows:
        # Same formula-injection guard as the CSV export, applied defensively
        # even though openpyxl writes explicit string cells (not formulas).
        ws.append([_csv_safe(_cell(row, col)) for col in _EXPORT_COLUMNS])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sara-transactions.xlsx"},
    )
