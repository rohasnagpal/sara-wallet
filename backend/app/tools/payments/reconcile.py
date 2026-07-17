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
    required = _required_raw(request.amount, decimals)
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


def check_tron_request(wallet, request) -> str | None:
    """Uses TronGrid's REST v1 API — verified live to work without a key for
    these read endpoints (unlike the wallet/* JSON-RPC endpoints TRC20 sends
    need). Native-TRX parsing is based on tronpy's own TransferContract
    builder shape (verified from source), not live-tested against a real
    incoming transfer."""
    api_key = os.getenv("TRONGRID_API_KEY", "").strip()
    headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
    created_at = _to_naive_utc(request.created_at)
    min_ts = int(created_at.timestamp() * 1000)
    try:
        if request.token.upper() == "TRX":
            url = f"https://api.trongrid.io/v1/accounts/{wallet.address}/transactions"
            params = {"only_to": "true", "min_timestamp": min_ts, "limit": 200, "only_confirmed": "true"}
            required = _required_raw(request.amount, 6)
            for tx in _trongrid_transactions(url, params, headers):
                # TRC20 transfers below are matched via event logs, which
                # only exist for transactions that already executed
                # successfully — a raw native-TRX TransferContract has no
                # such guarantee, so it needs its own explicit success check.
                ret = (tx.get("ret") or [{}])[0]
                if ret.get("contractRet") != "SUCCESS":
                    continue
                contract = (tx.get("raw_data", {}).get("contract") or [{}])[0]
                if contract.get("type") != "TransferContract":
                    continue
                value = contract.get("parameter", {}).get("value", {})
                amount_sun = value.get("amount")
                if amount_sun is not None and int(amount_sun) >= required:
                    return tx.get("txID")
        else:
            from app.chains.tron import TRC20_TOKENS
            entry = TRC20_TOKENS.get(request.token.upper())
            if not entry:
                return None
            contract_addr, decimals = entry
            url = f"https://api.trongrid.io/v1/accounts/{wallet.address}/transactions/trc20"
            params = {
                "only_to": "true", "min_timestamp": min_ts,
                "contract_address": contract_addr, "limit": 200, "only_confirmed": "true",
            }
            required = _required_raw(request.amount, decimals)
            for tx in _trongrid_transactions(url, params, headers):
                token_addr = (tx.get("token_info") or {}).get("address", "")
                if token_addr != contract_addr:
                    continue
                value = tx.get("value")
                if value is not None and int(value) >= required:
                    return tx.get("transaction_id")
    except Exception:
        return None
    return None


def _trongrid_transactions(url: str, params: dict, headers: dict):
    """Yield confirmed TronGrid records across fingerprint-paginated pages."""
    params = dict(params)
    for _ in range(_MAX_RECONCILE_PAGES):
        body = requests.get(url, params=params, headers=headers, timeout=10).json()
        for tx in body.get("data", []):
            yield tx
        fingerprint = (body.get("meta") or {}).get("fingerprint")
        if not fingerprint:
            break
        params["fingerprint"] = fingerprint


def check_solana_request(wallet, request) -> str | None:
    """Native SOL: compares static-account pre/post lamport balances (works
    for simple transfers; versioned txs that pull extra accounts in via
    address-table lookups are skipped rather than mismatched — verified live
    that solders' account_keys list only holds the statically-listed keys).
    SPL tokens: uses preTokenBalances/postTokenBalances, which report owner
    directly and don't have that limitation. Both verified live against
    real transaction shapes; not tested against an actual matching transfer."""
    try:
        from app.chains.solana import get_client
        from solders.pubkey import Pubkey
        client = get_client()
        pubkey = Pubkey.from_string(wallet.address)
        created_at = _to_naive_utc(request.created_at)
        is_native = request.token.upper() == "SOL"
        mint = None
        decimals = 9
        if not is_native:
            from app.tools.market.jupiter import resolve_mint, get_decimals
            mint = resolve_mint(request.token)
            if not mint:
                return None
            decimals = get_decimals(request.token)
        required = _required_raw(request.amount, decimals)
        before = None
        for _ in range(_MAX_RECONCILE_PAGES):
            kwargs = {"limit": 100}
            if before is not None:
                kwargs["before"] = before
            sigs = client.get_signatures_for_address(pubkey, **kwargs).value
            if not sigs:
                break
            for sig_info in sigs:
                if not sig_info.block_time:
                    continue
                if datetime.utcfromtimestamp(sig_info.block_time) < created_at:
                    return None  # signatures are newest-first
                if sig_info.err is not None:
                    continue
                tx = client.get_transaction(sig_info.signature, max_supported_transaction_version=0)
                if not tx.value:
                    continue
                meta = tx.value.transaction.meta
                if is_native:
                    keys = tx.value.transaction.transaction.message.account_keys
                    try:
                        idx = list(keys).index(pubkey)
                    except ValueError:
                        continue
                    if idx >= len(meta.pre_balances) or idx >= len(meta.post_balances):
                        continue
                    delta = int(meta.post_balances[idx]) - int(meta.pre_balances[idx])
                    if delta >= required:
                        return str(sig_info.signature)
                else:
                    owner = str(pubkey)
                    pre_amt = sum(
                        int(b.ui_token_amount.amount) for b in (meta.pre_token_balances or [])
                        if str(b.mint) == mint and str(b.owner) == owner
                    )
                    post_amt = sum(
                        int(b.ui_token_amount.amount) for b in (meta.post_token_balances or [])
                        if str(b.mint) == mint and str(b.owner) == owner
                    )
                    if post_amt - pre_amt >= required:
                        return str(sig_info.signature)
            before = sigs[-1].signature
    except Exception:
        return None
    return None


def check_payment_request(db, request) -> bool:
    """Checks on-chain for a matching incoming transfer. If found, marks the
    request paid and stores the matched tx hash. Returns True if the request
    is (newly or already) paid."""
    if request.status != "pending":
        return request.status == "paid"
    from app.db.models import Wallet
    wallet = db.query(Wallet).filter(Wallet.id == request.wallet_id).first()
    if not wallet:
        return False
    if request.chain == "evm":
        tx_hash = check_evm_request(wallet, request)
    elif request.chain == "tron":
        tx_hash = check_tron_request(wallet, request)
    elif request.chain == "solana":
        tx_hash = check_solana_request(wallet, request)
    else:
        tx_hash = None
    if tx_hash:
        # A single real transfer must not satisfy two different invoices —
        # without this check, two pending requests for the same (or
        # tolerance-adjacent) amount to the same wallet could both get
        # marked "paid" off the one matching transfer found on-chain. This
        # SELECT is only a fast path, though: it can itself race against a
        # concurrent commit between this check and the commit below, so the
        # (chain, network, matched_tx_hash) unique constraint on
        # PaymentRequest (models.py) is the actual source of truth.
        from app.db.models import PaymentRequest
        already_claimed = db.query(PaymentRequest).filter(
            PaymentRequest.matched_tx_hash == tx_hash,
            PaymentRequest.id != request.id,
        ).first()
        if already_claimed:
            return False
        request.status = "paid"
        request.matched_tx_hash = tx_hash
        try:
            db.commit()
        except IntegrityError:
            # Lost the race — a concurrent check already claimed this exact
            # (chain, network, tx_hash) for a different request.
            db.rollback()
            return False
        return True
    return False
