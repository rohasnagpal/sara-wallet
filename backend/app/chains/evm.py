from web3 import Web3
import os

_RPC = {
    "ethereum":  os.getenv("ETH_RPC")  or "https://ethereum.publicnode.com",
    "arbitrum":  os.getenv("ARB_RPC")  or "https://arb1.arbitrum.io/rpc",
    "base":      os.getenv("BASE_RPC") or "https://mainnet.base.org",
    "polygon":   os.getenv("POLY_RPC") or "https://polygon-bor-rpc.publicnode.com",
    "optimism":  os.getenv("OP_RPC")   or "https://mainnet.optimism.io",
    "bsc":       os.getenv("BSC_RPC")  or "https://bsc-dataseed.binance.org",
    "avalanche": os.getenv("AVAX_RPC") or "https://api.avax.network/ext/bc/C/rpc",
}

_CHAIN_IDS = {
    "ethereum": 1, "arbitrum": 42161, "base": 8453,
    "polygon": 137, "optimism": 10,
    "bsc": 56, "avalanche": 43114,
}

_NATIVE_TOKEN = {
    "ethereum":  "ETH",
    "arbitrum":  "ETH",
    "base":      "ETH",
    "optimism":  "ETH",
    "polygon":   "POL",
    "bsc":       "BNB",
    "avalanche": "AVAX",
}

def get_web3(network: str = "ethereum") -> Web3:
    network = network.lower()
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

def get_native_transfer_preview(address: str, amount_eth: float, network: str = "ethereum") -> dict:
    network = network.lower()
    w3 = get_web3(network)
    checksum = Web3.to_checksum_address(address)
    raw_balance = w3.eth.get_balance(checksum)
    gas_price = w3.eth.gas_price
    gas_limit = 21000
    fee_wei = gas_price * gas_limit
    value_wei = w3.to_wei(amount_eth, "ether")
    total_wei = value_wei + fee_wei
    unit = _NATIVE_TOKEN.get(network, "ETH")
    return {
        "network": network,
        "address": checksum,
        "balance": float(w3.from_wei(raw_balance, "ether")),
        "amount": amount_eth,
        "fee": float(w3.from_wei(fee_wei, "ether")),
        "total": float(w3.from_wei(total_wei, "ether")),
        "has_funds": raw_balance >= total_wei,
        "unit": unit,
    }

def send_tx(private_key: str, to: str, amount_eth: float, network: str = "ethereum") -> str:
    network = network.lower()
    if network not in _CHAIN_IDS:
        raise ValueError(f"unsupported EVM network: {network}")
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    preview = get_native_transfer_preview(account.address, amount_eth, network)
    if not preview["has_funds"]:
        raise ValueError(
            f"insufficient funds for amount plus gas: {preview['balance']:.6f} {preview['unit']} "
            f"available, {preview['total']:.6f} {preview['unit']} required"
        )
    nonce = w3.eth.get_transaction_count(account.address)
    gas_price = w3.eth.gas_price
    chain_id = _CHAIN_IDS[network]
    tx = {
        "nonce": nonce,
        "to": Web3.to_checksum_address(to),
        "value": w3.to_wei(amount_eth, "ether"),
        "gas": 21000,
        "gasPrice": gas_price,
        "chainId": chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()
