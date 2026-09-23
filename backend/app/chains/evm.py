from web3 import Web3
import os
from app.core.amounts import to_base_units

_RPC = {
    "ethereum":  os.getenv("ETH_RPC")  or "https://ethereum.publicnode.com",
    "arbitrum":  os.getenv("ARB_RPC")  or "https://arb1.arbitrum.io/rpc",
    "base":      os.getenv("BASE_RPC") or "https://mainnet.base.org",
    "polygon":   os.getenv("POLY_RPC") or "https://polygon-bor-rpc.publicnode.com",
    "optimism":  os.getenv("OP_RPC")   or "https://mainnet.optimism.io",
    "arc":       os.getenv("ARC_RPC")  or "https://rpc.mainnet.arc.io",
}

_CHAIN_IDS = {
    "ethereum": 1, "arbitrum": 42161, "base": 8453,
    "polygon": 137, "optimism": 10, "arc": 5042,
}

_NATIVE_TOKEN = {
    "ethereum":  "ETH",
    "arbitrum":  "ETH",
    "base":      "ETH",
    "optimism":  "ETH",
    "polygon":   "POL",
    "arc":       "USDC",  # Arc pays gas in USDC itself - no separate native token
}

# Chains Alchemy's API supports — shared by reconcile.py (asset-transfer
# lookups) and tx_simulate.py (pre-signing simulation). bsc/avalanche are
# supported EVM networks Sara can send on.
ALCHEMY_NETWORK_SLUGS = {
    "ethereum": "eth-mainnet", "polygon": "polygon-mainnet", "arbitrum": "arb-mainnet",
    "base": "base-mainnet", "optimism": "opt-mainnet",
}

def get_web3(network: str = "ethereum") -> Web3:
    network = network.lower()
    from app.core.assets import network_enabled
    if not network_enabled(network):
        raise ValueError(f"network is disabled: {network}")
    if network not in _RPC:
        raise ValueError(f"unsupported EVM network: {network}")
    url = _RPC[network]
    w3 = Web3(Web3.HTTPProvider(url))
    if not w3.is_connected():
        raise ConnectionError(f"could not connect to {network} RPC")
    return w3

def get_balance(address: str, network: str = "ethereum") -> dict:
    w3 = get_web3(network)
    raw = w3.eth.get_balance(Web3.to_checksum_address(address))
    bal = float(w3.from_wei(raw, "ether"))
    unit = _NATIVE_TOKEN.get(network.lower(), "ETH")
    return {"network": network, "address": address, "balance": bal, "unit": unit}

def get_native_transfer_preview_raw(address: str, amount_wei: int, network: str = "ethereum") -> dict:
    network = network.lower()
    w3 = get_web3(network)
    checksum = Web3.to_checksum_address(address)
    raw_balance = w3.eth.get_balance(checksum)
    gas_price = int(w3.eth.gas_price * 1.2)  # margin for gas-price drift between preview and confirm, same as the ERC20 preview/Paraswap fix
    gas_limit = 21000
    fee_wei = gas_price * gas_limit
    if amount_wei <= 0:
        raise ValueError("amount must be positive")
    total_wei = amount_wei + fee_wei
    unit = _NATIVE_TOKEN.get(network, "ETH")
    return {
        "network": network,
        "address": checksum,
        "balance": float(w3.from_wei(raw_balance, "ether")),
        "amount": float(w3.from_wei(amount_wei, "ether")),
        "fee": float(w3.from_wei(fee_wei, "ether")),
        "total": float(w3.from_wei(total_wei, "ether")),
        "has_funds": raw_balance >= total_wei,
        "unit": unit,
    }


def get_native_transfer_preview(address: str, amount_eth: float, network: str = "ethereum") -> dict:
    amount_wei = to_base_units(amount_eth, 18, _NATIVE_TOKEN.get(network.lower(), "native asset"))
    return get_native_transfer_preview_raw(address, amount_wei, network)

_ERC20_TRANSFER_SELECTOR = "a9059cbb"
_ERC20_BALANCEOF_SELECTOR = "70a08231"


def _encode_address(address: str) -> str:
    return Web3.to_checksum_address(address)[2:].lower().zfill(64)


def _encode_uint(value: int) -> str:
    return hex(value)[2:].zfill(64)


def _get_erc20_balance_raw(token_address: str, wallet_address: str,
                           network: str = "ethereum") -> int:
    w3 = get_web3(network)
    calldata = "0x" + _ERC20_BALANCEOF_SELECTOR + _encode_address(wallet_address)
    result = w3.eth.call({"to": Web3.to_checksum_address(token_address), "data": calldata})
    return int.from_bytes(result, "big")


def get_erc20_balance(token_address: str, decimals: int, wallet_address: str, network: str = "ethereum") -> float:
    """Read an ERC-20 balance directly via balanceOf() — no external API/key needed."""
    return _get_erc20_balance_raw(token_address, wallet_address, network) / (10 ** decimals)


