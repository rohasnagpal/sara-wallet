import os
from decimal import Decimal
from tronpy import Tron
from tronpy.keys import PrivateKey, is_base58check_address, to_hex_address
from tronpy.exceptions import AddressNotFound
from app.core.amounts import to_base_units

# Standard ERC20/TRC20 transfer(address,uint256) selector — TRC20 mirrors
# ERC20's ABI/selector scheme by design, same as _ERC20_TRANSFER_SELECTOR in
# app/chains/evm.py.
_TRC20_TRANSFER_SELECTOR = "a9059cbb"


def _encode_address_param(addr: str) -> str:
    """32-byte, left-padded parameter encoding of a Tron address — drops the
    0x41 Tron-specific prefix byte, since TRC20 contracts store addresses in
    the same 20-byte form as Ethereum."""
    return to_hex_address(addr)[2:].zfill(64)


def _encode_uint_param(value: int) -> str:
    return hex(value)[2:].zfill(64)

# Trusted TRC20 tokens — same verified-contract-allowlist principle as the
# EVM/Solana token lists (paraswap.py / jupiter.py). Address confirmed against
# tether.to and TronScan; symbol -> (contract address, decimals).
TRC20_TOKENS = {
    "USDT": ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", 6),
}


def trusted_trc20_symbols() -> list[str]:
    return list(TRC20_TOKENS.keys())


def resolve_trc20_with_correction(symbol: str) -> tuple[tuple[str, int] | None, str | None]:
    """Like TRC20_TOKENS.get(), but typo-corrects against the trusted TRC20
    list. Returns ((address, decimals), corrected_symbol) — corrected_symbol
    is None unless a correction was actually applied."""
    entry = TRC20_TOKENS.get(symbol.upper())
    if entry:
        return entry, None
    from app.tools.wallet.token_trust import fuzzy_correct
    corrected = fuzzy_correct(symbol, trusted_trc20_symbols())
    if corrected:
        return TRC20_TOKENS.get(corrected), corrected
    return None, None


def get_client() -> Tron:
    # Read fresh on every call, not once at import time — Settings can save
    # TRONGRID_API_KEY into os.environ mid-session (app/routers/settings.py),
    # and a module-level snapshot here would keep using the old value (no
    # key at all, if it was unset at import time) until the process restarts.
    api_key = os.getenv("TRONGRID_API_KEY")
    if api_key:
        from tronpy.providers import HTTPProvider
        return Tron(HTTPProvider(api_key=api_key))
    return Tron()


