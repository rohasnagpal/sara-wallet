from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Wallet
from app.tools.wallet.balance import get_wallet_balance
from app.tools.market.coingecko import get_multi_price, get_price, SYMBOL_TO_ID

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

CHAIN_NATIVE = {
    "ethereum": ("ETH", "#6366f1"),
    "arbitrum": ("ETH", "#6366f1"),
    "base":     ("ETH", "#6366f1"),
    "optimism": ("ETH", "#6366f1"),
    "polygon":  ("POL", "#8b5cf6"),
    "solana":   ("SOL", "#9945ff"),
}

COLORS = ["#f59e0b","#6366f1","#10b981","#8b5cf6","#94a3b8","#ef4444","#14b8a6"]

@router.get("")
def get_portfolio(db: Session = Depends(get_db)):
    wallets = db.query(Wallet).all()
    if not wallets:
        return {
            "total_usd": 0, "change_24h_pct": 0,
            "assets": [], "by_chain": {}, "allocation": [],
        }

    # Gather native balances
    holdings: list[dict] = []
    for w in wallets:
        try:
            b = get_wallet_balance(w)
            net = "solana" if w.chain == "solana" else "ethereum"
            sym, color = CHAIN_NATIVE.get(net, ("ETH", "#6366f1"))
            holdings.append({
                "wallet": w.name, "chain": net, "symbol": sym,
                "balance": b["balance"],
            })
        except Exception:
            pass

    # Fetch live prices for all unique symbols
    symbols = list({h["symbol"] for h in holdings})
    prices = get_multi_price(symbols) if symbols else {}

    assets = []
    by_chain: dict[str, float] = {}
    total_usd = 0.0

    for h in holdings:
        sym = h["symbol"]
        p = prices.get(sym, {})
        price = p.get("price", 0)
        change_24h = p.get("change_24h", 0)
        usd_value = h["balance"] * price
        total_usd += usd_value
        by_chain[h["chain"]] = by_chain.get(h["chain"], 0) + usd_value
        assets.append({
            "wallet": h["wallet"], "symbol": sym, "balance": h["balance"],
            "price": price, "usd_value": usd_value,
            "change_24h": change_24h, "chain": h["chain"],
        })

    # Allocation slices (only wallets with non-zero value)
    valued = [a for a in assets if a["usd_value"] > 0]
    allocation = []
    for i, a in enumerate(sorted(valued, key=lambda x: x["usd_value"], reverse=True)):
        pct = (a["usd_value"] / total_usd * 100) if total_usd else 0
        allocation.append({
            "name": f"{a['wallet']} ({a['symbol']})",
            "pct": round(pct, 2),
            "color": COLORS[i % len(COLORS)],
        })

    weighted_change = sum(a["change_24h"] * a["usd_value"] for a in assets) / total_usd if total_usd else 0

    return {
        "total_usd": round(total_usd, 2),
        "change_24h_pct": round(weighted_change, 2),
        "assets": assets,
        "by_chain": {k: round(v, 2) for k, v in by_chain.items()},
        "allocation": allocation,
    }
