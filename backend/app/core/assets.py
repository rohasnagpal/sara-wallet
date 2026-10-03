"""Sara's canonical network and stablecoin policy.

Every supported stablecoin/chain pair is defined exactly once in
``STABLECOINS``. Callers must resolve assets through this module instead of
maintaining feature-local contract tables. A registry entry does not imply
support for every feature: capabilities are explicit and chain-specific.
"""
from dataclasses import dataclass
import os


NETWORKS = {
    "ethereum": {"label": "Ethereum", "chain_id": 1, "native": "ETH"},
    "arbitrum": {"label": "Arbitrum", "chain_id": 42161, "native": "ETH"},
    "base": {"label": "Base", "chain_id": 8453, "native": "ETH"},
    "optimism": {"label": "OP Mainnet", "chain_id": 10, "native": "ETH"},
    "polygon": {"label": "Polygon PoS", "chain_id": 137, "native": "POL"},
    # Arc pays gas in USDC. Its ERC-20-compatible USDC precompile remains in
    # the stablecoin registry because contract calls use that address, while
    # native transfers use Arc's 18-decimal native balance.
    "arc": {"label": "Arc", "chain_id": 5042, "native": "USDC"},
    # Tempo has no native gas token. A TIP-20 transfer pays its fee in the
    # stablecoin being transferred, so at least one enabled USD stablecoin is
    # required instead of a separate native asset.
    "tempo": {"label": "Tempo", "chain_id": 4217, "native": None, "stablecoin_gas": True},
}

ALL_NETWORKS = tuple(NETWORKS)

BALANCE = "balance"
SEND = "send"
ACTIVITY = "activity"
INVOICE = "invoice"
RECONCILE = "reconcile"
SWAP = "swap"
BRIDGE = "bridge"
CCTP = "cctp"
AAVE = "aave"
X402 = "x402"


@dataclass(frozen=True)
class Stablecoin:
    symbol: str
    name: str
    issuer: str
    network: str
    address: str
    decimals: int
    capabilities: frozenset[str]
    default_enabled: bool = True

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


_USDC_STANDARD = frozenset({BALANCE, SEND, ACTIVITY, INVOICE, RECONCILE, SWAP, BRIDGE, CCTP, AAVE})
_USDC_X402 = _USDC_STANDARD | {X402}
_BALANCE_AND_SEND = frozenset({BALANCE, SEND})
_BALANCE_SEND_ACTIVITY = frozenset({BALANCE, SEND, ACTIVITY})


def _coin(symbol: str, name: str, issuer: str, network: str, address: str,
          capabilities: frozenset[str]) -> Stablecoin:
    return Stablecoin(
        symbol=symbol, name=name, issuer=issuer, network=network,
        address=address, decimals=6, capabilities=capabilities,
    )


