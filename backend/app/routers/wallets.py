from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.session import SessionLocal
from app.db.models import Wallet
from app.tools.wallet.keygen import generate_evm_wallet, generate_solana_wallet
from app.tools.wallet.encrypt import encrypt_key, decrypt_key
from app.tools.wallet.lock import WalletLockedError, unlock as verify_passphrase
from app.tools.wallet.balance import get_wallet_balance
from app.tools.wallet.send import execute_send

router = APIRouter(prefix="/wallets", tags=["wallets"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class CreateWalletRequest(BaseModel):
    name: str
    chain: str  # "evm" | "solana"

class ImportWalletRequest(BaseModel):
    name: str
    chain: str
    private_key: str  # hex string

class SendRequest(BaseModel):
    to: str
    amount: float
    network: Optional[str] = None

class RenameWalletRequest(BaseModel):
    name: str

class ExportWalletRequest(BaseModel):
    passphrase: str

@router.post("/create")
def create_wallet(req: CreateWalletRequest, db: Session = Depends(get_db)):
    if db.query(Wallet).filter(Wallet.name == req.name).first():
        raise HTTPException(400, "Wallet name already exists")
    chain = req.chain.lower()
    try:
        if chain == "evm":
            w = generate_evm_wallet()
            encrypted = encrypt_key(w["private_key"])
            address = w["address"]
        elif chain == "solana":
            w = generate_solana_wallet()
            encrypted = encrypt_key(w["private_key_bytes"].hex())
            address = w["address"]
        else:
            raise HTTPException(400, "chain must be 'evm' or 'solana'")
    except WalletLockedError as e:
        raise HTTPException(423, str(e))

    wallet = Wallet(name=req.name, chain=chain, address=address, encrypted_key=encrypted)
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return {"id": wallet.id, "name": wallet.name, "chain": wallet.chain, "address": wallet.address}

@router.post("/import")
def import_wallet(req: ImportWalletRequest, db: Session = Depends(get_db)):
    if db.query(Wallet).filter(Wallet.name == req.name).first():
        raise HTTPException(400, "Wallet name already exists")
    chain = req.chain.lower()
    if chain == "evm":
        from eth_account import Account
        try:
            acct = Account.from_key(req.private_key)
            address = acct.address
        except Exception:
            raise HTTPException(400, "Invalid EVM private key")
    elif chain == "solana":
        from solders.keypair import Keypair
        try:
            kp = Keypair.from_bytes(bytes.fromhex(req.private_key))
            address = str(kp.pubkey())
        except Exception:
            raise HTTPException(400, "Invalid Solana private key (hex bytes expected)")
    else:
        raise HTTPException(400, "chain must be 'evm' or 'solana'")

    try:
        encrypted = encrypt_key(req.private_key)
    except WalletLockedError as e:
        raise HTTPException(423, str(e))
    wallet = Wallet(name=req.name, chain=chain, address=address, encrypted_key=encrypted)
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return {"id": wallet.id, "name": wallet.name, "chain": wallet.chain, "address": wallet.address}

@router.get("")
def list_wallets(db: Session = Depends(get_db)):
    wallets = db.query(Wallet).all()
    return [{"id": w.id, "name": w.name, "chain": w.chain, "address": w.address} for w in wallets]

@router.patch("/{wallet_id}")
def rename_wallet(wallet_id: int, req: RenameWalletRequest, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(400, "Wallet name cannot be blank")
    if new_name != w.name and db.query(Wallet).filter(Wallet.name == new_name).first():
        raise HTTPException(400, "Wallet name already exists")
    w.name = new_name
    db.commit()
    return {"id": w.id, "name": w.name, "chain": w.chain, "address": w.address}

@router.delete("/{wallet_id}")
def delete_wallet(wallet_id: int, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    db.delete(w)
    db.commit()
    return {"deleted": wallet_id}

@router.post("/{wallet_id}/export")
def export_wallet(wallet_id: int, req: ExportWalletRequest, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    if not req.passphrase or not req.passphrase.strip():
        raise HTTPException(400, "Passphrase cannot be blank")
    if not verify_passphrase(req.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    try:
        private_key = decrypt_key(w.encrypted_key)
    except ValueError as e:
        raise HTTPException(500, str(e))
    return {"id": w.id, "name": w.name, "chain": w.chain, "address": w.address, "private_key": private_key}

@router.get("/{wallet_id}/balance")
def wallet_balance(wallet_id: int, network: Optional[str] = None, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    try:
        return get_wallet_balance(w, network)
    except Exception as e:
        raise HTTPException(502, str(e))

@router.post("/{wallet_id}/send")
def wallet_send(wallet_id: int, req: SendRequest, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    try:
        result = execute_send(w, req.to, req.amount, req.network)
    except WalletLockedError as e:
        raise HTTPException(423, str(e))
    if result.get("status") == "failed":
        raise HTTPException(502, result.get("error", "send failed"))
    return result
