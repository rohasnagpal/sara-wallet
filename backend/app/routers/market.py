from fastapi import APIRouter, HTTPException, Query
from app.tools.market import coingecko, defillama, yfinance_tool, gas_tracker

router = APIRouter(prefix="/market", tags=["market"])

def _or_404(data, detail="Not found"):
    if data is None:
        raise HTTPException(404, detail)
    return data

@router.get("/price/{coin}")
def price(coin: str):
    return _or_404(coingecko.get_price(coin), f"No price data for {coin}")

@router.get("/trending")
def trending():
    return coingecko.get_trending()

@router.get("/global")
def global_market():
    return _or_404(coingecko.get_global(), "Global market data unavailable")

@router.get("/ohlcv/{coin}")
def ohlcv(coin: str, days: int = Query(default=30, ge=1, le=365)):
    data = coingecko.get_ohlcv(coin, days)
    if not data:
        raise HTTPException(404, f"No OHLCV data for {coin}")
    return data

@router.get("/tvl")
def total_tvl():
    return _or_404(defillama.get_total_tvl(), "TVL data unavailable")

@router.get("/tvl/chain/{chain}")
def chain_tvl(chain: str):
    return _or_404(defillama.get_chain_tvl(chain), f"No TVL for chain {chain}")

@router.get("/tvl/protocol/{name}")
def protocol_tvl(name: str):
    return _or_404(defillama.get_protocol_tvl(name), f"No TVL for protocol {name}")

@router.get("/yields")
def yields(chain: str = Query(default=None)):
    return defillama.get_yields(chain)

@router.get("/protocols")
def top_protocols(limit: int = Query(default=10, le=50)):
    return defillama.get_top_protocols(limit)

@router.get("/stock/{ticker}")
def stock(ticker: str):
    return _or_404(yfinance_tool.get_stock(ticker), f"No data for {ticker}")

@router.get("/commodity/{ticker}")
def commodity(ticker: str):
    return _or_404(yfinance_tool.get_commodity(ticker), f"No data for {ticker}")

@router.get("/forex/{pair}")
def forex(pair: str):
    return _or_404(yfinance_tool.get_forex(pair), f"No data for {pair}")

@router.get("/index/{ticker}")
def index(ticker: str):
    return _or_404(yfinance_tool.get_index(ticker), f"No data for {ticker}")

@router.get("/gas")
def gas():
    data = gas_tracker.get_gas_prices()
    if "error" in data:
        raise HTTPException(502, data["error"])
    return data

@router.get("/history/{ticker}")
def history(ticker: str, period: str = Query(default="1mo")):
    """30-day close prices for any yfinance ticker (commodities, forex, stocks, crypto)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist.empty:
            raise HTTPException(404, f"No history for {ticker}")
        closes = [round(float(v), 6) for v in hist["Close"].tolist()]
        prev = [round(float(v), 6) for v in hist["Open"].tolist()]
        return {
            "ticker": ticker,
            "closes": closes,
            "open": prev[0] if prev else None,
            "prev_close": closes[-2] if len(closes) >= 2 else closes[-1],
            "current": closes[-1],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e))
