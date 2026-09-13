from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.db.session import get_db
from app.db.models import Wallet, PortfolioSnapshot
from datetime import datetime, timedelta
import json
from app.chains import evm as evm_chain
from app.core.assets import enabled_networks
from app.tools.wallet.tokens import get_erc20_balances
from app.tools.market.coingecko import get_multi_price, SYMBOL_TO_ID

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

NATIVE_SYMBOLS = {
    "ethereum": "ETH", "arbitrum": "ETH", "base": "ETH", "optimism": "ETH",
    "polygon": "POL",
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

    # Gather native gas assets and Circle-issued USDC on enabled networks.
    holdings: list[dict] = []
    networks = list(enabled_networks())
    for w in wallets:
        if w.chain == "evm":
            def _fetch(net, addr=w.address, wname=w.name):
                try:
                    b = evm_chain.get_balance(addr, net)
                    if b["balance"] > 0.000001:
                        sym = NATIVE_SYMBOLS.get(net, "ETH")
                        return {"wallet": wname, "chain": net, "symbol": sym, "balance": b["balance"]}
                except Exception:
                    pass
                return None
            with ThreadPoolExecutor(max_workers=5) as ex:
                for result in as_completed({ex.submit(_fetch, net): net for net in networks}, timeout=10):
                    r = result.result()
                    if r:
                        holdings.append(r)
            # ERC-20 tokens via Alchemy — checked on every network regardless
            # of native balance there. A wallet can hold a bridged/received
            # token on a chain it has zero native gas on (e.g. right after a
            # cross-chain bridge, before ever funding gas there), so gating
            # this on native balance made real token balances invisible.
            def _fetch_tokens(net, addr=w.address):
                try:
                    return get_erc20_balances(addr, net)
                except Exception:
                    return []
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(_fetch_tokens, net): net for net in networks}
                for fut in as_completed(futures, timeout=10):
                    net = futures[fut]
                    for tok in fut.result():
                        holdings.append({"wallet": w.name, "chain": net, "symbol": tok["symbol"], "balance": tok["balance"]})

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

    result = {
        "total_usd": round(total_usd, 2),
        "change_24h_pct": round(weighted_change, 2),
        "assets": assets,
        "by_chain": {k: round(v, 2) for k, v in by_chain.items()},
        "allocation": allocation,
    }
    latest = db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.captured_at.desc()).first()
    if latest is None or latest.captured_at < datetime.utcnow() - timedelta(hours=1):
        db.add(PortfolioSnapshot(total_usd=str(result["total_usd"]), holdings=json.dumps(assets, default=str)))
        db.commit()
    return result


@router.get("/history")
def portfolio_history(days: int = 30, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.captured_at >= since).order_by(PortfolioSnapshot.captured_at).all()
    return {"history": [{"timestamp": row.captured_at.isoformat(), "total_usd": row.total_usd} for row in rows]}
