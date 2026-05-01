from app.chains import evm, solana as sol_chain
from app.tools.wallet.encrypt import decrypt_key
from app.db.models import Wallet, Transaction
from app.db.session import SessionLocal
from datetime import datetime

def execute_send(wallet: Wallet, to: str, amount: float, network: str = None) -> dict:
    db = SessionLocal()
    try:
        plain_key = decrypt_key(wallet.encrypted_key)
        if wallet.chain == "evm":
            net = network or "ethereum"
            tx_hash = evm.send_tx(plain_key, to, amount, net)
        else:
            key_bytes = bytes.fromhex(plain_key)
            tx_hash = sol_chain.send_tx(key_bytes, to, amount)

        record = Transaction(
            wallet_id=wallet.id,
            chain=wallet.chain,
            tx_hash=tx_hash,
            to_address=to,
            amount=amount,
            status="confirmed",
            timestamp=datetime.utcnow(),
        )
        db.add(record)
        db.commit()
        return {"tx_hash": tx_hash, "status": "confirmed"}
    except Exception as e:
        db.rollback()
        return {"error": str(e), "status": "failed"}
    finally:
        db.close()
