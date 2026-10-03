"""Automatic reconciliation: checks a wallet's recent on-chain activity for
an incoming transfer matching a pending PaymentRequest, and marks it paid.

Matches by trusted contract/mint address (not the token's display name/asset
label) — live testing against a real wallet's transfer history turned up an
actual spoofed-name-token transfer in the wild, confirming display names
can't be trusted for matching, only addresses.

Each chain's check degrades gracefully: on any error, missing API key, or an
unrecognized transaction shape, it returns "not matched" rather than raising
— reconciliation staying "pending" is a safe failure mode; the request can
still be marked paid manually.
"""
import os
import requests
from decimal import Decimal, ROUND_CEILING
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.chains.evm import ALCHEMY_NETWORK_SLUGS as _ALCHEMY_SLUGS

_MAX_RECONCILE_PAGES = 25


def _required_raw(amount, decimals: int) -> int:
    """Convert an invoice amount to base units without float tolerance.
    ROUND_CEILING ensures an over-precision invoice can never be satisfied by
    less than the displayed amount."""
    return int((Decimal(str(amount)) * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_CEILING))


def _request_required_raw(request, decimals: int) -> int:
    if request.amount_raw is not None and request.decimals == decimals:
        return int(request.amount_raw)
    return _required_raw(request.amount, decimals)


def _raw_int(value) -> int | None:
    try:
        if isinstance(value, str):
            return int(value, 16) if value.lower().startswith("0x") else int(value)
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def check_evm_request(wallet, request) -> str | None:
    """Uses Alchemy's alchemy_getAssetTransfers — requires ALCHEMY_API_KEY.
    Verified live against real transfer history (correctly found a known
    0.082019 USDC transfer by contract address + timestamp)."""
    api_key = os.getenv("ALCHEMY_API_KEY", "").strip()
    slug = _ALCHEMY_SLUGS.get(request.network)
    if not api_key or not slug:
        return None
    from app.chains.evm import _NATIVE_TOKEN
    native = _NATIVE_TOKEN.get(request.network, "ETH")
    is_native = request.token.upper() == native
    params = {
        "toAddress": wallet.address,
        "category": ["external"] if is_native else ["erc20"],
        "withMetadata": True,
        "order": "desc",
        "maxCount": "0x64",
    }
    contract_addr = None
    decimals = 18
    if not is_native:
        from app.tools.market.paraswap import resolve_token
        result = resolve_token(request.token, request.network)
        if not result:
            return None
        contract_addr, decimals = result
        contract_addr = contract_addr.lower()
        params["contractAddresses"] = [result[0]]
    url = f"https://{slug}.g.alchemy.com/v2/{api_key}"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers", "params": [params]}
    created_at = _to_naive_utc(request.created_at)
    required = _request_required_raw(request, decimals)
    page_key = None
    try:
        for _ in range(_MAX_RECONCILE_PAGES):
            if page_key:
                params["pageKey"] = page_key
            body = requests.post(url, json=payload, timeout=10).json().get("result", {})
            for t in body.get("transfers", []):
                ts = (t.get("metadata") or {}).get("blockTimestamp")
                if not ts:
                    continue
                try:
                    block_time = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    continue
                if block_time < created_at:
                    return None  # results are newest-first
                raw_contract = t.get("rawContract") or {}
                if contract_addr and (raw_contract.get("address") or "").lower() != contract_addr:
                    continue
                raw_value = _raw_int(raw_contract.get("value"))
                if raw_value is not None and raw_value >= required:
                    return t.get("hash")
            page_key = body.get("pageKey")
            if not page_key:
                break
    except Exception:
        return None
    return None


def check_payment_request(db, request) -> bool:
    """Checks on-chain for a matching incoming transfer. If found, marks the
    request paid and stores the matched tx hash. Returns True if the request
    is (newly or already) paid."""
    if request.status not in ("pending", "overdue"):
        return request.status == "paid"
    from app.db.models import Wallet
    wallet = db.query(Wallet).filter(Wallet.id == request.wallet_id).first()
    if not wallet:
        return False
    tx_hash = check_evm_request(wallet, request)
    if tx_hash:
        # A single real transfer must not satisfy two different invoices —
        # without this check, two pending requests for the same (or
        # tolerance-adjacent) amount to the same wallet could both get
        # marked "paid" off the one matching transfer found on-chain. This
        # SELECT is only a fast path, though: it can itself race against a
        # concurrent commit between this check and the commit below, so the
        # (chain, network, matched_tx_hash) unique constraint on
        # PaymentRequest (models.py) is the actual source of truth.
        from app.db.models import PaymentRequest, Transaction
        already_claimed = db.query(PaymentRequest).filter(
            PaymentRequest.matched_tx_hash == tx_hash,
            PaymentRequest.id != request.id,
        ).first()
        if already_claimed:
            return False
        request.status = "paid"
        request.matched_tx_hash = tx_hash
        indexed = db.query(Transaction).filter(
            Transaction.network == request.network,
            Transaction.tx_hash == tx_hash,
            Transaction.direction == "incoming",
        ).first()
        if indexed:
            indexed.reference = request.reference
            indexed.category = "invoice_payment"
        try:
            from app.core.audit import append_audit
            from app.core.events import publish
            details = {
                "reference": request.reference, "tx_hash": tx_hash,
                "chain": request.chain, "network": request.network,
                "amount": str(request.amount), "amount_raw": request.amount_raw,
                "decimals": request.decimals, "token": request.token,
                "wallet_id": request.wallet_id, "payment_address": request.payment_address,
                "merchant_client_id": request.merchant_client_id,
            }
            publish(
                db, "payment_request.paid", details,
                aggregate_type="payment_request", aggregate_id=str(request.id),
                event_key=f"payment:{request.chain}:{request.network}:{tx_hash}:reconciled",
            )
            append_audit(
                db, "payment_request.reconciled", "payment_request",
                resource_id=str(request.id), details=details,
                actor_type="system", actor_id="payment-reconciler",
            )
            db.commit()
        except IntegrityError:
            # Lost the race — a concurrent check already claimed this exact
            # (chain, network, tx_hash) for a different request.
            db.rollback()
            return False
        return True
    return False


def reconcile_pending_requests(db, *, limit: int = 50) -> int:
    """Reconcile active invoices during the normal background cycle."""
    from app.db.models import PaymentRequest
    rows = db.query(PaymentRequest).filter(
        PaymentRequest.status.in_(("pending", "overdue"))
    ).order_by(PaymentRequest.created_at).limit(limit).all()
    matched = 0
    for row in rows:
        if row.status == "pending" and row.due_date and row.due_date < datetime.utcnow():
            row.status = "overdue"
            db.commit()
        if check_payment_request(db, row):
            matched += 1
    return matched
