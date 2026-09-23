"""Buy USDC with a card/bank transfer, into a Sara wallet — via Coinbase's
CDP Onramp API (see app.tools.payments.cdp_onramp for why this is
Coinbase's product, not Circle's undocumented "Onramp Kit").

Creating a session never signs or moves any of a wallet's existing funds
— it only asks Coinbase for a checkout URL the user's own browser opens.
Reading the stored CDP key back (to build that URL) needs Sara unlocked,
same as any other encrypted-secret read, but there's no passphrase
confirmation step the way a send/Aave/CCTP transaction has, since nothing
here is a transaction Sara signs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.session_auth import require_session
from app.db.models import OnrampSettings, Wallet
from app.db.session import get_db
from app.tools.payments import cdp_onramp

router = APIRouter(prefix="/onramp", tags=["onramp"], dependencies=[Depends(require_session)])


def _settings_row(db: Session) -> OnrampSettings | None:
    return db.query(OnrampSettings).filter(OnrampSettings.id == 1).first()


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    row = _settings_row(db)
    return {"configured": bool(row and row.cdp_key_id and row.encrypted_cdp_secret),
            "cdp_key_id": row.cdp_key_id if row else None}


class SaveSettingsBody(BaseModel):
    cdp_key_id: str
    cdp_key_secret: str


@router.post("/settings")
def save_settings(body: SaveSettingsBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import encrypt_key
    from app.tools.wallet.lock import WalletLockedError

    if not body.cdp_key_id.strip() or not body.cdp_key_secret.strip():
        raise HTTPException(400, "A CDP API key id and secret are both required")
    # Validate the secret actually decodes to a usable Ed25519 key before
    # persisting anything - a bad key should 400 immediately, not silently
    # fail the next time someone tries to actually buy USDC.
    try:
        cdp_onramp.build_jwt(body.cdp_key_id, body.cdp_key_secret, "POST", "/platform/v2/onramp/sessions")
    except cdp_onramp.OnrampError as exc:
        raise HTTPException(400, str(exc))

    try:
        encrypted_secret = encrypt_key(body.cdp_key_secret)
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))

    row = _settings_row(db)
    if row is None:
        row = OnrampSettings(id=1)
        db.add(row)
    row.cdp_key_id = body.cdp_key_id
    row.encrypted_cdp_secret = encrypted_secret
    db.commit()
    return {"configured": True}


@router.delete("/settings")
def delete_settings(db: Session = Depends(get_db)):
    row = _settings_row(db)
    if row:
        db.delete(row)
        db.commit()
    return {"configured": False}


class CreateSessionBody(BaseModel):
    wallet_id: int
    network: str
    amount_usd: str | None = None
    country: str | None = None


@router.post("/session")
def create_session(body: CreateSessionBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import WalletLockedError, is_unlocked

    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")

    row = _settings_row(db)
    if not row or not row.cdp_key_id or not row.encrypted_cdp_secret:
        raise HTTPException(400, "Add a Coinbase CDP API key in Onramp settings first")
    if not is_unlocked():
        raise HTTPException(423, "Wallet is locked. Unlock Sara first.")
    try:
        secret = decrypt_key(row.encrypted_cdp_secret)
    except WalletLockedError as exc:
        raise HTTPException(423, str(exc))

    try:
        result = cdp_onramp.create_session(
            row.cdp_key_id, secret, destination_address=wallet.address, network=body.network.lower(),
            payment_amount_usd=body.amount_usd, country=body.country,
        )
    except cdp_onramp.OnrampError as exc:
        raise HTTPException(400, str(exc))
    finally:
        secret = None

    return result
