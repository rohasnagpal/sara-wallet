import json
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import AccountingClassification, TokenDeployment, Transaction, Wallet
from app.db.session import get_db
from app.services import accounting_labels

router = APIRouter(prefix="/ledger", tags=["ledger"], dependencies=[Depends(require_session)])


def _amount(row: Transaction) -> str:
    if row.amount_raw is not None and row.decimals is not None:
        return format(Decimal(row.amount_raw) / (Decimal(10) ** row.decimals), "f")
    return str(row.amount or 0)


def _row(row: Transaction, wallet_name: str | None = None) -> dict:
    return {
        "id": row.id, "wallet": wallet_name, "chain": row.chain, "network": row.network,
        "tx_hash": row.tx_hash, "from": row.from_address, "to": row.to_address,
        "amount": _amount(row), "amount_raw": row.amount_raw, "decimals": row.decimals,
        "token": row.token, "status": row.status, "direction": row.direction,
        "category": row.category, "counterparty": row.counterparty, "note": row.note,
        "tags": json.loads(row.tags or "[]"), "fee_raw": row.fee_raw, "fee_token": row.fee_token,
        "confirmations": row.confirmations, "block_number": row.block_number,
        "fiat_usd_value": row.fiat_usd_value, "fiat_inr_value": row.fiat_inr_value,
        "valuation_source": row.valuation_source, "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "reference": row.reference,
    }


_TOKEN_CATEGORIES = {"token_mint", "token_burn", "token_transfer"}


def _deployed_token_contracts(db: Session) -> dict[tuple[str, str], set[str]]:
    contracts: dict[tuple[str, str], set[str]] = {}
    for dep in db.query(TokenDeployment).filter(TokenDeployment.contract_address.isnot(None)).all():
        contracts.setdefault((dep.network, dep.symbol.upper()), set()).add(dep.contract_address)
    return contracts


@router.get("")
def list_ledger(
    wallet_id: int | None = None, network: str | None = None, token: str | None = None,
    category: str | None = None, status: str | None = None,
    limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db),
):
    query = db.query(Transaction)
    for column, value in ((Transaction.wallet_id, wallet_id), (Transaction.network, network),
                          (Transaction.token, token), (Transaction.category, category),
                          (Transaction.status, status)):
        if value is not None:
            query = query.filter(column == value)
    rows = query.order_by(Transaction.timestamp.desc(), Transaction.id.desc()).limit(limit).all()
    names = {w.id: w.name for w in db.query(Wallet).all()}
    contracts = _deployed_token_contracts(db)
    own = accounting_labels.own_addresses(db)
    labels = {c.transaction_id: c for c in db.query(AccountingClassification).filter(
        AccountingClassification.transaction_id.in_([r.id for r in rows])).all()} if rows else {}
    out = []
    for row in rows:
        item = _row(row, names.get(row.wallet_id))
        # How this counts in Accounting's "Money in and money out", and whether
        # the user set it or Sara worked it out.
        item["accounting_label"], item["accounting_label_source"] = accounting_labels.effective_label(
            row, labels.get(row.id), own)
        # Link a mint/burn/transfer to the token's contract page - but only when the
        # network+symbol maps to exactly one deployment, so we never link the wrong one.
        found = contracts.get((row.network, (row.token or "").upper()), set())
        if row.category in _TOKEN_CATEGORIES and len(found) == 1:
            item["token_contract"] = next(iter(found))
        out.append(item)
    return {"transactions": out}


class LedgerUpdate(BaseModel):
    category: str | None = Field(None, max_length=60)
    counterparty: str | None = Field(None, max_length=160)
    note: str | None = Field(None, max_length=1000)
    tags: list[str] | None = None


@router.patch("/{transaction_id}")
def update_ledger(transaction_id: int, body: LedgerUpdate, db: Session = Depends(get_db)):
    row = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not row:
        raise HTTPException(404, "Transaction not found")
    changes = body.model_dump(exclude_unset=True)
    if "tags" in changes:
        tags = [tag.strip() for tag in changes.pop("tags") if tag.strip()]
        if len(tags) > 20 or any(len(tag) > 40 for tag in tags):
            raise HTTPException(400, "Use at most 20 tags of 40 characters each")
        row.tags = json.dumps(list(dict.fromkeys(tags)))
    for key, value in changes.items():
        setattr(row, key, value.strip() if isinstance(value, str) else value)
    append_audit(db, "transaction.annotated", "transaction", resource_id=str(row.id), details=body.model_dump(exclude_unset=True))
    db.commit()
    return _row(row)


@router.get("/{transaction_id}/receipt")
def payment_receipt(transaction_id: int, db: Session = Depends(get_db)):
    row = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not row:
        raise HTTPException(404, "Transaction not found")
    wallet = db.query(Wallet).filter(Wallet.id == row.wallet_id).first()
    explorers = {
        "ethereum": "https://etherscan.io/tx/", "polygon": "https://polygonscan.com/tx/",
        "arbitrum": "https://arbiscan.io/tx/", "base": "https://basescan.org/tx/",
        "optimism": "https://optimistic.etherscan.io/tx/",
    }
    result = _row(row, wallet.name if wallet else None)
    result.update({"receipt_type": "proof_of_payment", "explorer_url": explorers.get(row.network, "") + (row.tx_hash or "")})
    return result
