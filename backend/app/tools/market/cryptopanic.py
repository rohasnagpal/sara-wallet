"""
News & sentiment via free sources:
  - Headlines: CoinTelegraph + CoinDesk RSS (no key needed)
  - Market sentiment: Alternative.me Fear & Greed Index (no key needed)
  - Per-coin sentiment: keyword analysis of matched headlines
"""
import requests, time
import xml.etree.ElementTree as ET

_RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://feeds.feedburner.com/CoinDesk",
]
_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_cache: dict[str, tuple[float, object]] = {}
_TTL = 300  # 5 min

_BULLISH_WORDS = {
    "rally", "surge", "soar", "gain", "gains", "bull", "bullish", "rise", "rising",
    "high", "highs", "breakout", "buy", "buying", "positive", "record", "milestone",
    "adoption", "institutional", "accumulate", "moon", "pump", "recover", "recovery",
    "upside", "bounce", "strong", "strength",
}
_BEARISH_WORDS = {
    "crash", "drop", "drops", "fall", "falls", "bear", "bearish", "decline", "declining",
    "sell", "selling", "low", "lows", "dump", "fear", "risk", "hack", "hacked",
    "ban", "banned", "regulate", "regulation", "negative", "loss", "losses",
    "downside", "weak", "weakness", "correction", "liquidation", "capitulate",
}

_COIN_ALIASES: dict[str, list[str]] = {
    "BTC":  ["bitcoin", "btc"],
    "ETH":  ["ethereum", "eth", "ether"],
    "SOL":  ["solana", "sol"],
    "DOGE": ["dogecoin", "doge"],
    "XRP":  ["ripple", "xrp"],
    "ADA":  ["cardano", "ada"],
    "PEPE": ["pepe"],
    "LINK": ["chainlink", "link"],
    "AVAX": ["avalanche", "avax"],
    "BNB":  ["bnb", "binance"],
    "LTC":  ["litecoin", "ltc"],
    "DOT":  ["polkadot", "dot"],
    "WIF":  ["wif", "dogwifhat"],
}


def _cached(key: str):
    e = _cache.get(key)
    return e[1] if e and (time.time() - e[0]) < _TTL else None


def _store(key: str, data):
    _cache[key] = (time.time(), data)
    return data


def _fetch_rss(url: str) -> list[dict]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8)
        if not r.ok:
            return []
        root = ET.fromstring(r.content)
        out = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            if title:
                out.append({"title": title, "url": link})
        return out
    except Exception:
        return []


def _all_articles() -> list[dict]:
    cached = _cached("rss:all")
    if cached is not None:
        return cached
    articles = []
    for feed in _RSS_FEEDS:
        articles.extend(_fetch_rss(feed))
    return _store("rss:all", articles)


def _fng() -> dict:
    cached = _cached("fng")
    if cached is not None:
        return cached
    try:
        r = requests.get(_FNG_URL, timeout=6)
        if r.ok:
            d = r.json().get("data", [{}])[0]
            return _store("fng", {
                "value": int(d.get("value", 50)),
                "label": d.get("value_classification", "Neutral"),
            })
    except Exception:
        pass
    return _store("fng", {"value": 50, "label": "Neutral"})


def _coin_keywords(symbol: str) -> list[str]:
    sym = symbol.upper()
    return _COIN_ALIASES.get(sym, [sym.lower(), sym])


def _score_headline(title: str) -> int:
    words = set(title.lower().split())
    return len(words & _BULLISH_WORDS) - len(words & _BEARISH_WORDS)


# ── Public interface ──────────────────────────────────────────────────────────

def get_news(currencies: list[str] | None = None, filter: str = "hot", limit: int = 10) -> list[dict]:
    articles = _all_articles()
    if currencies:
        kws = []
        for sym in currencies:
            kws.extend(_coin_keywords(sym))
        articles = [a for a in articles if any(kw in a["title"].lower() for kw in kws)]
    return articles[:limit]


def get_sentiment(coin_symbol: str) -> dict:
    articles = _all_articles()
    kws = _coin_keywords(coin_symbol)
    matched = [a for a in articles if any(kw in a["title"].lower() for kw in kws)]

    fng = _fng()

    if not matched:
        val = fng["value"]
        if val >= 60:
            signal = "bullish"
        elif val <= 40:
            signal = "bearish"
        else:
            signal = "neutral"
        return {"signal": signal, "bullish_pct": 0.0, "bearish_pct": 0.0,
                "news_count": 0, "fng_value": fng["value"], "fng_label": fng["label"]}

    scores  = [_score_headline(a["title"]) for a in matched]
    total   = len(scores)
    bull_pct = round(sum(1 for s in scores if s > 0) / total * 100, 1)
    bear_pct = round(sum(1 for s in scores if s < 0) / total * 100, 1)

    if bull_pct > 55:
        signal = "bullish"
    elif bear_pct > 55:
        signal = "bearish"
    else:
        signal = "neutral"

    return {"signal": signal, "bullish_pct": bull_pct, "bearish_pct": bear_pct,
            "news_count": total, "fng_value": fng["value"], "fng_label": fng["label"]}
