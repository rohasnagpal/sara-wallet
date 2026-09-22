"""Aave v3 USDC yield — supply and withdraw only (see app.tools.lending.aave
for the contract-level implementation and address verification). Every
supply/withdraw needs a fresh passphrase confirmation, same as any other
money-moving action in Sara (export a key, register a name, run a batch) —
this isn't wired into the vendor-scoped spending-policy engine, since it
isn't a payment to a counterparty: it's moving a user's own funds into (and
back out of) a position they still fully control.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import Wallet
from app.db.session import get_db
from app.tools.lending import aave

router = APIRouter(prefix="/aave", tags=["aave"], dependencies=[Depends(require_session)])

_USDC_DECIMALS = 6


@router.get("/networks")
def supported_networks():
    return {"networks": list(aave.SUPPORTED_NETWORKS)}


def _wallet_or_404(db: Session, wallet_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    return wallet


@router.get("/position")
def get_position(wallet_id: int, network: str, db: Session = Depends(get_db)):
    wallet = _wallet_or_404(db, wallet_id)
    try:
        balance = aave.get_position(wallet.address, network)
        apy = aave.get_supply_apy(network)
    except aave.AaveError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Could not read Aave position: {exc}")
    return {"wallet_id": wallet.id, "network": network.lower(), "usdc_supplied": balance, "supply_apy_pct": apy}


class SupplyBody(BaseModel):
    wallet_id: int
    network: str
    amount: str  # decimal USDC string, e.g. "100.50"
    passphrase: str


@router.post("/supply")
def supply(body: SupplyBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase, is_unlocked

    wallet = _wallet_or_404(db, body.wallet_id)
    if not is_unlocked():
        raise HTTPException(423, "Wallet is locked. Unlock Sara first.")
    try:
        amount = Decimal(body.amount)
    except Exception:
        raise HTTPException(400, "Amount must be a number")
    if amount <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    amount_raw = int(amount * (Decimal(10) ** _USDC_DECIMALS))

    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")

    key = decrypt_key(wallet.encrypted_key)
    try:
        tx_hash = aave.execute_supply(key, body.network, amount_raw)
    except aave.AaveError as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    from app.routers.chat import _record_submitted_transaction
    network = body.network.lower()
    row = _record_submitted_transaction(
        db, wallet_id=wallet.id, network=network, tx_hash=tx_hash,
        from_address=wallet.address, to_address=aave.POOL_ADDRESSES[network],
        amount=float(amount), amount_raw=amount_raw, decimals=_USDC_DECIMALS,
        token="USDC", category="aave_supply", reference="Aave v3 USDC supply",
    )
    append_audit(
        db, "aave.supply", "wallet", resource_id=str(wallet.id),
        details={"network": network, "amount_raw": str(amount_raw), "tx_hash": tx_hash},
    )
    db.commit()
    return {"tx_hash": tx_hash, "transaction_id": row.id}


class WithdrawBody(BaseModel):
    wallet_id: int
    network: str
    amount: str | None = None  # None (or omitted) withdraws the full aUSDC balance
    passphrase: str


@router.post("/withdraw")
def withdraw(body: WithdrawBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase, is_unlocked

    wallet = _wallet_or_404(db, body.wallet_id)
    if not is_unlocked():
        raise HTTPException(423, "Wallet is locked. Unlock Sara first.")
    amount_raw = None
    if body.amount:
        try:
            amount = Decimal(body.amount)
        except Exception:
            raise HTTPException(400, "Amount must be a number")
        if amount <= 0:
            raise HTTPException(400, "Amount must be greater than zero")
        amount_raw = int(amount * (Decimal(10) ** _USDC_DECIMALS))

    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")

    key = decrypt_key(wallet.encrypted_key)
    try:
        tx_hash = aave.execute_withdraw(key, body.network, amount_raw)
    except aave.AaveError as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    from app.routers.chat import _record_submitted_transaction
    network = body.network.lower()
    # The exact amount withdrawn (especially for "withdraw all") is only
    # known on-chain; record the position's balance just before this call
    # would race the withdrawal itself, so the ledger amount here is the
    # amount the user asked for, or 0 (unquantified) for "withdraw all".
    amount_for_ledger = float(Decimal(amount_raw) / (Decimal(10) ** _USDC_DECIMALS)) if amount_raw else 0.0
    row = _record_submitted_transaction(
        db, wallet_id=wallet.id, network=network, tx_hash=tx_hash,
        from_address=aave.POOL_ADDRESSES[network], to_address=wallet.address,
        amount=amount_for_ledger, amount_raw=amount_raw or 0, decimals=_USDC_DECIMALS,
        token="USDC", category="aave_withdraw", direction="incoming",
        reference="Aave v3 USDC withdrawal (full balance)" if amount_raw is None else "Aave v3 USDC withdrawal",
    )
    append_audit(
        db, "aave.withdraw", "wallet", resource_id=str(wallet.id),
        details={"network": network, "amount_raw": str(amount_raw) if amount_raw else "all", "tx_hash": tx_hash},
    )
    db.commit()
    return {"tx_hash": tx_hash, "transaction_id": row.id}
