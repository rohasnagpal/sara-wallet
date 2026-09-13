from datetime import datetime
import statistics

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.session_auth import require_session
from app.db.session import get_db

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


# ── Name resolution ──────────────────────────────────────────────────────────

class ResolveBody(BaseModel):
    name: str

@router.post("/names/resolve", dependencies=[Depends(require_session)])
def resolve_name(body: ResolveBody):
    name = body.name.strip().lower()
    if name.endswith(".eth"):
        from app.tools.names.ens import resolve
        addr = resolve(name)
        return {"name": name, "address": addr, "chain": "evm", "resolved": bool(addr)}
    elif name.endswith(".sol"):
        from app.tools.names.sns import resolve
        addr = resolve(name)
        return {"name": name, "address": addr, "chain": "solana", "resolved": bool(addr)}
    from app.tools.names import sara_names
    if sara_names.is_configured() and sara_names.is_valid_label(name.split(".")[0]):
        result = sara_names.resolve(name)
        if result:
            return {"name": name, "address": result["owner"], "chain": "evm", "resolved": True, "source": "sara_names"}
    return {"name": name, "address": None, "chain": None, "resolved": False}


# ── News & Sentiment ──────────────────────────────────────────────────────────

@router.get("/news")
def get_news(coin: str = "", filter: str = "hot"):
    from app.tools.market.cryptopanic import get_news as _news
    currencies = [coin.upper()] if coin else None
    return _news(currencies=currencies, filter=filter)


@router.get("/sentiment/{coin}")
def get_sentiment(coin: str):
    from app.tools.market.cryptopanic import get_sentiment
    return get_sentiment(coin)


# ── Wallet intelligence (Stage 5.5) ──────────────────────────────────────────
# Deterministic ledger queries only — an LLM may explain these results but
# must never invent transactions or totals. Every total here carries the
# source transaction IDs it was computed from, so any answer traces back to
# the ledger (CLAUDE_STAGES_3_TO_7.md Stage 5.5).

def _outgoing_query(db: Session, wallet_id, network, start_date, end_date):
    from app.db.models import Transaction
    query = db.query(Transaction).filter(Transaction.direction == "outgoing", Transaction.status == "confirmed")
    if wallet_id is not None:
        query = query.filter(Transaction.wallet_id == wallet_id)
    if network is not None:
        query = query.filter(Transaction.network == network)
    if start_date is not None:
        query = query.filter(Transaction.timestamp >= start_date)
    if end_date is not None:
        query = query.filter(Transaction.timestamp <= end_date)
    return query.all()


@router.get("/wallet/top-payees", dependencies=[Depends(require_session)])
def top_payees(wallet_id: int | None = None, network: str | None = None, start_date: datetime | None = None,
                end_date: datetime | None = None, limit: int = 10, db: Session = Depends(get_db)):
    from decimal import Decimal
    totals: dict[str, dict] = {}
    for tx in _outgoing_query(db, wallet_id, network, start_date, end_date):
        if tx.fiat_usd_value is None:
            continue
        payee = tx.counterparty or tx.to_address or "unknown"
        entry = totals.setdefault(payee, {"payee": payee, "total_usd": Decimal(0), "transaction_ids": []})
        entry["total_usd"] += Decimal(tx.fiat_usd_value)
        entry["transaction_ids"].append(tx.id)
    ranked = sorted(totals.values(), key=lambda e: e["total_usd"], reverse=True)[:limit]
    return {"payees": [{**e, "total_usd": str(e["total_usd"])} for e in ranked]}


@router.get("/wallet/spend-by-category", dependencies=[Depends(require_session)])
def spend_by_category(wallet_id: int | None = None, network: str | None = None, start_date: datetime | None = None,
                       end_date: datetime | None = None, db: Session = Depends(get_db)):
    from decimal import Decimal
    totals: dict[str, dict] = {}
    for tx in _outgoing_query(db, wallet_id, network, start_date, end_date):
        if tx.fiat_usd_value is None:
            continue
        category = tx.category or "uncategorised"
        entry = totals.setdefault(category, {"category": category, "total_usd": Decimal(0), "transaction_ids": []})
        entry["total_usd"] += Decimal(tx.fiat_usd_value)
        entry["transaction_ids"].append(tx.id)
    ranked = sorted(totals.values(), key=lambda e: e["total_usd"], reverse=True)
    return {"categories": [{**e, "total_usd": str(e["total_usd"])} for e in ranked]}


@router.get("/wallet/recurring-counterparties", dependencies=[Depends(require_session)])
def recurring_counterparties(wallet_id: int | None = None, network: str | None = None,
                              start_date: datetime | None = None, end_date: datetime | None = None,
                              min_occurrences: int = 3, db: Session = Depends(get_db)):
    counts: dict[str, list[int]] = {}
    for tx in _outgoing_query(db, wallet_id, network, start_date, end_date):
        payee = tx.counterparty or tx.to_address or "unknown"
        counts.setdefault(payee, []).append(tx.id)
    recurring = [
        {"payee": payee, "occurrences": len(ids), "transaction_ids": ids}
        for payee, ids in counts.items() if len(ids) >= min_occurrences
    ]
    recurring.sort(key=lambda e: e["occurrences"], reverse=True)
    return {"recurring_counterparties": recurring}


@router.get("/wallet/unusual-activity", dependencies=[Depends(require_session)])
def unusual_activity(wallet_id: int | None = None, network: str | None = None, start_date: datetime | None = None,
                      end_date: datetime | None = None, stdev_threshold: float = 2.0, db: Session = Depends(get_db)):
    from decimal import Decimal
    rows = [tx for tx in _outgoing_query(db, wallet_id, network, start_date, end_date) if tx.fiat_usd_value is not None]
    values = [float(tx.fiat_usd_value) for tx in rows]
    if len(values) < 3:
        return {"unusual_transactions": [], "note": "not enough priced outgoing transactions to establish a baseline"}
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return {"unusual_transactions": [], "note": "no variation in outgoing amounts over this period"}
    flagged = [
        {"transaction_id": tx.id, "amount_usd": tx.fiat_usd_value, "counterparty": tx.counterparty or tx.to_address,
         "deviations_above_mean": round((float(tx.fiat_usd_value) - mean) / stdev, 2)}
        for tx in rows if (float(tx.fiat_usd_value) - mean) / stdev >= stdev_threshold
    ]
    flagged.sort(key=lambda e: e["deviations_above_mean"], reverse=True)
    return {"unusual_transactions": flagged, "baseline_mean_usd": round(mean, 2), "baseline_stdev_usd": round(stdev, 2)}