def get_erc20_transfer_preview_raw(token_address: str, decimals: int, wallet_address: str,
                                   amount_raw: int, to: str, network: str = "ethereum") -> dict:
    """Preview an ERC-20 send: token balance (for the transfer amount) and native
    balance (for gas) are two separate currencies — both must be checked."""
    network = network.lower()
    w3 = get_web3(network)
    checksum = Web3.to_checksum_address(wallet_address)
    token_balance_raw = _get_erc20_balance_raw(token_address, wallet_address, network)
    token_balance = token_balance_raw / (10 ** decimals)
    if amount_raw <= 0:
        raise ValueError("amount must be positive")
    calldata = "0x" + _ERC20_TRANSFER_SELECTOR + _encode_address(to) + _encode_uint(amount_raw)
    gas_price = w3.eth.gas_price
    try:
        gas_estimate = w3.eth.estimate_gas({
            "from": checksum,
            "to": Web3.to_checksum_address(token_address),
            "data": calldata,
        })
    except Exception:
        gas_estimate = 65000  # fallback if estimation reverts before a real balance/allowance check
    gas_limit = int(gas_estimate * 1.2)
    fee_wei = gas_price * gas_limit
    native_balance_wei = w3.eth.get_balance(checksum)
    native_unit = _NATIVE_TOKEN.get(network, "ETH")
    return {
        "network": network,
        "amount": amount_raw / (10 ** decimals),
        "token_balance": token_balance,
        "has_token_funds": token_balance_raw >= amount_raw,
        "gas_fee": float(w3.from_wei(fee_wei, "ether")),
        "native_balance": float(w3.from_wei(native_balance_wei, "ether")),
        "has_gas_funds": native_balance_wei >= fee_wei,
        "native_unit": native_unit,
        "gas_limit": gas_limit,
        "gas_price": gas_price,
        "amount_raw": amount_raw,
    }


def get_erc20_transfer_preview(token_address: str, decimals: int, wallet_address: str,
                                amount: float, to: str, network: str = "ethereum") -> dict:
    amount_raw = to_base_units(amount, decimals, "token")
    return get_erc20_transfer_preview_raw(
        token_address, decimals, wallet_address, amount_raw, to, network,
    )


def prepare_erc20_transfer_raw(private_key: str, token_address: str, decimals: int, to: str,
                               amount_raw: int, network: str = "ethereum", nonce: int | None = None) -> dict:
    """Sign an exact-base-unit ERC-20 transfer without broadcasting it."""
    network = network.lower()
    if network not in _CHAIN_IDS:
        raise ValueError(f"unsupported EVM network: {network}")
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    preview = get_erc20_transfer_preview_raw(
        token_address, decimals, account.address, int(amount_raw), to, network,
    )
    if not preview["has_token_funds"]:
        raise ValueError("insufficient token balance")
    if not preview["has_gas_funds"]:
        raise ValueError(f"insufficient {preview['native_unit']} for gas")
    tx = {
        "nonce": w3.eth.get_transaction_count(account.address) if nonce is None else nonce,
        "to": Web3.to_checksum_address(token_address), "value": 0,
        "data": "0x" + _ERC20_TRANSFER_SELECTOR + _encode_address(to) + _encode_uint(int(amount_raw)),
        "gas": preview["gas_limit"], "gasPrice": preview["gas_price"], "chainId": _CHAIN_IDS[network],
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    raw = signed.raw_transaction
    return {"tx_hash": signed.hash.hex(), "raw_transaction": raw.hex(), "nonce": tx["nonce"]}


def prepare_native_transfer_raw(private_key: str, to: str, amount_wei: int,
                                network: str = "ethereum", nonce: int | None = None) -> dict:
    """Sign an exact-wei native transfer without broadcasting it."""
    network = network.lower()
    if network not in _CHAIN_IDS:
        raise ValueError(f"unsupported EVM network: {network}")
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    preview = get_native_transfer_preview_raw(account.address, int(amount_wei), network)
    if not preview["has_funds"]:
        raise ValueError("insufficient funds for amount plus gas")
    tx = {
        "nonce": w3.eth.get_transaction_count(account.address) if nonce is None else nonce,
        "to": Web3.to_checksum_address(to), "value": int(amount_wei), "gas": 21000,
        "gasPrice": w3.eth.gas_price, "chainId": _CHAIN_IDS[network],
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return {"tx_hash": signed.hash.hex(), "raw_transaction": signed.raw_transaction.hex(), "nonce": tx["nonce"]}


def broadcast_raw_transaction(network: str, raw_transaction: str) -> str:
    w3 = get_web3(network)
    raw = bytes.fromhex(raw_transaction.removeprefix("0x"))
    expected = Web3.keccak(raw).hex()
    try:
        return w3.eth.send_raw_transaction(raw).hex()
    except Exception as exc:
        message = str(exc).lower()
        if "already known" in message or "known transaction" in message:
            return expected
        raise


def send_erc20_tx(private_key: str, token_address: str, decimals: int, to: str,
                   amount: float, network: str = "ethereum") -> str:
    prepared = prepare_erc20_transfer_raw(
        private_key, token_address, decimals, to, to_base_units(amount, decimals, "token"), network,
    )
    return broadcast_raw_transaction(network, prepared["raw_transaction"])


def send_tx(private_key: str, to: str, amount_eth: float, network: str = "ethereum") -> str:
    prepared = prepare_native_transfer_raw(
        private_key, to, to_base_units(amount_eth, 18, _NATIVE_TOKEN.get(network.lower(), "native asset")), network,
    )
    return broadcast_raw_transaction(network, prepared["raw_transaction"])
