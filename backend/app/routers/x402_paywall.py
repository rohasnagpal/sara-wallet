"""x402 Paywall Generator — a user picks a Sara wallet and a price, and Sara
generates a self-contained PHP snippet to paste into their own site,
gating a page behind an x402 payment to that wallet. Sara never runs,
proxies, or receives any traffic for the paywalled page itself — it only
ever generates the source code, once per (re)generation, in
app.tools.payments.x402_paywall_codegen. See that module for the protocol
details and the test/live mode split.

A CDP API secret (live mode only) is encrypted at rest the same way a
wallet's private key is — via the active unlock session's key — so both
creating and re-fetching a live-mode page's code require Sara to be
unlocked, same as any other secret-touching operation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.session_auth import require_session
from app.db.models import Wallet, X402PaywallPage
from app.db.session import get_db
from app.tools.payments import x402_paywall_codegen as codegen

router = APIRouter(prefix="/x402-paywall", tags=["x402-paywall"], dependencies=[Depends(require_session)])


@router.get("/networks")
def supported_networks():
    return {"test_network": codegen.TEST_NETWORK, "live_networks": list(codegen.LIVE_NETWORKS)}


class CreatePaywallPageBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    wallet_id: int
    mode: str  # "test" | "live"
    network: str | None = None  # required for live; test always uses base-sepolia
    price_usd: str
    cdp_key_id: str | None = None
    cdp_key_secret: str | None = None
    preview_message: str | None = Field(None, max_length=2000)


def _wallet_for(db: Session, wallet_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    return wallet


def _generate_and_validate(body: CreatePaywallPageBody, wallet_address: str, cdp_key_secret_plain: str | None) -> str:
    network = codegen.TEST_NETWORK if body.mode == "test" else (body.network or "").lower()
    try:
        return codegen.generate_php(
            label=body.label, wallet_address=wallet_address, mode=body.mode, network=network,
            price_usd=body.price_usd, cdp_key_id=body.cdp_key_id, cdp_key_secret=cdp_key_secret_plain,
            preview_message=body.preview_message,
        )
    except codegen.PaywallCodegenError as exc:
        raise HTTPException(400, str(exc))


@router.post("/pages")
def create_page(body: CreatePaywallPageBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import encrypt_key
    from app.tools.wallet.lock import WalletLockedError

    wallet = _wallet_for(db, body.wallet_id)
    network = codegen.TEST_NETWORK if body.mode == "test" else (body.network or "").lower()

    # Validate + render once up front so a bad config 400s before anything
    # is persisted (the plaintext secret, if any, is never written to disk).
    code = _generate_and_validate(body, wallet.address, body.cdp_key_secret)

    encrypted_secret = None
    if body.mode == "live":
        try:
            encrypted_secret = encrypt_key(body.cdp_key_secret)
        except WalletLockedError as exc:
            raise HTTPException(423, str(exc))

    page = X402PaywallPage(
        label=body.label, wallet_id=wallet.id, mode=body.mode, network=network,
        price_usd=body.price_usd, cdp_key_id=body.cdp_key_id if body.mode == "live" else None,
        encrypted_cdp_secret=encrypted_secret, preview_message=body.preview_message,
    )
    db.add(page)
    db.commit()
    db.refresh(page)
    return {"id": page.id, "code": code}


def _usdc_balance(wallet: Wallet, network: str) -> float | None:
    """Best-effort — a paywall page still lists even if the RPC read fails
    (e.g. transient network hiccup), it just shows no balance for that row."""
    try:
        from app.chains import evm
        asset = codegen.network_asset(network)
        if network == codegen.TEST_NETWORK:
            import os
            from web3 import Web3
            w3 = Web3(Web3.HTTPProvider(os.getenv("BASE_SEPOLIA_RPC") or "https://sepolia.base.org"))
            calldata = "0x70a08231" + Web3.to_checksum_address(wallet.address)[2:].lower().zfill(64)
            raw = w3.eth.call({"to": Web3.to_checksum_address(asset["usdc"]), "data": calldata})
            return int.from_bytes(raw, "big") / 1_000_000
        return evm.get_erc20_balance(asset["usdc"], 6, wallet.address, network)
    except Exception:
        return None


@router.get("/pages")
def list_pages(db: Session = Depends(get_db)):
    pages = db.query(X402PaywallPage).order_by(X402PaywallPage.created_at.desc()).all()
    wallets = {w.id: w for w in db.query(Wallet).filter(Wallet.id.in_([p.wallet_id for p in pages])).all()} if pages else {}
    out = []
    for p in pages:
        wallet = wallets.get(p.wallet_id)
        out.append({
            "id": p.id, "label": p.label, "wallet_id": p.wallet_id,
            "wallet_name": wallet.name if wallet else None,
            "wallet_address": wallet.address if wallet else None,
            "mode": p.mode, "network": p.network, "price_usd": p.price_usd,
            "usdc_balance": _usdc_balance(wallet, p.network) if wallet else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return {"pages": out}


@router.get("/pages/{page_id}/code")
def get_page_code(page_id: int, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import WalletLockedError

    page = db.query(X402PaywallPage).filter(X402PaywallPage.id == page_id).first()
    if not page:
        raise HTTPException(404, "Paywall page not found")
    wallet = db.query(Wallet).filter(Wallet.id == page.wallet_id).first()
    if not wallet:
        raise HTTPException(404, "The wallet this page pays to no longer exists")

    cdp_secret = None
    if page.mode == "live":
        if not page.encrypted_cdp_secret:
            raise HTTPException(500, "This live-mode page is missing its CDP key; recreate it")
        try:
            cdp_secret = decrypt_key(page.encrypted_cdp_secret)
        except WalletLockedError as exc:
            raise HTTPException(423, str(exc))

    try:
        code = codegen.generate_php(
            label=page.label, wallet_address=wallet.address, mode=page.mode, network=page.network,
            price_usd=page.price_usd, cdp_key_id=page.cdp_key_id, cdp_key_secret=cdp_secret,
            preview_message=page.preview_message,
        )
    except codegen.PaywallCodegenError as exc:
        raise HTTPException(400, str(exc))
    return {"id": page.id, "code": code}


@router.delete("/pages/{page_id}")
def delete_page(page_id: int, db: Session = Depends(get_db)):
    page = db.query(X402PaywallPage).filter(X402PaywallPage.id == page_id).first()
    if not page:
        raise HTTPException(404, "Paywall page not found")
    db.delete(page)
    db.commit()
    return {"deleted": True}
