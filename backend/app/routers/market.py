from fastapi import APIRouter, HTTPException, Query
from app.tools.market import coingecko, gas_tracker

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

@router.get("/gas")
def gas():
    data = gas_tracker.get_gas_prices()
    if "error" in data:
        raise HTTPException(502, data["error"])
    return data
