import requests
from web3 import Web3

_BASE = "https://apiv5.paraswap.io"

CHAIN_IDS = {
    "ethereum": 1, "polygon": 137, "arbitrum": 42161,
    "base": 8453, "optimism": 10,
}

NATIVE_SYMBOLS = {
    "ethereum": "ETH", "arbitrum": "ETH", "base": "ETH", "optimism": "ETH",
    "polygon": "POL",
}

_NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

_TOKENS: dict[int, dict[str, tuple[str, int]]] = {  # symbol → (address, decimals)
    # Sara specializes in stablecoin payments — only USDC/USDT (plus each
    # chain's native gas token, handled separately via NATIVE_SYMBOLS) are
    # trusted. No speculative/DeFi tokens.
    1: {
        "USDC":  ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
        "USDT":  ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
    },
    137: {
        "USDC":  ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
        "USDT":  ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
    },
    42161: {
        "USDC":  ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
        "USDT":  ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
    },
    8453: {
        "USDC":  ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
    },
    10: {
        "USDC":  ("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6),
        "USDT":  ("0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", 6),
    },
}

_PARASWAP_SPENDER_ABI = [
    {"inputs":[],"name":"getTokenTransferProxy","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"}
]
# Augustus router addresses — this is the exact contract execute_swap()
# requires tx["to"] to match before it will sign anything (see the
# allowlist check there). Verified live against Paraswap's own /prices
# response on 2026-07-15 (each response's priceRoute.contractAddress) —
# Base uses a genuinely different deployment from every other chain here;
# the previous version of this file used the Ethereum address for all five
# chains, which was simply wrong for Base.
_AUGUSTUS_ADDRESSES = {
    1: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    137: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    42161: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    8453: "0x59C7C832e96D2568bea6db468C1aAdcbbDa08A52",
    10: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
}
_ERC20_ABI = [
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
     "name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]

_MAX_SWAP_GAS_LIMIT = 1_500_000
_MAX_SWAP_FEE_WEI = 50_000_000_000_000_000  # 0.05 native asset


def max_total_network_fee_wei(source_token: str | None) -> int:
    """Hard confirmation ceiling across every transaction this swap can
    submit. ERC-20 routes may need reset-approval + exact approval + swap;
    native routes submit only the swap transaction."""
    is_native = bool(source_token) and source_token.lower() == _NATIVE.lower()
    return _MAX_SWAP_FEE_WEI * (1 if is_native else 3)


def resolve_token(symbol: str, network: str) -> tuple[str, int] | None:
    """Returns (address, decimals) or None."""
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    native = NATIVE_SYMBOLS.get(network.lower(), "ETH")
    if symbol.upper() == native:
        return (_NATIVE, 18)
    entry = _TOKENS.get(chain_id, {}).get(symbol.upper())
    return entry  # (address, decimals) or None


def trusted_symbols(network: str) -> list[str]:
    """Every symbol Sara will resolve to a real contract on this network —
    the allowlist a user's input is checked (and typo-corrected) against."""
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return []
    native = NATIVE_SYMBOLS.get(network.lower(), "ETH")
    return [native] + list(_TOKENS.get(chain_id, {}).keys())


def resolve_token_with_correction(symbol: str, network: str) -> tuple[tuple[str, int] | None, str | None]:
    """Like resolve_token, but if the symbol doesn't match and looks like a
    typo of exactly one trusted symbol on this network, resolves that instead.
    Returns (result, corrected_symbol) — corrected_symbol is None unless a
    correction was actually applied."""
    result = resolve_token(symbol, network)
    if result:
        return result, None
    from app.tools.wallet.token_trust import fuzzy_correct
    corrected = fuzzy_correct(symbol, trusted_symbols(network))
    if corrected:
        return resolve_token(corrected, network), corrected
    return None, None


def get_quote(src: str, src_dec: int, dst: str, dst_dec: int,
              amount_wei: int, network: str) -> dict | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    try:
        r = requests.get(f"{_BASE}/prices", params={
            "srcToken": src, "destToken": dst,
            "srcDecimals": src_dec, "destDecimals": dst_dec,
            "amount": str(amount_wei), "side": "SELL",
            "network": chain_id,
        }, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def get_swap_tx(price_route: dict, src: str, dst: str,
                src_amount: str,
                user_addr: str, network: str, slippage_bps: int = 100) -> dict | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    try:
        r = requests.post(
            f"{_BASE}/transactions/{chain_id}",
            params={"ignoreChecks": "true"},
            json={
                "srcToken": src, "destToken": dst,
                "srcAmount": src_amount,
                "priceRoute": price_route,
                "userAddress": user_addr,
                "receiver": user_addr,
                "slippage": slippage_bps,
                "txOrigin": user_addr,
            },
            timeout=12,
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def _token_transfer_proxy(w3, chain_id: int) -> str:
    """Queries the Augustus router on-chain for its current token-transfer
    proxy (the actual approval spender — a separate, minimal contract from
    Augustus itself, by Paraswap's own design, so a bug in Augustus's more
    complex swap-routing logic can't get at anything users approved).
    Querying on-chain rather than hardcoding a second address means this
    stays correct even if Paraswap ever redeploys it — it's re-derived from
    the same router address that's already allowlisted below."""
    router = _AUGUSTUS_ADDRESSES.get(chain_id)
    if not router:
        raise ValueError(f"no known Paraswap router for chain {chain_id}")
    contract = w3.eth.contract(address=Web3.to_checksum_address(router), abi=_PARASWAP_SPENDER_ABI)
    return contract.functions.getTokenTransferProxy().call()


def ensure_allowance(private_key: str, token_addr: str, amount_wei: int,
                     network: str) -> str | None:
    if token_addr == _NATIVE:
        return None
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    chain_id = _CHAIN_IDS.get(network.lower(), 1)
    account = w3.eth.account.from_key(private_key)
    spender = _token_transfer_proxy(w3, chain_id)
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    current = contract.functions.allowance(account.address, spender).call()
    if current == amount_wei:
        return None
    # Never leave an unlimited or stale oversized approval behind. Tokens
    # such as USDT require resetting a non-zero allowance before changing it.
    tx_hash = None
    for value in ([0, amount_wei] if current > 0 else [amount_wei]):
        gas_price = int(w3.eth.gas_price * 1.2)
        if 60_000 * gas_price > _MAX_SWAP_FEE_WEI:
            raise ValueError("Refusing approval: estimated approval fee exceeds 0.05 native asset.")
        tx = contract.functions.approve(spender, value).build_transaction({
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 60000,
            "gasPrice": gas_price,
            "chainId": chain_id,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex() if tx_hash is not None else None


def validate_swap_transaction_static(tx_data: dict, network: str, expected_value_wei: int) -> str:
    """Checks aggregator-controlled fields that do not depend on allowance.
    Call this before granting any approval; execute_swap repeats it."""
    chain_id = CHAIN_IDS.get(network.lower())
    expected_router = _AUGUSTUS_ADDRESSES.get(chain_id)
    if not expected_router:
        raise ValueError(f"no known Paraswap router for chain {chain_id} — refusing to sign")
    actual_to = Web3.to_checksum_address(tx_data["to"])
    if actual_to != Web3.to_checksum_address(expected_router):
        raise ValueError(
            f"Refusing to sign: Paraswap returned an unexpected contract address "
            f"({actual_to}) for this swap — expected the known router {expected_router}."
        )
    tx_value = int(tx_data.get("value", 0))
    if tx_value != expected_value_wei:
        raise ValueError(
            f"Refusing to sign: transaction value ({tx_value} wei) does not match "
            f"the confirmed swap amount ({expected_value_wei} wei)."
        )
    return actual_to


def _local_fee_fields(w3, call: dict) -> tuple[int, int]:
    estimate = int(w3.eth.estimate_gas(call))
    gas = max(21_000, (estimate * 120 + 99) // 100)
    if gas > _MAX_SWAP_GAS_LIMIT:
        raise ValueError(f"Refusing to sign: locally estimated gas limit {gas} is excessive.")
    gas_price = int(w3.eth.gas_price * 1.2)
    if gas * gas_price > _MAX_SWAP_FEE_WEI:
        raise ValueError("Refusing to sign: locally estimated swap fee exceeds 0.05 native asset.")
    return gas, gas_price


def execute_swap(private_key: str, tx_data: dict, network: str, expected_value_wei: int = 0, *,
                  expected_recipient: str, expected_src_token: str | None, expected_dst_token: str | None,
                  expected_src_amount: int, expected_min_dst_amount: int = 0) -> str:
    """Signs and broadcasts a Paraswap-provided swap transaction — but only
    after checking it against what the user actually confirmed. Paraswap's
    /transactions response supplies `to`/`data`/`value` directly, and
    without these checks a compromised or malformed response could redirect
    funds to an arbitrary address:
      - `to` must be the known Augustus router for this exact chain (not
        reused from another chain, not attacker-supplied).
      - `value` (native ETH/POL/etc sent with the tx) must exactly match
        what was confirmed — zero for a token-source swap, the exact
        confirmed amount for a native-source swap. Either direction of
        mismatch is refused rather than silently trusting the API.
      - the transaction is *simulated* (tx_simulate.verify_swap_effect) and
        its actual predicted asset movements are checked against what was
        confirmed. An earlier version of this function instead scanned the
        calldata's 32-byte words for the confirmed recipient/token/amount —
        that was bypassable: an attacker can call a different, malicious
        function while padding the calldata with decoy words that match the
        confirmed values without those values doing anything. Simulation
        checks the real effect, not what the bytes merely contain.
    """
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    chain_id = _CHAIN_IDS.get(network.lower(), 1)

    actual_to = validate_swap_transaction_static(tx_data, network, expected_value_wei)
    tx_value = int(tx_data.get("value", 0))
    call = {
        "from": account.address, "to": actual_to, "data": tx_data["data"], "value": tx_value,
    }
    gas, gas_price = _local_fee_fields(w3, call)

    from app.tools.market.tx_simulate import verify_swap_effect
    verify_swap_effect(
        network, account.address, actual_to, tx_data["data"], tx_value,
        wallet_address=account.address,
        expected_src_token=None if expected_src_token and expected_src_token.lower() == _NATIVE.lower() else expected_src_token,
        expected_dst_token=None if expected_dst_token and expected_dst_token.lower() == _NATIVE.lower() else expected_dst_token,
        expected_src_amount=expected_src_amount,
        expected_min_dst_amount=expected_min_dst_amount,
        gas=gas, gas_price=gas_price,
    )

    tx = {
        "from":     account.address,
        "to":       actual_to,
        "data":     tx_data["data"],
        "value":    tx_value,
        "gas":      gas,
        "gasPrice": gas_price,
        "nonce":    w3.eth.get_transaction_count(account.address),
        "chainId":  chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()
