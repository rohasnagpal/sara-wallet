from eth_account import Account
from solders.keypair import Keypair

def generate_evm_wallet() -> dict:
    acct = Account.create()
    return {"address": acct.address, "private_key": acct.key.hex()}

def generate_solana_wallet() -> dict:
    kp = Keypair()
    return {
        "address": str(kp.pubkey()),
        "private_key_bytes": bytes(kp),
    }

def generate_tron_wallet() -> dict:
    from app.chains.tron import generate_wallet
    return generate_wallet()
