import requests

_BASE = "https://api.hyperliquid.xyz"

PERP_ALIASES: dict[str, str] = {
    # Crypto
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH", "ether": "ETH",
    "sol": "SOL", "solana": "SOL",
    "avax": "AVAX", "avalanche": "AVAX",
    "bnb": "BNB",
    "xrp": "XRP",
    "doge": "DOGE", "dogecoin": "DOGE",
    "link": "LINK", "chainlink": "LINK",
    "arb": "ARB", "arbitrum": "ARB",
    "op": "OP", "optimism": "OP",
    "apt": "APT", "aptos": "APT",
    "sui": "SUI",
    "atom": "ATOM", "cosmos": "ATOM",
    "near": "NEAR",
    "inj": "INJ", "injective": "INJ",
    "tia": "TIA", "celestia": "TIA",
    "pepe": "PEPE",
    "wif": "WIF",
    "dot": "DOT", "polkadot": "DOT",
    "ada": "ADA", "cardano": "ADA",
    "ltc": "LTC", "litecoin": "LTC",
    "matic": "MATIC", "polygon": "MATIC",
    # Tokenised equities / RWAs on HL (subset that actually trade)
    "aapl": "AAPL", "apple": "AAPL",
    "tsla": "TSLA", "tesla": "TSLA",
    "nvda": "NVDA", "nvidia": "NVDA",
    "amzn": "AMZN", "amazon": "AMZN",
    "msft": "MSFT", "microsoft": "MSFT",
    "meta": "META",
    "googl": "GOOGL", "google": "GOOGL",
    # Commodities / FX that trade on HL
    "gold": "XAU", "xau": "XAU",
    "silver": "XAG", "xag": "XAG",
    "eur": "EUR", "euro": "EUR",
    "gbp": "GBP",
}


def _info(payload: dict) -> dict | None:
    try:
        r = requests.post(f"{_BASE}/info", json=payload, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def get_all_markets() -> list[dict]:
    meta = _info({"type": "meta"})
    mids = _info({"type": "allMids"}) or {}
    if not meta:
        return []
    out = []
    for asset in meta.get("universe", []):
        name = asset.get("name", "")
        price = float(mids.get(name, 0))
        out.append({
            "symbol": name,
            "price": price,
            "max_leverage": asset.get("maxLeverage", 50),
        })
    return out


def get_mark_price(symbol: str) -> float | None:
    mids = _info({"type": "allMids"}) or {}
    raw = mids.get(symbol)
    return float(raw) if raw else None


def preview_order(symbol: str, side: str, size_usd: float, leverage: float) -> dict | None:
    price = get_mark_price(symbol)
    if not price:
        return None
    leverage = max(1.0, leverage)
    qty = size_usd / price
    margin = size_usd / leverage
    mm = 0.005  # 0.5 % maintenance margin fraction
    if side.lower() == "long":
        liq = price * (1 - 1 / leverage + mm)
    else:
        liq = price * (1 + 1 / leverage - mm)
    fee = size_usd * 0.00035  # 0.035 % taker
    return {
        "symbol": symbol,
        "side": side,
        "size_usd": size_usd,
        "leverage": leverage,
        "entry_price": price,
        "quantity": qty,
        "margin_required": margin,
        "liquidation_price": max(0.0, liq),
        "fee_usd": fee,
    }


def execute_order(private_key: str, symbol: str, side: str,
                  size_usd: float, leverage: float) -> dict:
    try:
        from hyperliquid.exchange import Exchange
        import eth_account as _ea

        account = _ea.Account.from_key(private_key)
        exchange = Exchange(account, base_url=_BASE)

        price = get_mark_price(symbol)
        if not price:
            return {"status": "error", "error": f"No price data for {symbol}"}

        qty = round(size_usd / price, 4)
        is_buy = side.lower() in ("long", "buy")
        lev = max(1, int(leverage))

        exchange.update_leverage(lev, symbol, is_cross=False)
        result = exchange.market_open(symbol, is_buy, qty)

        if result.get("status") == "ok":
            statuses = (result.get("response", {})
                        .get("data", {}).get("statuses", [{}]))
            oid = (statuses[0].get("filled", {}).get("oid")
                   or statuses[0].get("resting", {}).get("oid")
                   or "placed")
            return {"status": "ok", "order_id": str(oid), "qty": qty, "price": price}
        else:
            return {"status": "error", "error": str(result)}
    except ImportError:
        return {"status": "error",
                "error": "hyperliquid-python-sdk not installed — run: pip install hyperliquid-python-sdk"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_positions(wallet_address: str) -> list[dict]:
    state = _info({"type": "clearinghouseState", "user": wallet_address})
    if not state:
        return []
    out = []
    for ap in state.get("assetPositions", []):
        p = ap.get("position", {})
        szi = float(p.get("szi", 0))
        if szi == 0:
            continue
        entry = float(p.get("entryPx") or 0)
        pval = float(p.get("positionValue") or 0)
        mark = pval / abs(szi) if abs(szi) > 0 else 0.0
        lev_info = p.get("leverage", {})
        lev = float(lev_info.get("value", 1)) if isinstance(lev_info, dict) else 1.0
        out.append({
            "symbol": p.get("coin", ""),
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "entry_price": entry,
            "mark_price": mark,
            "pnl": float(p.get("unrealizedPnl") or 0),
            "liquidation_price": float(p.get("liquidationPx") or 0),
            "leverage": lev,
        })
    return out


def close_position(private_key: str, symbol: str) -> dict:
    try:
        from hyperliquid.exchange import Exchange
        import eth_account as _ea

        account = _ea.Account.from_key(private_key)
        exchange = Exchange(account, base_url=_BASE)
        result = exchange.market_close(symbol)
        if result.get("status") == "ok":
            return {"status": "ok", "symbol": symbol}
        return {"status": "error", "error": str(result)}
    except ImportError:
        return {"status": "error",
                "error": "hyperliquid-python-sdk not installed — run: pip install hyperliquid-python-sdk"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
