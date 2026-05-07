from app.chains import evm, solana as sol_chain
from app.tools.wallet.encrypt import decrypt_key
from app.db.models import Wallet, Transaction
from app.db.session import SessionLocal
from datetime import datetime
import re

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _exception_message(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    if getattr(exc, "args", None):
        return " ".join(str(arg) for arg in exc.args if str(arg).strip()) or repr(exc)
    return repr(exc)


def _validate_recipient(to: str, chain: str) -> None:
    if chain == "solana":
        from solders.pubkey import Pubkey
        Pubkey.from_string(to)
    elif not _EVM_ADDRESS_RE.fullmatch(to or ""):
        raise ValueError("recipient is not a valid EVM address")

def execute_send(wallet: Wallet, to: str, amount: float, network: str = None) -> dict:
    db = SessionLocal()
    try:
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        _validate_recipient(to, wallet.chain)
        plain_key = decrypt_key(wallet.encrypted_key)
        if wallet.chain == "evm":
            net = network or "ethereum"
            bal = evm.get_native_transfer_preview(wallet.address, amount, net)
            if not bal["has_funds"]:
                raise ValueError(
                    f"insufficient funds for amount plus gas: {bal['balance']:.6f} {bal['unit']} "
                    f"available, {bal['total']:.6f} {bal['unit']} required"
                )
            tx_hash = evm.send_tx(plain_key, to, amount, net)
        else:
            bal = sol_chain.get_balance(wallet.address)
            if bal["balance"] < amount:
                raise ValueError(f"insufficient balance: {bal['balance']:.6f} {bal['unit']} available")
            key_bytes = bytes.fromhex(plain_key)
            tx_hash = sol_chain.send_tx(key_bytes, to, amount)

        record = Transaction(
            wallet_id=wallet.id,
            chain=wallet.chain,
            tx_hash=tx_hash,
            to_address=to,
            amount=amount,
            status="submitted",
            timestamp=datetime.utcnow(),
        )
        db.add(record)
        db.commit()
        return {"tx_hash": tx_hash, "status": "submitted"}
    except Exception as e:
        db.rollback()
        return {"error": _exception_message(e), "status": "failed"}
    finally:
        db.close()
