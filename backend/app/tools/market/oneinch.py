import os, requests
from web3 import Web3

_BASE_URL = "https://api.1inch.dev/swap/v6.0"
_NATIVE    = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

CHAIN_IDS = {
    "ethereum": 1, "polygon": 137, "arbitrum": 42161,
    "base": 8453, "optimism": 10,
}

NATIVE_SYMBOLS = {
    "ethereum": "ETH", "arbitrum": "ETH", "base": "ETH", "optimism": "ETH",
    "polygon":  "POL",
}

# Well-known token addresses per chain_id
_TOKENS: dict[int, dict[str, str]] = {
    1: {
        "USDC":  "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT":  "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "WETH":  "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "DAI":   "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC":  "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "LINK":  "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "UNI":   "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    },
    137: {
        "USDC":  "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "USDT":  "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "WETH":  "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
        "DAI":   "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
        "WBTC":  "0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6",
        "LINK":  "0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39",
        "WPOL":  "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
    },
    42161: {
        "USDC":  "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT":  "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "WETH":  "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "DAI":   "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        "WBTC":  "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "LINK":  "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
    },
    8453: {
        "USDC":  "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "WETH":  "0x4200000000000000000000000000000000000006",
        "DAI":   "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    },
    10: {
        "USDC":  "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "USDT":  "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        "WETH":  "0x4200000000000000000000000000000000000006",
        "DAI":   "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
        "WBTC":  "0x68f180fcCe6836688e9084f035309E29Bf0A2095",
        "OP":    "0x4200000000000000000000000000000000000042",
        "LINK":  "0x350a791Bfc2C21F9Ed5d10980Dad2e2638ffa7f6",
    },
}

# 1inch v6 router (same address on all chains)
ROUTER = "0x111111125421cA6dc452d289314280a0f8842A65"
_ERC20_ABI = [
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
     "name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]


def resolve_token(symbol: str, network: str) -> str | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    native = NATIVE_SYMBOLS.get(network.lower(), "ETH")
    if symbol.upper() == native:
        return _NATIVE
    return _TOKENS.get(chain_id, {}).get(symbol.upper())


def _headers() -> dict:
    key = os.getenv("ONEINCH_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def get_quote(src: str, dst: str, amount_wei: int, network: str) -> dict | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    try:
        r = requests.get(
            f"{_BASE_URL}/{chain_id}/quote",
            params={"src": src, "dst": dst, "amount": str(amount_wei)},
            headers=_headers(), timeout=8,
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def get_swap_tx(src: str, dst: str, amount_wei: int, from_addr: str,
                network: str, slippage: float = 1.0) -> dict | None:
    chain_id = CHAIN_IDS.get(network.lower())
    if not chain_id:
        return None
    try:
        r = requests.get(
            f"{_BASE_URL}/{chain_id}/swap",
            params={
                "src": src, "dst": dst, "amount": str(amount_wei),
                "from": from_addr, "slippage": slippage,
                "disableEstimate": "true",
            },
            headers=_headers(), timeout=10,
        )
        return r.json() if r.ok else None
    except Exception:
        return None


def ensure_allowance(private_key: str, token_addr: str, amount_wei: int, network: str) -> str | None:
    """Approves the 1inch router if allowance is insufficient. Returns tx hash or None."""
    from app.chains.evm import get_web3, _CHAIN_IDS
    if token_addr == _NATIVE:
        return None  # native doesn't need approval
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI
    )
    allowance = contract.functions.allowance(account.address, ROUTER).call()
    if allowance >= amount_wei:
        return None  # already approved
    # Send approval
    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract.functions.approve(ROUTER, 2**256 - 1).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": 60000,
        "gasPrice": w3.eth.gas_price,
        "chainId": _CHAIN_IDS.get(network.lower(), 1),
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex()


def execute_swap(private_key: str, swap_data: dict, network: str) -> str:
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    tx_raw = swap_data["tx"]
    tx = {
        "from":     account.address,
        "to":       Web3.to_checksum_address(tx_raw["to"]),
        "data":     tx_raw["data"],
        "value":    int(tx_raw.get("value", 0)),
        "gas":      int(tx_raw.get("gas", 300000)),
        "gasPrice": w3.eth.gas_price,
        "nonce":    w3.eth.get_transaction_count(account.address),
        "chainId":  _CHAIN_IDS.get(network.lower(), 1),
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()
