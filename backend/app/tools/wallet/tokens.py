import os, requests
from app.core.assets import NETWORKS, token_enabled

_ALCHEMY_SLUGS = {
    "ethereum": "eth-mainnet",
    "polygon":  "polygon-mainnet",
    "arbitrum": "arb-mainnet",
    "base":     "base-mainnet",
    "optimism": "opt-mainnet",
}

def _direct_rpc_usdc_balance(address: str, network: str) -> list[dict]:
    """Fallback for a network Alchemy doesn't (yet) cover — a plain
    on-chain balanceOf() call, same mechanism used elsewhere in Sara
    (Aave, the x402 paywall generator) when no balance-indexing API is
    available. Used instead of silently returning nothing, or (the actual
    prior bug) silently defaulting to Ethereum mainnet's Alchemy slug and
    checking a contract address that only makes sense on this network."""
    try:
        from app.chains.evm import get_erc20_balance
        balance = get_erc20_balance(NETWORKS[network]["usdc"], 6, address, network)
    except Exception:
        return []
    if balance < 0.000001:
        return []
    return [{"symbol": "USDC", "name": "USD Coin", "balance": balance, "network": network}]


def get_erc20_balances(address: str, network: str = "ethereum") -> list[dict]:
    network = network.lower()
    if not token_enabled("USDC", network):
        return []
    if network not in _ALCHEMY_SLUGS:
        return _direct_rpc_usdc_balance(address, network)
    api_key = os.getenv("ALCHEMY_API_KEY", "").strip()
    if not api_key:
        return []
    slug = _ALCHEMY_SLUGS[network]
    url = f"https://{slug}.g.alchemy.com/v2/{api_key}"
    try:
        r = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "alchemy_getTokenBalances",
            "params": [address, [NETWORKS[network]["usdc"]]],
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
        if h["contract"].lower() != NETWORKS[network]["usdc"].lower():
            continue
        balance = h["raw"] / (10 ** 6)
        if balance < 0.000001:
            continue
        tokens.append({
            "symbol":  "USDC",
            "name":    "USD Coin",
            "balance": balance,
            "network": network,
        })

    return sorted(tokens, key=lambda t: t["balance"], reverse=True)
