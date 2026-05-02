from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


# ── Name resolution ──────────────────────────────────────────────────────────

class ResolveBody(BaseModel):
    name: str

@router.post("/names/resolve")
def resolve_name(body: ResolveBody):
    name = body.name.strip().lower()
    if name.endswith(".eth"):
        from app.tools.names.ens import resolve
        addr = resolve(name)
        return {"name": name, "address": addr, "chain": "evm", "resolved": bool(addr)}
    elif name.endswith(".sol"):
        from app.tools.names.sns import resolve
        addr = resolve(name)
        return {"name": name, "address": addr, "chain": "solana", "resolved": bool(addr)}
    return {"name": name, "address": None, "chain": None, "resolved": False}


# ── Polymarket ────────────────────────────────────────────────────────────────

@router.get("/prediction")
def prediction_markets(q: str = ""):
    from app.tools.prediction.polymarket import search_markets
    return search_markets(q, limit=5)


# ── News & Sentiment ──────────────────────────────────────────────────────────

@router.get("/news")
def get_news(coin: str = "", filter: str = "hot"):
    from app.tools.market.cryptopanic import get_news as _news
    currencies = [coin.upper()] if coin else None
    return _news(currencies=currencies, filter=filter)


@router.get("/sentiment/{coin}")
def get_sentiment(coin: str):
    from app.tools.market.cryptopanic import get_sentiment
    return get_sentiment(coin)
