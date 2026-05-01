import requests

BASE = "https://api.llama.fi"

def _get(path: str) -> dict | list | None:
    try:
        r = requests.get(BASE + path, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def get_total_tvl() -> dict | None:
    data = _get("/charts")
    if not data or not isinstance(data, list):
        return None
    # Filter non-zero and take last entry
    valid = [d for d in data if d.get("totalLiquidityUSD", 0) > 0]
    if not valid:
        return None
    last = valid[-1]
    return {"tvl_usd": last.get("totalLiquidityUSD", 0), "date": last.get("date")}

CHAIN_NAMES = {
    "ethereum": "Ethereum", "arbitrum": "Arbitrum", "base": "Base",
    "polygon": "Polygon", "optimism": "Optimism", "solana": "Solana",
    "avalanche": "Avalanche", "bsc": "BSC", "fantom": "Fantom",
}

def get_chain_tvl(chain: str) -> dict | None:
    normalized = CHAIN_NAMES.get(chain.lower(), chain.capitalize())
    data = _get(f"/charts/{normalized}")
    if not data or not isinstance(data, list):
        return None
    valid = [d for d in data if d.get("totalLiquidityUSD", d.get("tvl", 0)) > 0]
    if not valid:
        return None
    last = valid[-1]
    tvl = last.get("totalLiquidityUSD", last.get("tvl", 0))
    return {"chain": normalized, "tvl_usd": tvl}

def get_protocol_tvl(protocol: str) -> dict | None:
    data = _get(f"/protocol/{protocol.lower()}")
    if not data or isinstance(data, list):
        return None
    # tvl field is a list of historical data — get the latest value
    tvl_raw = data.get("tvl")
    if isinstance(tvl_raw, list) and tvl_raw:
        tvl = tvl_raw[-1].get("totalLiquidityUSD", 0)
    elif isinstance(tvl_raw, (int, float)):
        tvl = tvl_raw
    else:
        # Sum currentChainTvls as fallback
        chain_tvls = data.get("currentChainTvls", {})
        tvl = sum(v for k, v in chain_tvls.items() if "-" not in k)
    return {
        "name": data.get("name"),
        "tvl": tvl,
        "change_1d": data.get("change_1d"),
        "change_7d": data.get("change_7d"),
        "category": data.get("category"),
    }

def get_top_protocols(limit: int = 10) -> list:
    data = _get("/protocols")
    if not data or not isinstance(data, list):
        return []
    sorted_data = sorted(data, key=lambda x: x.get("tvl") or 0, reverse=True)
    return [
        {
            "name": p.get("name"), "tvl": p.get("tvl"),
            "change_7d": p.get("change_7d"), "category": p.get("category"),
            "chain": p.get("chain"),
        }
        for p in sorted_data[:limit]
    ]

def get_yields(chain: str = None, limit: int = 20) -> list:
    data = _get("/pools")
    if not data:
        return []
    pools = data.get("data", []) if isinstance(data, dict) else data
    if chain:
        pools = [p for p in pools if p.get("chain", "").lower() == chain.lower()]
    pools = sorted(pools, key=lambda x: x.get("apy") or 0, reverse=True)
    return [
        {
            "pool": p.get("symbol"), "project": p.get("project"),
            "chain": p.get("chain"), "apy": p.get("apy"),
            "tvl_usd": p.get("tvlUsd"),
        }
        for p in pools[:limit]
    ]
