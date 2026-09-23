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


def network_enabled(network: str) -> bool:
    return network.lower() in enabled_networks()


def token_enabled(symbol: str, network: str) -> bool:
    network = network.lower()
    if not network_enabled(network):
        return False
    if symbol.upper() == NETWORKS[network]["native"]:
        return True
    return symbol.upper() == "USDC" and network in usdc_networks()


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
