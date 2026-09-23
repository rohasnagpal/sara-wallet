from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeoutError
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
    # "arc" is deliberately absent: Arc pays gas in USDC itself, so its
    # native balance and its USDC (ERC-20) balance are the exact same
    # money in two decimal representations, not two separate assets.
    # Counting both would double the portfolio's Arc total.
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
    # Every (wallet, network) balance/token fetch is an independent RPC call,
    # so all of them - across every wallet, not just every network within one
    # wallet - are submitted to a single shared pool. Looping wallets
    # sequentially (each waiting on its own pool before the next wallet even
    # starts) turned N wallets into N times the latency for no reason.
    holdings: list[dict] = []
    networks = list(enabled_networks())
    evm_wallets = [w for w in wallets if w.chain == "evm"]

    def _fetch_native(addr: str, net: str):
        try:
            b = evm_chain.get_balance(addr, net)
            if b["balance"] > 0.000001:
                return {"symbol": NATIVE_SYMBOLS.get(net, "ETH"), "balance": b["balance"]}
        except Exception:
            pass
        return None

    def _fetch_tokens(addr: str, net: str):
        try:
            return get_erc20_balances(addr, net)
        except Exception:
            return []

    if evm_wallets and networks:
        max_workers = min(32, max(5, len(evm_wallets) * len(networks) * 2))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_map = {}
            for w in evm_wallets:
                for net in networks:
                    # Arc has no separate native balance to fetch (see
                    # NATIVE_SYMBOLS above) - only its USDC/ERC-20 balance,
                    # from _fetch_tokens.
                    if net in NATIVE_SYMBOLS:
                        future_map[ex.submit(_fetch_native, w.address, net)] = ("native", w, net)
                    future_map[ex.submit(_fetch_tokens, w.address, net)] = ("tokens", w, net)
            try:
                for fut in as_completed(future_map, timeout=15):
                    kind, w, net = future_map[fut]
                    result = fut.result()
                    if kind == "native" and result:
                        holdings.append({"wallet": w.name, "chain": net, **result})
                    elif kind == "tokens":
                        for tok in result:
                            holdings.append({"wallet": w.name, "chain": net, "symbol": tok["symbol"], "balance": tok["balance"]})
            except FutureTimeoutError:
                # Whatever hasn't resolved by the deadline is simply left out
                # of this refresh (rather than failing the whole portfolio
                # load) - one slow RPC provider on one network must not block
                # every other wallet's already-available balances.
                pass

    # Fetch live prices for all unique symbols
    symbols = list({h["symbol"] for h in holdings})
    prices = get_multi_price(symbols) if symbols else {}

    assets = []
    by_chain: dict[str, float] = {}
    total_usd = 0.0
    any_price_unavailable = False

    for h in holdings:
        sym = h["symbol"]
        p = prices.get(sym)
        if p is None:
            # A missing price (rate-limited provider, unlisted symbol, etc.)
            # must never be treated as $0 — that silently understates the
            # portfolio and looks identical to a real zero-value asset.
            any_price_unavailable = True
            assets.append({
                "wallet": h["wallet"], "symbol": sym, "balance": h["balance"],
                "price": None, "usd_value": None, "change_24h": None,
                "chain": h["chain"], "price_unavailable": True,
            })
            continue
        price = p.get("price", 0)
        change_24h = p.get("change_24h", 0)
        usd_value = h["balance"] * price
        total_usd += usd_value
        by_chain[h["chain"]] = by_chain.get(h["chain"], 0) + usd_value
        assets.append({
            "wallet": h["wallet"], "symbol": sym, "balance": h["balance"],
            "price": price, "usd_value": usd_value,
            "change_24h": change_24h, "chain": h["chain"], "price_unavailable": False,
        })

    priced = [a for a in assets if not a["price_unavailable"]]

    # Allocation slices (only wallets with non-zero value)
    valued = [a for a in priced if a["usd_value"] > 0]
    allocation = []
    for i, a in enumerate(sorted(valued, key=lambda x: x["usd_value"], reverse=True)):
        pct = (a["usd_value"] / total_usd * 100) if total_usd else 0
        allocation.append({
            "name": f"{a['wallet']} ({a['symbol']})",
            "pct": round(pct, 2),
            "color": COLORS[i % len(COLORS)],
        })

    weighted_change = sum(a["change_24h"] * a["usd_value"] for a in priced) / total_usd if total_usd else 0

    result = {
        "total_usd": round(total_usd, 2),
        "change_24h_pct": round(weighted_change, 2),
        "assets": assets,
        "by_chain": {k: round(v, 2) for k, v in by_chain.items()},
        "allocation": allocation,
        "prices_unavailable": any_price_unavailable,
    }
    # Skip the durable snapshot when any price is missing — a rate-limited
    # provider must not write an artificially low total into portfolio
    # history (the 30-day chart) any more than it should show one live.
    if not any_price_unavailable:
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
