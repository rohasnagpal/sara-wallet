import requests, json
from datetime import datetime

_BASE = "https://gamma-api.polymarket.com"

_STOP = {"the", "a", "an", "is", "will", "be", "to", "of", "in", "on", "at",
         "for", "and", "or", "it", "i", "my", "do", "what", "who", "how",
         "are", "was", "hit", "reach", "before", "after", "by"}


def _get(path: str, params: dict = None) -> list | dict | None:
    try:
        r = requests.get(f"{_BASE}{path}", params=params, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def _parse_price(raw) -> float:
    """outcomePrices entries are sometimes strings, sometimes floats."""
    try:
        return float(raw)
    except Exception:
        return 0.0


def _parse_json_field(val):
    """Gamma API embeds JSON arrays as strings inside the JSON response."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return val or []


def _build_market(m: dict) -> dict:
    outcomes    = _parse_json_field(m.get("outcomes"))
    prices_raw  = _parse_json_field(m.get("outcomePrices"))

    yes_price = no_price = 0.0
    for i, outcome in enumerate(outcomes):
        name = str(outcome).lower()
        price = _parse_price(prices_raw[i]) if i < len(prices_raw) else 0.0
        if "yes" in name:
            yes_price = price
        elif "no" in name:
            no_price = price

    # Fallback: binary market with only two prices but no yes/no labels
    if yes_price == 0.0 and no_price == 0.0 and len(prices_raw) >= 2:
        yes_price = _parse_price(prices_raw[0])
        no_price  = _parse_price(prices_raw[1])

    volume  = float(m.get("volumeNum") or m.get("volume") or 0)
    end_raw = m.get("endDateIso") or m.get("endDate") or ""
    try:
        end_dt  = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        end_str = end_dt.strftime("%b %d %Y")
    except Exception:
        end_str = end_raw[:10] if end_raw else "N/A"

    return {
        "question":  m.get("question", ""),
        "yes_price": round(yes_price * 100, 1),
        "no_price":  round(no_price  * 100, 1),
        "volume":    volume,
        "end_date":  end_str,
    }


def search_markets(query: str, limit: int = 5) -> list[dict]:
    # Gamma API search param is unreliable; fetch by volume and filter client-side.
    data = _get("/markets", {"limit": 100, "active": "true", "closed": "false",
                              "order": "volume", "ascending": "false"})
    if not data:
        return []

    items = data if isinstance(data, list) else data.get("markets", [])

    # Clean query: strip $/, symbols and skip pure-numeric tokens
    import re as _re
    clean_query = _re.sub(r'[^\w\s]', ' ', query.lower())
    keywords = [w for w in clean_query.split()
                if w not in _STOP and len(w) > 2 and not w.isdigit()]
    scored = []
    for m in items:
        q = _re.sub(r'[^\w\s]', ' ', m.get("question", "").lower())
        score = sum(1 for kw in keywords if kw in q)
        if score > 0:
            scored.append((score, m))

    # Sort by score desc, then volume desc
    scored.sort(key=lambda x: (x[0], float(x[1].get("volumeNum") or 0)), reverse=True)
    top = [m for _, m in scored[:limit]]

    # If no keyword match, return top-volume markets as fallback
    if not top:
        top = items[:limit]

    return [_build_market(m) for m in top]


def format_market(m: dict) -> str:
    return (
        f"**{m['question']}**\n"
        f"YES: **{m['yes_price']}%**  ·  NO: **{m['no_price']}%**\n"
        f"Volume: ${m['volume']:,.0f}  ·  Closes: {m['end_date']}"
    )
