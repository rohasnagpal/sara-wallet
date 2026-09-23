import os, requests
from app.core.assets import EURC_ADDRESSES, EURC_DECIMALS, NETWORKS, token_enabled

_ALCHEMY_SLUGS = {
    "ethereum": "eth-mainnet",
    "polygon":  "polygon-mainnet",
    "arbitrum": "arb-mainnet",
    "base":     "base-mainnet",
    "optimism": "opt-mainnet",
}


def _trusted_contracts(network: str) -> list[dict]:
    """Every ERC-20 contract this function will ever check a balance for on
    this network - USDC always, plus EURC on the (Ethereum/Base/Arc-only)
    networks it's actually live on and enabled. Nothing outside this list
    is ever queried, same discipline as app.tools.market.paraswap's own
    trusted-contract table."""
    contracts = []
    if token_enabled("USDC", network):
        contracts.append({"symbol": "USDC", "name": "USD Coin", "address": NETWORKS[network]["usdc"], "decimals": 6})
    if token_enabled("EURC", network):
        contracts.append({"symbol": "EURC", "name": "EURC", "address": EURC_ADDRESSES[network], "decimals": EURC_DECIMALS})
    return contracts


def _direct_rpc_balances(address: str, network: str) -> list[dict]:
    """Fallback for a network Alchemy doesn't (yet) cover — a plain
    on-chain balanceOf() call per trusted contract, same mechanism used
    elsewhere in Sara (Aave, the x402 paywall generator) when no
    balance-indexing API is available. Used instead of silently returning
    nothing, or (the actual prior bug) silently defaulting to Ethereum
    mainnet's Alchemy slug and checking a contract address that only makes
    sense on this network."""
    from app.chains.evm import get_erc20_balance
    tokens = []
    for c in _trusted_contracts(network):
        try:
            balance = get_erc20_balance(c["address"], c["decimals"], address, network)
        except Exception:
            continue
        if balance < 0.000001:
            continue
        tokens.append({"symbol": c["symbol"], "name": c["name"], "balance": balance, "network": network})
    return tokens


def get_erc20_balances(address: str, network: str = "ethereum") -> list[dict]:
    network = network.lower()
    contracts = _trusted_contracts(network)
    if not contracts:
        return []
    if network not in _ALCHEMY_SLUGS:
        return _direct_rpc_balances(address, network)
    api_key = os.getenv("ALCHEMY_API_KEY", "").strip()
    if not api_key:
        return []
    slug = _ALCHEMY_SLUGS[network]
    url = f"https://{slug}.g.alchemy.com/v2/{api_key}"
    by_address = {c["address"].lower(): c for c in contracts}
    try:
        r = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "alchemy_getTokenBalances",
            "params": [address, [c["address"] for c in contracts]],
        }, timeout=10)
        balances_raw = r.json().get("result", {}).get("tokenBalances", [])
    except Exception:
        return []

    held = []
    for entry in balances_raw:
        try:
            raw = int(entry.get("tokenBalance") or "0x0", 16)
        except (ValueError, TypeError):
            continue
        if raw > 0:
            held.append({"contract": entry["contractAddress"], "raw": raw})
    if not held:
        return []

    tokens = []
    for h in held:
        c = by_address.get(h["contract"].lower())
        if c is None:
            continue
        balance = h["raw"] / (10 ** c["decimals"])
        if balance < 0.000001:
            continue
        tokens.append({
            "symbol":  c["symbol"],
            "name":    c["name"],
            "balance": balance,
            "network": network,
        })

    return sorted(tokens, key=lambda t: t["balance"], reverse=True)
