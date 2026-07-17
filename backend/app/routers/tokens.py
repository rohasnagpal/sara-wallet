from fastapi import APIRouter
from app.chains.evm import _NATIVE_TOKEN
from app.tools.market.paraswap import CHAIN_IDS, NATIVE_SYMBOLS, _TOKENS, _NATIVE
from app.tools.market.jupiter import TOKEN_MINTS
from app.chains.tron import TRC20_TOKENS as TRON_TRC20_TOKENS

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("/trusted")
def trusted_tokens():
    """Every contract Sara will ever resolve a symbol to. Nothing outside
    this list is reachable from a send/swap/bridge command — a scam token
    sharing a symbol like USDT can't be substituted in."""
    chains = []
    for network, chain_id in CHAIN_IDS.items():
        native_symbol = NATIVE_SYMBOLS.get(network, _NATIVE_TOKEN.get(network, "ETH"))
        tokens = [{"symbol": native_symbol, "address": _NATIVE, "decimals": 18, "native": True}]
        for symbol, (address, decimals) in _TOKENS.get(chain_id, {}).items():
            tokens.append({"symbol": symbol, "address": address, "decimals": decimals, "native": False})
        chains.append({"chain": network, "tokens": tokens})

    # bsc / avalanche support native sends + bridging but have no verified
    # ERC-20 list of their own yet — still worth listing so the native asset
    # shows up as trusted.
    for network, symbol in (("bsc", "BNB"), ("avalanche", "AVAX")):
        if network not in CHAIN_IDS:
            chains.append({
                "chain": network,
                "tokens": [{"symbol": symbol, "address": _NATIVE, "decimals": 18, "native": True}],
            })

    sol_tokens = [
        {"symbol": symbol, "address": mint, "decimals": None, "native": symbol == "SOL"}
        for symbol, mint in TOKEN_MINTS.items()
    ]
    chains.append({"chain": "solana", "tokens": sol_tokens})

    tron_tokens = [{"symbol": "TRX", "address": "native", "decimals": 6, "native": True}]
    for symbol, (address, decimals) in TRON_TRC20_TOKENS.items():
        tron_tokens.append({"symbol": symbol, "address": address, "decimals": decimals, "native": False})
    chains.append({"chain": "tron", "tokens": tron_tokens})

    return {"chains": chains}
