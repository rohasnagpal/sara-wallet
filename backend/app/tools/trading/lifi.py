"""Cross-chain swaps via LI.FI (https://li.quest) — bridges + DEXs aggregated
behind one API. This is a separate, independent integration from
paraswap.py (same-chain EVM swaps) and jupiter.py (same-chain Solana swaps);
it doesn't share code with either and doesn't touch them.

LI.FI only supports EVM chains — no Solana route exists here.
"""
import requests
from web3 import Web3

_BASE = "https://li.quest/v1"

# Same numeric chain IDs as backend/app/chains/evm.py — these are standard,
# public EVM chain IDs, not something LI.FI-specific.
CHAIN_IDS = {
    "ethereum": 1, "polygon": 137, "arbitrum": 42161,
    "base": 8453, "optimism": 10, "bsc": 56, "avalanche": 43114,
}

# LI.FI represents the native asset as the zero address — confirmed against
# the live API, and different from Paraswap's 0xEeee... convention, so this
# can't be blindly shared between the two integrations.
_NATIVE = "0x0000000000000000000000000000000000000000"


def resolve_token(symbol: str, network: str) -> tuple[str, int] | None:
    """Native asset uses LI.FI's zero-address convention; ERC-20 tokens reuse
    the already-verified address list in paraswap.py (contract addresses
    aren't aggregator-specific, unlike the native-asset placeholder)."""
    from app.chains.evm import _NATIVE_TOKEN
    native_symbol = _NATIVE_TOKEN.get(network.lower())
    if native_symbol and symbol.upper() == native_symbol:
        return (_NATIVE, 18)
    from app.tools.market.paraswap import resolve_token as _paraswap_resolve
    return _paraswap_resolve(symbol, network)


_ERC20_ABI = [
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]


def get_quote(from_network: str, to_network: str, from_token: str, to_token: str,
              amount_wei: int, from_address: str, slippage: float = 0.005) -> dict | None:
    from_chain = CHAIN_IDS.get(from_network.lower())
    to_chain = CHAIN_IDS.get(to_network.lower())
    if not from_chain or not to_chain:
        return None
    try:
        r = requests.get(f"{_BASE}/quote", params={
            "fromChain": from_chain, "toChain": to_chain,
            "fromToken": from_token, "toToken": to_token,
            "fromAmount": str(amount_wei),
            "fromAddress": from_address,
            "toAddress": from_address,  # self-bridge only, never a different recipient
            "slippage": slippage,
        }, timeout=15)
        return r.json() if r.ok else None
    except Exception:
        return None


def _send_approve(w3, chain_id, account, private_key, contract, spender_addr, value):
    tx = contract.functions.approve(spender_addr, value).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash


def ensure_allowance(private_key: str, token_addr: str, spender: str, amount_wei: int, network: str) -> str | None:
    if int(token_addr, 16) == 0:  # native asset, no approval needed
        return None
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    chain_id = _CHAIN_IDS.get(network.lower(), 1)
    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    spender_addr = Web3.to_checksum_address(spender)
    current = contract.functions.allowance(account.address, spender_addr).call()
    if current >= amount_wei:
        return None
    # Some tokens (Tether's original contract, and several bridged/L2 USDT
    # deployments that copy its behavior) revert if you approve a new
    # non-zero value while the existing allowance is already non-zero — you
    # have to reset to 0 first. Also approve the exact amount needed rather
    # than an unlimited amount: safer for the user, and avoids a class of
    # contracts that reject max-uint256 approvals outright.
    last_hash = None
    if current > 0:
        last_hash = _send_approve(w3, chain_id, account, private_key, contract, spender_addr, 0)
    last_hash = _send_approve(w3, chain_id, account, private_key, contract, spender_addr, amount_wei)
    return last_hash.hex()


def execute_bridge(private_key: str, tx_request: dict, network: str) -> str:
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    tx = {
        "from":     account.address,
        "to":       Web3.to_checksum_address(tx_request["to"]),
        "data":     tx_request["data"],
        "value":    int(tx_request.get("value", "0x0"), 16) if isinstance(tx_request.get("value"), str) else int(tx_request.get("value", 0)),
        "gas":      int(tx_request.get("gasLimit", "0x493e0"), 16) if isinstance(tx_request.get("gasLimit"), str) else int(tx_request.get("gasLimit", 300000)),
        "gasPrice": int(tx_request["gasPrice"], 16) if isinstance(tx_request.get("gasPrice"), str) else w3.eth.gas_price,
        "nonce":    w3.eth.get_transaction_count(account.address),
        "chainId":  _CHAIN_IDS.get(network.lower(), 1),
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def get_status(tx_hash: str) -> dict | None:
    try:
        r = requests.get(f"{_BASE}/status", params={"txHash": tx_hash}, timeout=15)
        return r.json() if r.ok else None
    except Exception:
        return None
