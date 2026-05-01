from web3 import Web3
import os

_RPC = {
    "ethereum":  os.getenv("ETH_RPC")  or "https://ethereum.publicnode.com",
    "arbitrum":  os.getenv("ARB_RPC")  or "https://arb1.arbitrum.io/rpc",
    "base":      os.getenv("BASE_RPC") or "https://mainnet.base.org",
    "polygon":   os.getenv("POLY_RPC") or "https://polygon-bor-rpc.publicnode.com",
    "optimism":  os.getenv("OP_RPC")   or "https://mainnet.optimism.io",
}

_CHAIN_IDS = {
    "ethereum": 1, "arbitrum": 42161, "base": 8453,
    "polygon": 137, "optimism": 10,
}

def get_web3(network: str = "ethereum") -> Web3:
    url = _RPC.get(network.lower(), _RPC["ethereum"])
    w3 = Web3(Web3.HTTPProvider(url))
    return w3

def get_balance(address: str, network: str = "ethereum") -> dict:
    w3 = get_web3(network)
    raw = w3.eth.get_balance(Web3.to_checksum_address(address))
    eth = float(w3.from_wei(raw, "ether"))
    return {"network": network, "address": address, "balance": eth, "unit": "ETH"}

def send_tx(private_key: str, to: str, amount_eth: float, network: str = "ethereum") -> str:
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    nonce = w3.eth.get_transaction_count(account.address)
    gas_price = w3.eth.gas_price
    chain_id = _CHAIN_IDS.get(network.lower(), 1)
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