# Contract addresses are issuer- or network-published mainnet addresses. EURC exists only
# on Ethereum, Base and Arc among Sara's current networks. USDT is native on
# Ethereum, participates in USDT0's legacy mesh on Arbitrum, and is deployed
# through USDT0 on Optimism and Polygon. Arc's USDC address is its enshrined
# ERC-20 precompile, not a conventional deployed contract.
STABLECOINS: dict[tuple[str, str], Stablecoin] = {
    ("ethereum", "USDC"): _coin("USDC", "USD Coin", "Circle", "ethereum", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", _USDC_X402),
    ("arbitrum", "USDC"): _coin("USDC", "USD Coin", "Circle", "arbitrum", "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", _USDC_X402),
    ("base", "USDC"): _coin("USDC", "USD Coin", "Circle", "base", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", _USDC_X402),
    ("optimism", "USDC"): _coin("USDC", "USD Coin", "Circle", "optimism", "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", _USDC_STANDARD),
    ("polygon", "USDC"): _coin("USDC", "USD Coin", "Circle", "polygon", "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", _USDC_X402),
    ("arc", "USDC"): _coin("USDC", "USD Coin", "Circle", "arc", "0x3600000000000000000000000000000000000000", _BALANCE_AND_SEND),
    ("ethereum", "EURC"): _coin("EURC", "EURC", "Circle", "ethereum", "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c", _BALANCE_SEND_ACTIVITY),
    ("base", "EURC"): _coin("EURC", "EURC", "Circle", "base", "0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42", _BALANCE_SEND_ACTIVITY),
    ("arc", "EURC"): _coin("EURC", "EURC", "Circle", "arc", "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1", _BALANCE_AND_SEND),
    ("ethereum", "USDT"): _coin("USDT", "Tether USD", "Tether", "ethereum", "0xdAC17F958D2ee523a2206206994597C13D831ec7", _BALANCE_SEND_ACTIVITY),
    ("arbitrum", "USDT"): _coin("USDT", "Tether USD", "Tether / USDT0", "arbitrum", "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", _BALANCE_SEND_ACTIVITY),
    ("optimism", "USDT"): _coin("USDT", "Tether USD (USDT0)", "Tether / USDT0", "optimism", "0x01bFF41798a0BcF287b996046Ca68b395DbC1071", _BALANCE_SEND_ACTIVITY),
    ("polygon", "USDT"): _coin("USDT", "Tether USD (USDT0)", "Tether / USDT0", "polygon", "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", _BALANCE_SEND_ACTIVITY),
    ("tempo", "USDC"): _coin("USDC", "USD Coin (USDC.e)", "Bridged USDC", "tempo", "0x20c000000000000000000000b9537d11c60e8b50", _BALANCE_AND_SEND),
    ("tempo", "USDT"): _coin("USDT", "Tether USD (USDT0)", "Tether / USDT0", "tempo", "0x20C00000000000000000000014f22CA97301EB73", _BALANCE_AND_SEND),
}

STABLECOIN_SYMBOLS = tuple(dict.fromkeys(symbol for _, symbol in STABLECOINS))


def get_stablecoin(symbol: str, network: str) -> Stablecoin | None:
    return STABLECOINS.get((network.lower(), symbol.upper()))


def stablecoins_on(network: str, *, capability: str | None = None,
                   enabled_only: bool = False) -> tuple[Stablecoin, ...]:
    network = network.lower()
    assets = tuple(
        asset for (asset_network, _), asset in STABLECOINS.items()
        if asset_network == network and (capability is None or asset.supports(capability))
    )
    if enabled_only:
        assets = tuple(asset for asset in assets if token_enabled(asset.symbol, network))
    return assets


def stablecoin_networks(symbol: str, *, capability: str | None = None,
                        enabled_only: bool = True) -> tuple[str, ...]:
    symbol = symbol.upper()
    return tuple(
        network for network in ALL_NETWORKS
        if (asset := get_stablecoin(symbol, network)) is not None
        and (capability is None or asset.supports(capability))
        and (not enabled_only or token_enabled(symbol, network))
    )


def resolve_stablecoin(symbol: str, network: str, *, capability: str | None = None,
                       enabled_only: bool = True) -> tuple[str, int] | None:
    asset = get_stablecoin(symbol, network)
    if asset is None or (capability is not None and not asset.supports(capability)):
        return None
    if enabled_only and not token_enabled(asset.symbol, asset.network):
        return None
    return asset.address, asset.decimals


def _read_network_set(key: str, supported: tuple[str, ...]) -> set[str]:
    raw = os.getenv(key)
    if raw is None:
        return set(supported)
    selected = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return selected.intersection(supported)


def enabled_networks() -> tuple[str, ...]:
    selected = _read_network_set("SARA_ENABLED_NETWORKS", ALL_NETWORKS)
    return tuple(network for network in ALL_NETWORKS if network in selected)


def configured_stablecoin_networks(symbol: str) -> tuple[str, ...]:
    symbol = symbol.upper()
    supported = stablecoin_networks(symbol, enabled_only=False)
    raw = os.getenv(f"SARA_{symbol}_NETWORKS")
    if raw is None:
        selected = {
            network for network in supported
            if get_stablecoin(symbol, network).default_enabled
        }
    else:
        selected = {
            item.strip().lower() for item in raw.split(",") if item.strip()
        }.intersection(supported)
    enabled = set(enabled_networks())
    return tuple(network for network in supported if network in selected and network in enabled)


def network_enabled(network: str) -> bool:
    return network.lower() in enabled_networks()


def token_enabled(symbol: str, network: str) -> bool:
    network = network.lower()
    symbol = symbol.upper()
    if not network_enabled(network):
        return False
    if symbol == NETWORKS[network]["native"]:
        return True
    return get_stablecoin(symbol, network) is not None and network in configured_stablecoin_networks(symbol)


def sendable_symbols(network: str) -> list[str]:
    network = network.lower()
    if not network_enabled(network):
        return []
    native = NETWORKS[network]["native"]
    symbols = [native] if native else []
    for asset in stablecoins_on(network, capability=SEND, enabled_only=True):
        if asset.symbol not in symbols:
            symbols.append(asset.symbol)
    return symbols


def serialize_preferences() -> dict:
    enabled = set(enabled_networks())
    return {
        "stablecoin_symbols": list(STABLECOIN_SYMBOLS),
        "networks": [
            {
                "id": network,
                **details,
                "enabled": network in enabled,
                "stablecoins": [
                    {
                        "symbol": asset.symbol,
                        "name": asset.name,
                        "issuer": asset.issuer,
                        "address": asset.address,
                        "decimals": asset.decimals,
                        "capabilities": sorted(asset.capabilities),
                        "enabled": token_enabled(asset.symbol, network),
                        "required_for_gas": details["native"] == asset.symbol,
                        "can_pay_gas": details.get("stablecoin_gas", False) or details["native"] == asset.symbol,
                    }
                    for asset in stablecoins_on(network)
                ],
            }
            for network, details in NETWORKS.items()
        ],
    }
