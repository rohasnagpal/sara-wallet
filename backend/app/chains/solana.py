from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
import os

_RPC = os.getenv("HELIUS_RPC") or "https://api.mainnet-beta.solana.com"

def get_client() -> Client:
    return Client(_RPC)

def get_balance(address: str) -> dict:
    client = get_client()
    pubkey = Pubkey.from_string(address)
    resp = client.get_balance(pubkey)
    lamports = resp.value
    sol = lamports / 1_000_000_000
    return {"network": "solana", "address": address, "balance": sol, "unit": "SOL"}

def send_tx(private_key_bytes: bytes, to: str, amount_sol: float) -> str:
    from solders.system_program import transfer, TransferParams
    from solders.transaction import Transaction
    from solders.message import Message
    from solders.hash import Hash

    client = get_client()
    keypair = Keypair.from_bytes(private_key_bytes)
    to_pubkey = Pubkey.from_string(to)
    lamports = int(amount_sol * 1_000_000_000)

    blockhash_resp = client.get_latest_blockhash()
    recent_blockhash = blockhash_resp.value.blockhash

    ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=to_pubkey, lamports=lamports))
    msg = Message.new_with_blockhash([ix], keypair.pubkey(), recent_blockhash)
    tx = Transaction([keypair], msg, recent_blockhash)
    resp = client.send_transaction(tx)
    return str(resp.value)
