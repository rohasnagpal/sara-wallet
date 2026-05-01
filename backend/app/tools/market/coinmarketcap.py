import os, requests

BASE = "https://pro-api.coinmarketcap.com/v1"

def _headers():
    key = os.getenv("COINMARKETCAP_API_KEY", "")
    return {"X-CMC_PRO_API_KEY": key} if key else None

def get_global() -> dict | None:
    h = _headers()
    if not h:
        return None
    try:
        r = requests.get(BASE + "/global-metrics/quotes/latest", headers=h, timeout=10)
        d = r.json().get("data", {}).get("quote", {}).get("USD", {})
        return {
            "total_market_cap": d.get("total_market_cap"),
            "btc_dominance": d.get("btc_dominance"),
            "total_volume_24h": d.get("total_volume_24h"),
        }
    except Exception:
        return None

def get_top_coins(limit: int = 10) -> list | None:
    h = _headers()
    if not h:
        return None
    try:
        r = requests.get(BASE + "/cryptocurrency/listings/latest",
                         params={"limit": limit, "convert": "USD"},
                         headers=h, timeout=10)
        coins = r.json().get("data", [])
        return [
            {
                "name": c["name"], "symbol": c["symbol"],
                "price": c["quote"]["USD"]["price"],
                "change_24h": c["quote"]["USD"]["percent_change_24h"],
                "rank": c["cmc_rank"],
            }
            for c in coins
        ]
    except Exception:
        return None
