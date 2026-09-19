import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.session_auth import require_session
from app.core.assets import NETWORKS, token_enabled
from app.chains.evm import get_erc20_balance
from app.db.models import ProofRecord, Wallet
from app.db.session import get_db
from app.tools.proofs import blockchainproof as service
from app.tools.wallet import lock as lock_state
from app.tools.wallet.encrypt import decrypt_bytes, decrypt_key, encrypt_bytes, encrypt_key
from app.tools.wallet.lock import WalletLockedError


router = APIRouter(prefix="/proofs", tags=["proofs"], dependencies=[Depends(require_session)])


class CreateProofCheckout(BaseModel):
    wallet_id: int
    hash: str
    description: str = Field(default="", max_length=300)
    file_name: str = Field(default="", max_length=255)


class ConfirmProofCheckout(BaseModel):
    passphrase: str


class VerifyProof(BaseModel):
    hash: str
    proof_id: str | None = None


def _details(row: ProofRecord) -> dict:
    try:
        return json.loads(decrypt_key(row.encrypted_details))
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))


def _public(row: ProofRecord) -> dict:
    details = _details(row)
    proof = json.loads(decrypt_key(row.encrypted_proof)) if row.encrypted_proof else None
    return {
        "id": row.id, "checkout_id": row.checkout_id, "wallet_id": row.wallet_id,
        "wallet_address": row.wallet_address, "wallet_name": details.get("wallet_name"),
        "hash": details.get("hash"), "description": details.get("description", ""),
        "file_name": details.get("file_name", ""),
        "usdc_balance": details.get("usdc_balance"),
        "status": row.status, "payment_txid": row.payment_txid,
        "proof_id": row.proof_id, "proof_status": row.proof_status,
        "proof": proof, "last_error": row.last_error, "expires_at": row.expires_at,
        "has_evidence": row.encrypted_evidence is not None,
        "public_url": (service._base_url() + "/public/v1/proofs/" + row.proof_id) if row.proof_id else None,
        "recipient": (details.get("typed_data", {}).get("message", {}) or {}).get("to"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _apply_remote(row: ProofRecord, data: dict) -> None:
    row.status = str(data.get("status") or row.status)
    row.payment_txid = data.get("payment_txid") or row.payment_txid
    row.last_error = data.get("error") if isinstance(data.get("error"), str) else None
    proof = data.get("proof")
    if isinstance(proof, dict):
        row.proof_id = proof.get("proof_id") or row.proof_id
        row.proof_status = proof.get("status") or row.proof_status
        row.encrypted_proof = encrypt_key(json.dumps(proof, separators=(",", ":")))


@router.get("")
def list_proofs(db: Session = Depends(get_db)):
    try:
        lock_state.get_active_key()
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))
    rows = []
    for row in db.query(ProofRecord).order_by(ProofRecord.created_at.desc()).all():
        try:
            rows.append(_public(row))
        except HTTPException:
            raise
        except Exception as exc:
            # One record that can't be decrypted or resolved (e.g. written
            # under a since-rotated key, or a malformed BlockchainProof
            # config) must not take down the whole list — every other proof
            # is still perfectly readable.
            rows.append({
                "id": row.id, "error": True, "error_message": str(exc) or exc.__class__.__name__,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })
    return rows


@router.post("/checkouts", status_code=201)
def create_proof_checkout(req: CreateProofCheckout, db: Session = Depends(get_db)):
    try:
        lock_state.get_active_key()
        document_hash = req.hash.strip().lower()
        if not service.HASH_RE.fullmatch(document_hash):
            raise HTTPException(422, "SHA-256 hash must be exactly 64 hexadecimal characters")
        wallet = db.query(Wallet).filter(Wallet.id == req.wallet_id, Wallet.chain == "evm").first()
        if not wallet:
            raise HTTPException(404, "EVM wallet not found")
        if not token_enabled("USDC", "polygon"):
            raise HTTPException(400, "Enable Polygon PoS and USDC in Settings before creating a proof")
        try:
            usdc_balance = get_erc20_balance(NETWORKS["polygon"]["usdc"], 6, wallet.address, "polygon")
        except Exception as exc:
            raise HTTPException(502, "Could not verify the wallet's Polygon USDC balance") from exc
        if usdc_balance < 1:
            raise HTTPException(402, "The selected wallet needs at least 1 native USDC on Polygon")
        checkout = service.create_checkout(document_hash, wallet.address, req.description.strip())
        details = {
            "checkout_token": checkout["checkout_token"], "hash": document_hash,
            "description": req.description.strip(), "wallet_name": wallet.name,
            "file_name": req.file_name.strip(),
            "usdc_balance": usdc_balance,
            "typed_data": checkout["typed_data"],
        }
        row = ProofRecord(
            checkout_id=checkout["checkout_id"], wallet_id=wallet.id, wallet_address=wallet.address,
            encrypted_details=encrypt_key(json.dumps(details, separators=(",", ":"))),
            status="created", expires_at=checkout.get("expires_at"),
        )
        db.add(row); db.commit(); db.refresh(row)
        return _public(row)
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))
    except service.ProofServiceError as exc:
        raise HTTPException(502, str(exc))


