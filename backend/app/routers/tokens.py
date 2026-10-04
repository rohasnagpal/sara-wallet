from fastapi import APIRouter

from app.core.assets import NETWORKS, SEND, enabled_networks, stablecoins_on
from app.tools.market.paraswap import _NATIVE

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("/trusted")
def trusted_tokens():
    """Return only assets Sara is allowed to resolve for wallet actions."""
    chains = []
    for network in enabled_networks():
        native_symbol = NETWORKS[network]["native"]
        tokens = ([{"symbol": native_symbol, "address": _NATIVE, "decimals": 18, "native": True}]
                  if native_symbol else [])
        for asset in stablecoins_on(network, capability=SEND, enabled_only=True):
            if asset.symbol == native_symbol:
                continue
            tokens.append({
                "symbol": asset.symbol, "address": asset.address,
                "decimals": asset.decimals, "native": False,
            })
        chains.append({"chain": network, "tokens": tokens})
    return {"chains": chains}
