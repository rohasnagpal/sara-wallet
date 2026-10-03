from fastapi import APIRouter, HTTPException
from app.tools.market import coingecko, gas_tracker

router = APIRouter(prefix="/market", tags=["market"])

def _or_404(data, detail="Not found"):
    if data is None:
        raise HTTPException(404, detail)
    return data

@router.get("/price/{coin}")
def price(coin: str):
    return _or_404(coingecko.get_price(coin), f"No price data for {coin}")

@router.get("/global")
def global_market():
    return _or_404(coingecko.get_global(), "Global market data unavailable")

@router.get("/gas")
def gas():
    data = gas_tracker.get_gas_prices()
    if "error" in data:
        raise HTTPException(502, data["error"])
    return data
