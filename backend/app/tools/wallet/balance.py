from app.chains import evm
from app.tools.wallet.encrypt import decrypt_key
from app.db.models import Wallet

def get_wallet_balance(wallet: Wallet, network: str = None) -> dict:
    net = network or "ethereum"
    return evm.get_balance(wallet.address, net)
