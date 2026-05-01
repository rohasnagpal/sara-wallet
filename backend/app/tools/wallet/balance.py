from app.chains import evm, solana as sol_chain
from app.tools.wallet.encrypt import decrypt_key
from app.db.models import Wallet

def get_wallet_balance(wallet: Wallet, network: str = None) -> dict:
    if wallet.chain == "evm":
        net = network or "ethereum"
        return evm.get_balance(wallet.address, net)
    else:
        return sol_chain.get_balance(wallet.address)
