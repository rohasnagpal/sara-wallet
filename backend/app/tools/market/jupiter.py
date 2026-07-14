import os, base64, requests

BASE = "https://lite-api.jup.ag/swap/v1"

TOKEN_MINTS = {
    "SOL":     "So11111111111111111111111111111111111111112",
    "USDC":    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT":    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "BONK":    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "JUP":     "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "RAY":     "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "WIF":     "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "JITOSOL": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
    "PYTH":    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "RNDR":    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
}

TOKEN_DECIMALS = {
    "SOL": 9, "USDC": 6, "USDT": 6, "BONK": 5,
    "JUP": 6, "RAY": 6, "WIF": 6, "JITOSOL": 9,
    "PYTH": 6, "RNDR": 8,
}


def resolve_mint(symbol: str) -> str | None:
    return TOKEN_MINTS.get(symbol.upper())


def get_decimals(symbol: str) -> int:
    return TOKEN_DECIMALS.get(symbol.upper(), 9)


def get_quote(input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50) -> dict | None:
    try:
        r = requests.get(f"{BASE}/quote", params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps,
        }, timeout=10)
        return r.json() if r.ok else None
    except Exception:
        return None


def get_swap_transaction(quote: dict, user_public_key: str) -> str | None:
    try:
        r = requests.post(f"{BASE}/swap", json={
            "quoteResponse": quote,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }, timeout=12)
        return r.json().get("swapTransaction") if r.ok else None
    except Exception:
        return None


def execute_swap(private_key_bytes: bytes, tx_b64: str) -> str:
    from solders.transaction import VersionedTransaction
    from solders.keypair import Keypair
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts

    rpc_url = os.getenv("HELIUS_RPC") or "https://api.mainnet-beta.solana.com"
    keypair = Keypair.from_bytes(private_key_bytes)
    raw = base64.b64decode(tx_b64)
    tx = VersionedTransaction.from_bytes(raw)
    signed_tx = VersionedTransaction(tx.message, [keypair])
    client = Client(rpc_url)
    resp = client.send_raw_transaction(
        bytes(signed_tx),
        opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"),
    )
    return str(resp.value)
