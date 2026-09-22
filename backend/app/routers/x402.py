"""x402 payments - Sara as a buyer, paying for and fetching HTTP
402-gated resources (see app.tools.payments.x402_client for protocol
details and the trusted-asset safety rationale).

Every payment is recorded in the same ledger as any other transaction
(category "x402_payment"), and it's the one flow in Sara where money can
move without a fresh per-call passphrase: only when an explicit,
user-created spending policy (app.core.spending_policy - the same engine
Business -> Policies already uses) both matches this wallet/network/token
and doesn't deny the exact price the resource asked for. No matching
policy, or a price outside it, falls back to Sara's normal
passphrase-confirmed flow, same as every other send.
"""
from __future__ import annotations

import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import Wallet
from app.db.session import get_db
from app.tools.payments import x402_client

router = APIRouter(prefix="/x402", tags=["x402"], dependencies=[Depends(require_session)])

_USDC_DECIMALS = 6  # Circle's USDC is 6 decimals on every EVM chain x402_client supports


@router.get("/networks")
def supported_networks():
    return {"networks": list(x402_client.SUPPORTED_NETWORKS)}


class X402FetchBody(BaseModel):
    wallet_id: int
    network: str = "base"
    url: str
    method: str = "GET"
    json_body: dict | None = None
    note: str | None = Field(None, max_length=500)
    tags: list[str] = Field(default_factory=list)
    passphrase: str | None = None  # only required if no spending policy covers the price


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = [t.strip() for t in tags if t.strip()]
    if len(cleaned) > 20 or any(len(t) > 40 for t in cleaned):
        raise HTTPException(400, "Use at most 20 tags of 40 characters each")
    return list(dict.fromkeys(cleaned))


@router.post("/fetch")
async def fetch(body: X402FetchBody, db: Session = Depends(get_db)):
    from app.core import spending_policy
    from app.routers.chat import _record_submitted_transaction
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase, is_unlocked

    network = body.network.lower()
    if network not in x402_client.SUPPORTED_NETWORKS:
        raise HTTPException(400, f"x402 is only supported on: {', '.join(x402_client.SUPPORTED_NETWORKS)}")
    # URL scheme validated inside x402_client.probe()/pay_and_fetch() - the
    # single source of truth for the https-only (plus loopback-for-testing)
    # rule, so it can't drift out of sync between here and there.
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    if not is_unlocked():
        raise HTTPException(423, "Wallet is locked. Unlock Sara first.")
    tags = _clean_tags(body.tags)

    try:
        requirement = await x402_client.probe(
            url=body.url, method=body.method, network=network, json_body=body.json_body,
        )
    except x402_client.X402Error as exc:
        raise HTTPException(400, str(exc))

    if requirement is None:
        # Nothing to pay for - fetch it directly, no wallet/policy involved.
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                r = await http.request(body.method.upper(), body.url, json=body.json_body)
        except Exception as exc:
            raise HTTPException(502, f"Could not reach {body.url}: {exc}")
        return {
            "paid": False, "status_code": r.status_code, "body": r.text,
            "content_type": r.headers.get("content-type"),
        }

    amount_raw = int(requirement.amount_raw)
    decision = spending_policy.evaluate(
        db, wallet_id=wallet.id, network=network, token="USDC", counterparty_id=None,
        destination_address=requirement.pay_to, amount_raw=amount_raw,
    )
    auto_approved = decision.allowed and bool(decision.matched_policy_ids)

    if not auto_approved:
        if not body.passphrase:
            amount = format(Decimal(amount_raw) / (Decimal(10) ** _USDC_DECIMALS), "f")
            return {
                "requires_confirmation": True,
                "reason": decision.denial_reasons[0] if decision.denial_reasons else
                          "No spending policy authorizes automatic x402 payment for this wallet/network - enter your passphrase to approve this one payment.",
                "amount": amount, "amount_raw": str(amount_raw), "token": "USDC",
                "network": network, "pay_to": requirement.pay_to,
            }
        if not confirm_passphrase(body.passphrase):
            raise HTTPException(401, "Incorrect passphrase")

    key = decrypt_key(wallet.encrypted_key)
    try:
        result = await x402_client.pay_and_fetch(
            url=body.url, method=body.method, private_key=key, network=network, json_body=body.json_body,
            # Pin to exactly what was just evaluated (auto-approved by
            # policy, or approved by the user's passphrase moments ago) —
            # pay_and_fetch does its own separate 402 round trip and must
            # refuse rather than silently pay a different price/recipient.
            expected_amount_raw=str(amount_raw), expected_pay_to=requirement.pay_to,
        )
    except x402_client.X402Error as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    ledger_row = None
    is_testnet = network in x402_client.TESTNET_NETWORKS
    if result.paid and not is_testnet:
        # Testnet payments (free faucet funds, not real value) are
        # deliberately never written to the real ledger - they'd otherwise
        # show up alongside genuine transactions in reports/exports/balances.
        amount = float(Decimal(amount_raw) / (Decimal(10) ** _USDC_DECIMALS))
        ledger_row = _record_submitted_transaction(
            db, wallet_id=wallet.id, network=network,
            tx_hash=result.tx_hash or f"x402:{secrets.token_hex(16)}",
            from_address=wallet.address, to_address=requirement.pay_to,
            amount=amount, amount_raw=amount_raw, decimals=_USDC_DECIMALS,
            token="USDC", category="x402_payment", reference=body.url[:200],
            note=body.note, tags=tags,
        )
        append_audit(
            db, "x402.payment", "wallet", resource_id=str(wallet.id),
            details={"url": body.url, "network": network, "pay_to": requirement.pay_to,
                     "amount_raw": str(amount_raw), "auto_approved": auto_approved, "tx_hash": result.tx_hash},
        )
        db.commit()

    return {
        "paid": result.paid, "status_code": result.status_code, "body": result.body_text,
        "content_type": result.content_type, "tx_hash": result.tx_hash,
        # The exact price probed before paying, not the facilitator's own
        # settle-response echo of it - some facilitators leave that field
        # blank on success, which showed as "0 USDC" even after a real payment.
        "amount_raw": str(amount_raw), "network": result.network or network, "is_testnet": is_testnet,
        "auto_approved": auto_approved, "transaction_id": ledger_row.id if ledger_row else None,
    }


@router.get("/payments")
def list_payments(wallet_id: int | None = None, db: Session = Depends(get_db)):
    from app.db.models import Transaction
    query = db.query(Transaction).filter(Transaction.category == "x402_payment")
    if wallet_id is not None:
        query = query.filter(Transaction.wallet_id == wallet_id)
    rows = query.order_by(Transaction.timestamp.desc()).limit(200).all()
    import json as _json
    return {"payments": [{
        "id": r.id, "wallet_id": r.wallet_id, "network": r.network, "to_address": r.to_address,
        "amount": r.amount, "reference": r.reference, "note": r.note,
        "tags": _json.loads(r.tags) if r.tags else [], "status": r.status,
        "tx_hash": r.tx_hash, "timestamp": r.timestamp.isoformat() if r.timestamp else None,
    } for r in rows]}
