import yfinance as yf

# yfinance crypto tickers: symbol → Yahoo Finance ticker
CRYPTO_TICKERS = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
    "AVAX": "AVAX-USD", "MATIC": "MATIC-USD", "POL": "POL-USD",
    "DOT": "DOT-USD", "ADA": "ADA-USD", "DOGE": "DOGE-USD",
    "LINK": "LINK-USD", "BNB": "BNB-USD", "XRP": "XRP-USD",
    "UNI": "UNI-USD", "AAVE": "AAVE-USD", "ARB": "ARB-USD",
    "OP": "OP-USD", "LTC": "LTC-USD", "ATOM": "ATOM-USD",
    "NEAR": "NEAR-USD", "FTM": "FTM-USD", "SUI": "SUI-USD",
    "TON": "TON-USD", "PEPE": "PEPE-USD", "TRX": "TRX-USD",
    "SHIB": "SHIB-USD", "BCH": "BCH-USD", "WIF": "WIF-USD",
    # CoinGecko IDs → yf tickers
    "BITCOIN": "BTC-USD", "ETHEREUM": "ETH-USD", "SOLANA": "SOL-USD",
}

def _fetch(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        hist = t.history(period="2d")
        if hist.empty:
            return None
        prev_close = hist["Close"].iloc[-2] if len(hist) >= 2 else hist["Close"].iloc[-1]
        curr = hist["Close"].iloc[-1]
        change_pct = ((curr - prev_close) / prev_close * 100) if prev_close else 0
        return {
            "ticker": ticker, "price": round(float(curr), 4),
            "prev_close": round(float(prev_close), 4),
            "change_pct": round(float(change_pct), 2),
            "currency": getattr(info, "currency", "USD"),
        }
    except Exception:
        return None

def get_crypto_price(symbol: str) -> dict | None:
    sym = symbol.upper()
    ticker = CRYPTO_TICKERS.get(sym, sym + "-USD")
    d = _fetch(ticker)
    if not d:
        return None
    return {
        "symbol": sym,
        "price": d["price"],
        "change_24h": d["change_pct"],
        "currency": "USD",
        "source": "yfinance",
    }
