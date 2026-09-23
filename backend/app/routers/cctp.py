"""Circle's CCTP v2 — native USDC bridging (see app.tools.trading.cctp for
the protocol details and why this exists alongside LI.FI, not instead of
it). A transfer needs a passphrase, same as any other money-moving action
(Aave supply/withdraw, export a key) - it isn't wired into the
vendor-scoped spending-policy engine, since it isn't a payment to a
counterparty: it's moving a user's own USDC from one of their own
addresses to another (usually the same wallet, on a different chain).

Two chains, two transactions: /transfer does the source-chain burn and
then tries the destination-chain mint immediately (Fast Transfer usually
settles in well under a minute) - if the attestation isn't ready in time,
the transfer is left "burned" (funds are safely, provably recoverable;
nothing failed) and /transfers/{id}/complete finishes it whenever the
caller retries.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import CctpTransfer, Wallet
from app.db.session import get_db
from app.tools.trading import cctp

router = APIRouter(prefix="/cctp", tags=["cctp"], dependencies=[Depends(require_session)])

_USDC_DECIMALS = 6
_ATTESTATION_WAIT_SECONDS = 45.0  # generous for Fast Transfer's usual 8-20s, still well under a typical request timeout


@router.get("/networks")
def supported_networks():
    return {"networks": list(cctp.SUPPORTED_NETWORKS)}


def _wallet_or_404(db: Session, wallet_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    return wallet


def _serialize(t: CctpTransfer) -> dict:
    return {
        "id": t.id, "wallet_id": t.wallet_id, "source_network": t.source_network,
        "destination_network": t.destination_network,
        "amount": float(Decimal(t.amount_raw) / (Decimal(10) ** _USDC_DECIMALS)),
        "recipient_address": t.recipient_address, "fast": t.fast,
        "burn_tx_hash": t.burn_tx_hash, "mint_tx_hash": t.mint_tx_hash, "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def _try_complete(db: Session, transfer: CctpTransfer, key: str) -> None:
    """Best-effort: attempts the destination mint if an attestation is
    already available. Leaves the transfer as "burned" (not an error) if
    it isn't ready yet - the burn already succeeded and isn't at risk."""
    attestation = cctp.wait_for_attestation(
        transfer.source_network, transfer.burn_tx_hash, max_wait_seconds=_ATTESTATION_WAIT_SECONDS,
    )
    if attestation is None:
        return
    from datetime import datetime
    mint_tx_hash = cctp.execute_mint(key, transfer.destination_network, attestation)
    transfer.mint_tx_hash = mint_tx_hash
    transfer.status = "complete"
    transfer.completed_at = datetime.utcnow()
    db.commit()


class TransferBody(BaseModel):
    wallet_id: int
    source_network: str
    destination_network: str
    amount: str  # decimal USDC string, e.g. "100.50"
    recipient_address: str | None = None  # defaults to the same wallet's own address
    fast: bool = True
    passphrase: str


@router.post("/transfer")
def transfer(body: TransferBody, db: Session = Depends(get_db)):
    from app.routers.chat import _record_submitted_transaction
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
    recipient = body.recipient_address or wallet.address

    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")

    key = decrypt_key(wallet.encrypted_key)
    try:
        burn_tx_hash = cctp.execute_burn(
            key, body.source_network, body.destination_network, amount_raw, recipient, fast=body.fast,
        )
    except cctp.CctpError as exc:
        key = None
        raise HTTPException(400, str(exc))
    except Exception as exc:
        key = None
        raise HTTPException(502, f"CCTP burn failed: {exc}")

    source_network = body.source_network.lower()
    destination_network = body.destination_network.lower()
    row = CctpTransfer(
        wallet_id=wallet.id, source_network=source_network, destination_network=destination_network,
        amount_raw=str(amount_raw), recipient_address=recipient, fast=body.fast, burn_tx_hash=burn_tx_hash,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    _record_submitted_transaction(
        db, wallet_id=wallet.id, network=source_network, tx_hash=burn_tx_hash,
        from_address=wallet.address, to_address=cctp.TOKEN_MESSENGER,
        amount=float(amount), amount_raw=amount_raw, decimals=_USDC_DECIMALS,
        token="USDC", category="cctp_bridge", reference=f"CCTP to {destination_network}",
    )
    append_audit(
        db, "cctp.burn", "wallet", resource_id=str(wallet.id),
        details={"source_network": source_network, "destination_network": destination_network,
                 "amount_raw": str(amount_raw), "burn_tx_hash": burn_tx_hash},
    )
    db.commit()

    try:
        _try_complete(db, row, key)
    except Exception:
        # The burn already succeeded and is safely recorded; a mint failure
        # here (e.g. insufficient destination-chain gas) just leaves the
        # transfer "burned" for a later retry via /transfers/{id}/complete.
        pass
    finally:
        key = None

    if row.status == "complete":
        _record_submitted_transaction(
            db, wallet_id=wallet.id, network=destination_network, tx_hash=row.mint_tx_hash,
            from_address=cctp.MESSAGE_TRANSMITTER, to_address=recipient,
            amount=float(amount), amount_raw=amount_raw, decimals=_USDC_DECIMALS,
            token="USDC", category="cctp_bridge", direction="incoming",
            reference=f"CCTP from {source_network}",
        )
        db.commit()

    return _serialize(row)


@router.post("/transfers/{transfer_id}/complete")
def complete_transfer(transfer_id: int, body: dict, db: Session = Depends(get_db)):
    from app.routers.chat import _record_submitted_transaction
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase, is_unlocked

    row = db.query(CctpTransfer).filter(CctpTransfer.id == transfer_id).first()
    if not row:
        raise HTTPException(404, "Transfer not found")
    if row.status == "complete":
        return _serialize(row)

    passphrase = (body or {}).get("passphrase")
    wallet = _wallet_or_404(db, row.wallet_id)
    if not is_unlocked():
        raise HTTPException(423, "Wallet is locked. Unlock Sara first.")
    if not passphrase or not confirm_passphrase(passphrase):
        raise HTTPException(401, "Incorrect passphrase")

    key = decrypt_key(wallet.encrypted_key)
    try:
        attestation = cctp.fetch_attestation(row.source_network, row.burn_tx_hash)
        if attestation is None:
            raise HTTPException(409, "Circle's attestation isn't ready yet - try again shortly.")
        row.mint_tx_hash = cctp.execute_mint(key, row.destination_network, attestation)
    except cctp.CctpError as exc:
        raise HTTPException(400, str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"CCTP mint failed: {exc}")
    finally:
        key = None

    from datetime import datetime
    row.status = "complete"
    row.completed_at = datetime.utcnow()
    db.commit()

    amount = float(Decimal(row.amount_raw) / (Decimal(10) ** _USDC_DECIMALS))
    _record_submitted_transaction(
        db, wallet_id=wallet.id, network=row.destination_network, tx_hash=row.mint_tx_hash,
        from_address=cctp.MESSAGE_TRANSMITTER, to_address=row.recipient_address,
        amount=amount, amount_raw=int(row.amount_raw), decimals=_USDC_DECIMALS,
        token="USDC", category="cctp_bridge", direction="incoming",
        reference=f"CCTP from {row.source_network}",
    )
    db.commit()
    return _serialize(row)


@router.get("/transfers")
def list_transfers(wallet_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(CctpTransfer)
    if wallet_id is not None:
        query = query.filter(CctpTransfer.wallet_id == wallet_id)
    rows = query.order_by(CctpTransfer.created_at.desc()).limit(200).all()
    return {"transfers": [_serialize(t) for t in rows]}
