import os, time, requests

BASE = "https://api.coingecko.com/api/v3"

SYMBOL_TO_ID = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "AVAX": "avalanche-2", "MATIC": "matic-network", "POL": "polygon-ecosystem-token",
    "DOT": "polkadot", "ADA": "cardano", "DOGE": "dogecoin",
    "LINK": "chainlink", "BNB": "binancecoin", "XRP": "ripple",
    "UNI": "uniswap", "AAVE": "aave", "ARB": "arbitrum",
    "OP": "optimism", "LTC": "litecoin", "ATOM": "cosmos",
    "NEAR": "near", "FTM": "fantom", "INJ": "injective-protocol",
    "TIA": "celestia", "SUI": "sui", "SEI": "sei-network",
    "TON": "the-open-network", "PEPE": "pepe", "WIF": "dogwifcoin",
}

# Simple TTL cache: {cache_key: (timestamp, data)}
_cache: dict[str, tuple[float, object]] = {}
_TTL = 90  # seconds — well within CoinGecko free tier (30 req/min)

def _cached(key: str, ttl: int = _TTL):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None

def _store(key: str, data):
    _cache[key] = (time.time(), data)
    return data

def _headers() -> dict:
    key = os.getenv("COINGECKO_API_KEY", "")
    return {"x-cg-demo-api-key": key} if key else {}

def _resolve_id(coin: str) -> str:
    return SYMBOL_TO_ID.get(coin.upper(), coin.lower())

def _get(path: str, params: dict = None) -> dict | list | None:
    try:
        r = requests.get(BASE + path, params=params, headers=_headers(), timeout=10)
        if r.status_code == 429:
            return None  # rate limited — caller will return cached or None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def get_price(coin: str, vs: str = "usd") -> dict | None:
    cid = _resolve_id(coin)
    key = f"price:{cid}:{vs}"
    cached = _cached(key)
    if cached is not None:
        return cached

    # Primary: /coins/markets — rich data in one call
    data = _get("/coins/markets", {
        "vs_currency": vs, "ids": cid,
        "price_change_percentage": "7d,30d",
        "sparkline": "false",
    })
    if data and isinstance(data, list) and len(data) > 0:
        m = data[0]
        result = {
            "coin_id":        cid,
            "symbol":         coin.upper(),
            "price":          m.get("current_price") or 0,
            "change_24h":     round(m.get("price_change_percentage_24h") or 0, 2),
            "change_7d":      round(m.get("price_change_percentage_7d_in_currency") or 0, 2),
            "change_30d":     round(m.get("price_change_percentage_30d_in_currency") or 0, 2),
            "market_cap":     m.get("market_cap") or 0,
            "market_cap_rank": m.get("market_cap_rank"),
            "volume_24h":     m.get("total_volume") or 0,
            "high_24h":       m.get("high_24h") or 0,
            "low_24h":        m.get("low_24h") or 0,
            "ath":            m.get("ath") or 0,
            "ath_change_pct": round(m.get("ath_change_percentage") or 0, 1),
        }
        return _store(key, result)

    # Fallback: yfinance (if CoinGecko rate-limited)
    try:
        from app.tools.market.yfinance_tool import get_crypto_price
        yf_data = get_crypto_price(coin)
        if yf_data and yf_data.get("price"):
            result = {
                "coin_id": cid, "symbol": coin.upper(),
                "price": yf_data["price"],
                "change_24h": yf_data["change_24h"],
                "change_7d": None, "change_30d": None,
                "market_cap": 0, "volume_24h": 0,
                "high_24h": 0, "low_24h": 0,
                "ath": 0, "ath_change_pct": None,
                "market_cap_rank": None,
            }
            return _store(key, result)
    except Exception:
        pass

    return None

def get_trending() -> list:
    key = "trending"
    cached = _cached(key, ttl=300)  # trending changes slowly, cache 5 min
    if cached is not None:
        return cached
    data = _get("/search/trending")
    if not data:
        return []
    results = []
    for item in data.get("coins", [])[:7]:
        c = item.get("item", {})
        results.append({
            "name": c.get("name"), "symbol": c.get("symbol"),
            "rank": c.get("market_cap_rank"),
        })
    return _store(key, results)

def get_ohlcv(coin: str, days: int = 30) -> list:
    cid = _resolve_id(coin)
    key = f"ohlcv:{cid}:{days}"
    cached = _cached(key, ttl=600)  # OHLCV data, cache 10 min
    if cached is not None:
        return cached
    data = _get(f"/coins/{cid}/ohlc", {"vs_currency": "usd", "days": str(days)})
    result = data if isinstance(data, list) else []
    return _store(key, result)

def get_global() -> dict | None:
    key = "global"
    cached = _cached(key, ttl=120)
    if cached is not None:
        return cached
    data = _get("/global")
    if not data:
        return None
    d = data.get("data", {})
    result = {
        "total_market_cap_usd": d.get("total_market_cap", {}).get("usd", 0),
        "btc_dominance": round(d.get("market_cap_percentage", {}).get("btc", 0), 2),
        "total_volume_24h": d.get("total_volume", {}).get("usd", 0),
        "market_cap_change_24h": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
    }
    return _store(key, result)

def get_multi_price(coins: list[str], vs: str = "usd") -> dict:
    key = f"multi:{','.join(sorted(coins))}:{vs}"
    cached = _cached(key)
    if cached is not None:
        return cached

    out: dict = {}

    # yfinance pass — fast, no rate limits, but may miss some coins
    try:
        from app.tools.market.yfinance_tool import get_crypto_price
        for coin in coins:
            yf_data = get_crypto_price(coin)
            if yf_data and yf_data.get("price"):
                out[coin.upper()] = {
                    "price": yf_data["price"],
                    "change_24h": yf_data["change_24h"],
                }
    except Exception:
        pass

    # CoinGecko pass for any coins yfinance missed
    missing = [c for c in coins if c.upper() not in out]
    if missing:
        ids = ",".join(_resolve_id(c) for c in missing)
        data = _get("/simple/price", {"ids": ids, "vs_currencies": vs, "include_24hr_change": "true"})
        if data:
            for coin in missing:
                cid = _resolve_id(coin)
                if cid in data and data[cid].get(vs):
                    out[coin.upper()] = {
                        "price": data[cid].get(vs, 0),
                        "change_24h": round(data[cid].get(f"{vs}_24h_change", 0), 2),
                    }

    return _store(key, out)
