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
            "method": "alchemy_getTokenBalances",
            "params": [address, "DEFAULT_TOKENS"],
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
        try:
            meta_r = requests.post(url, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "alchemy_getTokenMetadata",
                "params": [h["contract"]],
            }, timeout=10)
            meta = meta_r.json().get("result", {}) or {}
        except Exception:
            continue
        decimals = meta.get("decimals")
        if decimals is None:
            continue
        balance = h["raw"] / (10 ** decimals)
        if balance < 0.000001:
            continue
        tokens.append({
            "symbol":  meta.get("symbol") or "?",
            "name":    meta.get("name") or "Unknown",
            "balance": balance,
            "network": network,
        })

    return sorted(tokens, key=lambda t: t["balance"], reverse=True)
