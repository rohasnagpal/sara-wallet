def resolve(name: str) -> str | None:
    """Resolve an ENS name (*.eth) to a checksummed EVM address."""
    if not name.lower().endswith(".eth"):
        return None
    try:
        from app.chains.evm import get_web3
        from web3 import Web3
        w3 = get_web3("ethereum")
        addr = w3.ens.address(name)
        return Web3.to_checksum_address(addr) if addr else None
    except Exception:
        return None


def reverse_resolve(address: str) -> str | None:
    """Reverse-resolve an EVM address to its ENS name."""
    try:
        from app.chains.evm import get_web3
        w3 = get_web3("ethereum")
        return w3.ens.name(address)
    except Exception:
        return None
