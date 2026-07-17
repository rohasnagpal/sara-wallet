"""Payment request links: a self-contained, base64-encoded payload that any
Sara instance can decode and act on — {to, chain, network, token, amount,
note}. No server-side state; the payload carries everything needed, so a
link generated on one machine works when opened on any other Sara instance
(each person's own local Sara resolves the token/sends from their own
wallets against their own trusted-token list).
"""
import base64
import json
import math
import secrets


def generate_reference() -> str:
    return "INV-" + secrets.token_hex(3).upper()


def is_trusted_token(symbol: str, network: str, wallet_chain: str) -> tuple[bool, str]:
    """Validates a token symbol against Sara's trusted list for this chain.
    Returns (is_valid, canonical_symbol) — canonical_symbol is the corrected
    symbol to actually use (may differ from input if a typo was corrected)."""
    sym = symbol.upper()
    if wallet_chain == "solana":
        if sym == "SOL":
            return True, "SOL"
        from app.tools.market.jupiter import resolve_mint_with_correction
        mint, corrected = resolve_mint_with_correction(symbol)
        return (mint is not None), (corrected or sym)
    if wallet_chain == "tron":
        if sym == "TRX":
            return True, "TRX"
        from app.chains.tron import resolve_trc20_with_correction
        entry, corrected = resolve_trc20_with_correction(symbol)
        return (entry is not None), (corrected or sym)
    # evm
    from app.chains.evm import _NATIVE_TOKEN
    native = _NATIVE_TOKEN.get(network, "ETH")
    if sym == native:
        return True, native
    from app.tools.market.paraswap import resolve_token_with_correction
    result, corrected = resolve_token_with_correction(symbol, network)
    return (result is not None), (corrected or sym)


def encode_payload(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_payload(payload: str) -> dict:
    padded = payload + "=" * (-len(payload) % 4)
    raw = base64.urlsafe_b64decode(padded.encode())
    return json.loads(raw)


def create_payment_request(db, wallet, network: str, token: str, amount: float, note: str = ""):
    """Validates the token, persists a PaymentRequest row, and returns
    (payment_request, payload) — or (None, error_message) on validation
    failure. Shared by the REST endpoint and the chat intent so both create
    an identical, reconcilable record."""
    from app.db.models import PaymentRequest

    # amount <= 0 alone doesn't catch NaN/Infinity — every comparison with
    # NaN is False in Python, and inf > 0 is True, so `1e999` (which Python
    # parses as float('inf')) or a NaN payload would otherwise slip through
    # and get persisted/encoded into a payment link with an unusable amount.
    if not math.isfinite(amount) or amount <= 0:
        return None, "amount must be a positive, finite number"
    valid, symbol = is_trusted_token(token, network, wallet.chain)
    if not valid:
        return None, f"'{token}' is not a token Sara trusts on this wallet's chain"

    note = (note or "").strip()[:200]
    reference = generate_reference()
    row = PaymentRequest(
        wallet_id=wallet.id, reference=reference, chain=wallet.chain,
        network=network, token=symbol, amount=amount, note=note, status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    payload = encode_payload({
        "v": 1, "ref": reference, "to": wallet.address, "chain": wallet.chain,
        "network": network, "token": symbol, "amount": amount, "note": note,
    })
    return row, payload
