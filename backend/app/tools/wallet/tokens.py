import os, requests

_ALCHEMY_SLUGS = {
    "ethereum": "eth-mainnet",
    "polygon":  "polygon-mainnet",
    "arbitrum": "arb-mainnet",
    "base":     "base-mainnet",
    "optimism": "opt-mainnet",
}

def get_erc20_balances(address: str, network: str = "ethereum") -> list[dict]:
    api_key = os.getenv("ALCHEMY_API_KEY", "").strip()
    if not api_key:
        return []
    slug = _ALCHEMY_SLUGS.get(network.lower(), "eth-mainnet")
    url = f"https://{slug}.g.alchemy.com/v2/{api_key}"
    try:
        r = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "alchemy_getTokensForOwner",
            "params": [address, {"withMetadata": True}]
        }, timeout=10)
        tokens_raw = r.json().get("result", {}).get("tokens", [])
    except Exception:
        return []

    tokens = []
    for t in tokens_raw:
        try:
            balance = int(t.get("rawBalance", "0")) / (10 ** (t.get("decimals") or 18))
        except (ValueError, TypeError):
            continue
        if balance < 0.000001:
            continue
        tokens.append({
            "symbol":  t.get("symbol", "?"),
            "name":    t.get("name", "Unknown"),
            "balance": balance,
            "network": network,
        })

    return sorted(tokens, key=lambda t: t["balance"], reverse=True)
