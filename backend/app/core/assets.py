"""Sara's supported network and asset policy.

USDC addresses are Circle's official mainnet contracts:
https://developers.circle.com/stablecoins/usdc-contract-addresses

Users may hide supported networks or USDC on a network. Native assets remain
enabled whenever their network is enabled because they are required for gas.
"""
import os


NETWORKS = {
    "ethereum": {
        "label": "Ethereum", "chain_id": 1, "native": "ETH",
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    },
    "arbitrum": {
        "label": "Arbitrum", "chain_id": 42161, "native": "ETH",
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    },
    "base": {
        "label": "Base", "chain_id": 8453, "native": "ETH",
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    },
    "optimism": {
        "label": "OP Mainnet", "chain_id": 10, "native": "ETH",
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    },
    "polygon": {
        "label": "Polygon PoS", "chain_id": 137, "native": "POL",
        "usdc": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    },
    "arc": {
        # Arc (Circle's own L1, mainnet launched 2026-09-16) pays gas in
        # USDC itself - there is no separate native token. USDC here is an
        # "enshrined" precompile, not a bridged/deployed contract: verified
        # live on-chain (decimals=6, symbol="USDC", name="USDC") at this
        # exact address, kept in sync with the native 18-decimal balance by
        # Arc itself. Deliberately not yet wired into swap (Paraswap),
        # bridge (LI.FI), CCTP, Aave or the x402 facilitators - none of
        # those have a confirmed Arc integration as of this network's
        # addition, so claiming otherwise here would be a guess, not a
        # verified fact. Wallet creation, balance display and plain sends
        # are the only things Sara currently supports on Arc.
        "label": "Arc", "chain_id": 5042, "native": "USDC",
        "usdc": "0x3600000000000000000000000000000000000000",
    },
}

ALL_NETWORKS = tuple(NETWORKS)

# EURC (Circle's euro-backed stablecoin) — deliberately narrower than USDC.
# Live only on Ethereum, Base and Arc among Sara's networks (confirmed
# against Circle's own contract-address reference and independently
# verified live on-chain: decimals=6, symbol="EURC" on all three,
# name="Euro Coin" on Ethereum, name="EURC" on Base and Arc). NOT
# Arbitrum/Optimism/Polygon - Circle has never deployed EURC there.
# Scope is deliberately narrow, same as Arc's own rollout: wallet balance
# display and plain sends only. Not wired into swap (Paraswap), bridge
# (LI.FI), CCTP (Circle itself says CCTP-for-EURC is "planned", not live),
# Aave, x402 or onramp - each of those is a separate, larger decision.
EURC_ADDRESSES = {
    "ethereum": "0x1aBaEA1f7C830bD89Acc67eC4af516284b1bC33c",
    "base": "0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42",
    "arc": "0xbEf5f6d51CB62b58e6A8f77868681825C6fe21c1",
}
EURC_DECIMALS = 6


def _read_set(key: str, default: tuple[str, ...]) -> set[str]:
    raw = os.getenv(key)
    if raw is None:
        return set(default)
    selected = {item.strip().lower() for item in raw.split(",")}
    return selected.intersection(NETWORKS)


def enabled_networks() -> tuple[str, ...]:
    selected = _read_set("SARA_ENABLED_NETWORKS", ALL_NETWORKS)
    return tuple(network for network in ALL_NETWORKS if network in selected)


def usdc_networks() -> tuple[str, ...]:
    selected = _read_set("SARA_USDC_NETWORKS", ALL_NETWORKS)
    enabled = set(enabled_networks())
    return tuple(network for network in ALL_NETWORKS if network in selected and network in enabled)


_EURC_ALL_NETWORKS = tuple(EURC_ADDRESSES)


def eurc_networks() -> tuple[str, ...]:
    raw = os.getenv("SARA_EURC_NETWORKS")
    if raw is None:
        selected = set(_EURC_ALL_NETWORKS)
    else:
        selected = {item.strip().lower() for item in raw.split(",")}.intersection(_EURC_ALL_NETWORKS)
    enabled = set(enabled_networks())
    return tuple(network for network in _EURC_ALL_NETWORKS if network in selected and network in enabled)


def network_enabled(network: str) -> bool:
    return network.lower() in enabled_networks()


def token_enabled(symbol: str, network: str) -> bool:
    network = network.lower()
    if not network_enabled(network):
        return False
    symbol = symbol.upper()
    if symbol == NETWORKS[network]["native"]:
        return True
    if symbol == "USDC" and network in usdc_networks():
        return True
    return symbol == "EURC" and network in eurc_networks()


def sendable_symbols(network: str) -> list[str]:
    """Every symbol Sara will resolve to a real, sendable contract on this
    network - the native gas token, USDC/other tokens via Paraswap's
    per-chain table (only present for networks Paraswap itself covers), and
    EURC where it's live. Used for the assistant's "not recognized"
    message. Deliberately doesn't call app.tools.market.paraswap.
    trusted_symbols() directly: that function returns [] for Arc (Arc has
    no Paraswap chain-id entry at all, by design - it isn't swap-integrated),
    which would silently hide Arc's own native USDC from this message even
    though sending it works fine."""
    network = network.lower()
    if not network_enabled(network):
        return []
    symbols = [NETWORKS[network]["native"]]
    from app.tools.market.paraswap import trusted_symbols as _paraswap_trusted
    for symbol in _paraswap_trusted(network):
        if symbol not in symbols:
            symbols.append(symbol)
    if network in eurc_networks() and "EURC" not in symbols:
        symbols.append("EURC")
    return symbols


def resolve_extra_token(symbol: str, network: str) -> tuple[str, int] | None:
    """Non-swap-routable tokens Sara still recognizes for balance display and
    plain sends - currently just EURC. Kept out of
    app.tools.market.paraswap's per-chain token table on purpose: that table
    doubles as Paraswap's own swap-routing registry, so adding a symbol
    there would also make it swappable, and Arc isn't even in Paraswap's
    chain-id map at all. Returns (address, decimals) or None."""
    symbol = symbol.upper()
    network = network.lower()
    if symbol == "EURC" and token_enabled("EURC", network):
        return (EURC_ADDRESSES[network], EURC_DECIMALS)
    return None


def serialize_preferences() -> dict:
    enabled = set(enabled_networks())
    usdc = set(usdc_networks())
    return {
        "networks": [
            {
                "id": network,
                **details,
                "enabled": network in enabled,
                "usdc_enabled": network in usdc,
            }
            for network, details in NETWORKS.items()
        ]
    }