def _reraise_friendly(exc: Exception):
    """TronGrid's public endpoint allows plain TRX balance/send calls without
    a key, but rejects smart-contract calls (TRC20 balance/transfer) with a
    401/403 once unauthenticated — surface that as an actionable message
    instead of a raw HTTPError."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403, 429):
        raise RuntimeError(
            "Tron's public RPC requires an API key for token (TRC20/USDT) calls, "
            "or you're rate-limited without one. Get a free key at trongrid.io "
            "and set TRONGRID_API_KEY in your .env."
        ) from exc
    raise exc


def is_valid_address(address: str) -> bool:
    return is_base58check_address(address)


def generate_wallet() -> dict:
    priv = PrivateKey.random()
    return {"address": priv.public_key.to_base58check_address(), "private_key": priv.hex()}


def import_wallet(private_key_hex: str) -> dict:
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    return {"address": priv.public_key.to_base58check_address(), "private_key": priv.hex()}


def get_balance(address: str) -> dict:
    """Native TRX balance. Result in TRX (not sun)."""
    client = get_client()
    try:
        trx = client.get_account_balance(address)
    except AddressNotFound:
        # Account never received a first transaction yet — Tron only
        # "activates" an address on-chain once it's funded once.
        trx = Decimal(0)
    return {"network": "tron", "address": address, "balance": float(trx), "unit": "TRX"}


def get_trc20_balance(address: str, symbol: str = "USDT") -> dict:
    """Reads balanceOf via a raw constant-contract call rather than
    client.get_contract() — avoids a second network round-trip to fetch the
    contract's ABI, which some TronGrid endpoints gate more strictly than
    plain balance/broadcast calls."""
    entry = TRC20_TOKENS.get(symbol.upper())
    if not entry:
        return {"balance": 0.0, "unit": symbol.upper()}
    contract_addr, decimals = entry
    client = get_client()
    try:
        raw_hex = client.trigger_const_smart_contract_function(
            address, contract_addr, "balanceOf(address)", _encode_address_param(address),
        )
    except AddressNotFound:
        raw_hex = ""
    except Exception as e:
        _reraise_friendly(e)
    raw = int(raw_hex, 16) if raw_hex else 0
    return {
        "balance": raw / (10 ** decimals), "raw_balance": raw,
        "unit": symbol.upper(),
    }


def get_native_transfer_preview(address: str, amount_trx: float) -> dict:
    balance = get_balance(address)["balance"]
    fee_trx = 1.1  # Tron bandwidth/activation fee is usually ~0.1 TRX once an
    # account has bandwidth, but a brand-new recipient account costs ~1.1 TRX
    # to activate — quoting the conservative worst case so the preview never
    # underestimates what's needed.
    total = amount_trx + fee_trx
    return {
        "balance": balance, "amount": amount_trx, "fee": fee_trx, "total": total,
        "has_funds": balance >= total, "unit": "TRX",
    }


def get_trc20_transfer_preview(address: str, amount: float, symbol: str = "USDT") -> dict:
    _, decimals = TRC20_TOKENS[symbol.upper()]
    token = get_trc20_balance(address, symbol)
    amount_raw = to_base_units(amount, decimals, symbol.upper())
    token_balance = token["balance"]
    native = get_balance(address)
    fee_trx = 15.0  # TRC20 transfers burn TRX for energy when the sender has
    # none staked — quoting a conservative worst case (well above the typical
    # ~13-14 TRX full-price energy cost of a USDT transfer).
    return {
        "token_balance": token_balance, "amount": amount,
        "has_token_funds": token["raw_balance"] >= amount_raw,
        "native_balance": native["balance"], "gas_fee": fee_trx,
        "has_gas_funds": native["balance"] >= fee_trx, "native_unit": "TRX",
    }


def send_trx(private_key_hex: str, to: str, amount_trx: float) -> str:
    client = get_client()
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    from_addr = priv.public_key.to_base58check_address()
    amount_sun = to_base_units(amount_trx, 6, "TRX")
    txn = client.trx.transfer(from_addr, to, amount_sun).build().sign(priv)
    result = txn.broadcast()
    return result.txid


def send_trc20(private_key_hex: str, to: str, amount: float, symbol: str = "USDT") -> str:
    """Builds a raw TriggerSmartContract call rather than going through
    client.get_contract() — same reasoning as get_trc20_balance above."""
    entry = TRC20_TOKENS.get(symbol.upper())
    if not entry:
        raise ValueError(f"{symbol} is not a trusted TRC20 token on Tron")
    contract_addr, decimals = entry
    client = get_client()
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    from_addr = priv.public_key.to_base58check_address()
    amount_raw = to_base_units(amount, decimals, symbol.upper())
    data = _TRC20_TRANSFER_SELECTOR + _encode_address_param(to) + _encode_uint_param(amount_raw)
    try:
        txn = (
            client.trx._build_transaction(
                "TriggerSmartContract",
                {
                    "owner_address": to_hex_address(from_addr),
                    "contract_address": to_hex_address(contract_addr),
                    "data": data,
                    "call_token_value": 0,
                    "call_value": 0,
                    "token_id": 0,
                },
            )
            .fee_limit(20_000_000)
            .build()
            .sign(priv)
        )
        result = txn.broadcast()
    except Exception as e:
        _reraise_friendly(e)
    return result.txid
