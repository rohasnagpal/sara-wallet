import os, requests

_BASE = "https://cryptopanic.com/api/v1"


def _key() -> str | None:
    return os.getenv("CRYPTOPANIC_API_KEY") or None


def get_news(currencies: list[str] | None = None, filter: str = "hot", limit: int = 10) -> list[dict]:
    key = _key()
    if not key:
        return []
    params: dict = {"auth_token": key, "filter": filter, "public": "true"}
    if currencies:
        params["currencies"] = ",".join(currencies)
    try:
        r = requests.get(f"{_BASE}/posts/", params=params, timeout=10)
        if not r.ok:
            return []
        results = r.json().get("results", [])[:limit]
        out = []
        for item in results:
            votes = item.get("votes", {})
            out.append({
                "title":           item.get("title", ""),
                "url":             item.get("url", ""),
                "published_at":    item.get("published_at", ""),
                "source":          item.get("source", {}).get("title", ""),
                "votes_positive":  votes.get("positive", 0),
                "votes_negative":  votes.get("negative", 0),
            })
        return out
    except Exception:
        return []


def get_sentiment(coin_symbol: str) -> dict:
    news = get_news(currencies=[coin_symbol.upper()], filter="hot", limit=20)
    if not news:
        return {"coin": coin_symbol, "bullish_pct": 0, "bearish_pct": 0, "signal": "neutral", "news_count": 0}
    pos = sum(n["votes_positive"] for n in news)
    neg = sum(n["votes_negative"] for n in news)
    total = pos + neg or 1
    bull = round(pos / total * 100, 1)
    bear = round(neg / total * 100, 1)
    signal = "bullish" if bull > 60 else ("bearish" if bear > 60 else "neutral")
    return {"coin": coin_symbol, "bullish_pct": bull, "bearish_pct": bear, "signal": signal, "news_count": len(news)}
