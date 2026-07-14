import requests
from web3 import Web3

_BASE = "https://apiv5.paraswap.io"

CHAIN_IDS = {
    "ethereum": 1, "polygon": 137, "arbitrum": 42161,
    "base": 8453, "optimism": 10,
}

NATIVE_SYMBOLS = {
    "ethereum": "ETH", "arbitrum": "ETH", "base": "ETH", "optimism": "ETH",
    "polygon": "POL",
}

_NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

_TOKENS: dict[int, dict[str, tuple[str, int]]] = {  # symbol → (address, decimals)
    1: {
        "USDC":  ("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
        "USDT":  ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
        "WETH":  ("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18),
        "DAI":   ("0x6B175474E89094C44Da98b954EedeAC495271d0F", 18),
        "WBTC":  ("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),
        "LINK":  ("0x514910771AF9Ca656af840dff83E8264EcF986CA", 18),
        "UNI":   ("0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", 18),
    },
    137: {
        "USDC":  ("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
        "USDT":  ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6),
        "WETH":  ("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),
        "DAI":   ("0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18),
        "WBTC":  ("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),
        "LINK":  ("0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18),
        "WPOL":  ("0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270", 18),
    },
    42161: {
        "USDC":  ("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
        "USDT":  ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6),
        "WETH":  ("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18),
        "DAI":   ("0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", 18),
        "WBTC":  ("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", 8),
        "LINK":  ("0xf97f4df75117a78c1A5a0DBb814Af92458539FB4", 18),
    },
    8453: {
        "USDC":  ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
        "WETH":  ("0x4200000000000000000000000000000000000006", 18),
        "DAI":   ("0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", 18),
    },
    10: {
        "USDC":  ("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6),
        "USDT":  ("0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", 6),
        "WETH":  ("0x4200000000000000000000000000000000000006", 18),
        "DAI":   ("0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", 18),
        "WBTC":  ("0x68f180fcCe6836688e9084f035309E29Bf0A2095", 8),
        "OP":    ("0x4200000000000000000000000000000000000042", 18),
        "LINK":  ("0x350a791Bfc2C21F9Ed5d10980Dad2e2638ffa7f6", 18),
    },
}

_PARASWAP_SPENDER_ABI = [
    {"inputs":[],"name":"getTokenTransferProxy","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"}
]
_AUGUSTUS_ADDRESSES = {
    1: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    137: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    42161: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    8453: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
    10: "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
}
_ERC20_ABI = [
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
     "name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]


def resolve_token(symbol: str, network: str) -> tuple[str, int] | None:
    """Returns (address, decimals) or None."""
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    native = NATIVE_SYMBOLS.get(network.lower(), "ETH")
    if symbol.upper() == native:
        return (_NATIVE, 18)
    entry = _TOKENS.get(chain_id, {}).get(symbol.upper())
    return entry  # (address, decimals) or None


def get_quote(src: str, src_dec: int, dst: str, dst_dec: int,
              amount_wei: int, network: str) -> dict | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    try:
        r = requests.get(f"{_BASE}/prices", params={
            "srcToken": src, "destToken": dst,
            "srcDecimals": src_dec, "destDecimals": dst_dec,
            "amount": str(amount_wei), "side": "SELL",
            "network": chain_id,
        }, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def get_swap_tx(price_route: dict, src: str, dst: str,
                src_amount: str,
                user_addr: str, network: str, slippage_bps: int = 100) -> dict | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    try:
        r = requests.post(
            f"{_BASE}/transactions/{chain_id}",
            params={"ignoreChecks": "true"},
            json={
                "srcToken": src, "destToken": dst,
                "srcAmount": src_amount,
                "priceRoute": price_route,
                "userAddress": user_addr,
                "receiver": user_addr,
                "slippage": slippage_bps,
                "txOrigin": user_addr,
            },
            timeout=12,
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def ensure_allowance(private_key: str, token_addr: str, amount_wei: int,
                     network: str) -> str | None:
    if token_addr == _NATIVE:
        return None
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    chain_id = _CHAIN_IDS.get(network.lower(), 1)
    account = w3.eth.account.from_key(private_key)
    spender = _AUGUSTUS_ADDRESSES.get(chain_id, _AUGUSTUS_ADDRESSES[1])
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    if contract.functions.allowance(account.address, spender).call() >= amount_wei:
        return None
    tx = contract.functions.approve(spender, 2**256 - 1).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 60000,
        "gasPrice": int(w3.eth.gas_price * 1.2),  # margin for L2 base-fee drift between fetch and submit
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex()


def execute_swap(private_key: str, tx_data: dict, network: str) -> str:
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    tx = {
        "from":     account.address,
        "to":       Web3.to_checksum_address(tx_data["to"]),
        "data":     tx_data["data"],
        "value":    int(tx_data.get("value", 0)),
        "gas":      int(tx_data.get("gas", 300000)),
        "gasPrice": int(w3.eth.gas_price * 1.2),  # margin for L2 base-fee drift between fetch and submit
        "nonce":    w3.eth.get_transaction_count(account.address),
        "chainId":  _CHAIN_IDS.get(network.lower(), 1),
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()