@router.post("/{record_id}/confirm", status_code=202)
def confirm_proof_checkout(record_id: int, req: ConfirmProofCheckout, db: Session = Depends(get_db)):
    row = db.query(ProofRecord).filter(ProofRecord.id == record_id).first()
    if not row:
        raise HTTPException(404, "Proof checkout not found")
    if row.status not in {"created", "submitted_unknown"}:
        raise HTTPException(409, "This checkout is no longer awaiting authorization")
    try:
        if not lock_state.confirm_passphrase(req.passphrase):
            raise HTTPException(401, "Incorrect passphrase")
        details = _details(row)
        service.validate_checkout({
            "checkout_id": row.checkout_id, "checkout_token": details["checkout_token"],
            "price": {"amount": "1.00", "currency": "USDC", "network": "polygon"},
            "typed_data": details["typed_data"],
        }, details["hash"], row.wallet_address)
        wallet = db.query(Wallet).filter(Wallet.id == row.wallet_id).first()
        if not wallet or wallet.address.lower() != row.wallet_address.lower():
            raise HTTPException(409, "Selected wallet is no longer available")
        private_key = decrypt_key(wallet.encrypted_key)
        try:
            signature = service.sign_checkout(details["typed_data"], private_key, wallet.address)
        finally:
            private_key = None
        row.status = "submitting"; db.commit()
        try:
            data = service.confirm_checkout(row.checkout_id, details["checkout_token"], signature)
        except service.ProofServiceError:
            row.status = "submitted_unknown"; db.commit()
            raise
        _apply_remote(row, data); db.commit(); db.refresh(row)
        return _public(row)
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))
    except service.ProofServiceError as exc:
        raise HTTPException(502, str(exc))


@router.post("/{record_id}/refresh")
def refresh_proof(record_id: int, db: Session = Depends(get_db)):
    row = db.query(ProofRecord).filter(ProofRecord.id == record_id).first()
    if not row:
        raise HTTPException(404, "Proof checkout not found")
    try:
        details = _details(row)
        data = service.get_checkout(row.checkout_id, details["checkout_token"])
        _apply_remote(row, data)
        proof = data.get("proof") or {}
        if proof.get("status") in service.TERMINAL_PROOF_STATUSES and row.encrypted_evidence is None:
            try:
                row.encrypted_evidence = encrypt_bytes(service.get_evidence(row.checkout_id, details["checkout_token"]))
            except service.ProofServiceError as exc:
                row.last_error = f"Proof saved; evidence download pending: {exc}"
        db.commit(); db.refresh(row)
        return _public(row)
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))
    except service.ProofServiceError as exc:
        raise HTTPException(502, str(exc))


@router.get("/{record_id}/evidence")
def download_evidence(record_id: int, db: Session = Depends(get_db)):
    row = db.query(ProofRecord).filter(ProofRecord.id == record_id).first()
    if not row or row.encrypted_evidence is None:
        raise HTTPException(404, "Evidence archive is not stored yet")
    try:
        data = decrypt_bytes(row.encrypted_evidence)
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))
    filename = f"blockchainproof-{row.proof_id or row.checkout_id}.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
    })


@router.post("/verify/hash")
def verify_proof(req: VerifyProof):
    document_hash = req.hash.strip().lower()
    if not service.HASH_RE.fullmatch(document_hash):
        raise HTTPException(422, "SHA-256 hash must be exactly 64 hexadecimal characters")
    try:
        return service.verify_hash(document_hash, req.proof_id)
    except service.ProofServiceError as exc:
        raise HTTPException(502, str(exc))
