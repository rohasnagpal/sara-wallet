from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.session import SessionLocal
from app.db.models import RecoverySeed, Wallet
from app.tools.wallet.encrypt import encrypt_key, decrypt_key
from app.tools.wallet.lock import WalletLockedError, unlock as verify_passphrase
from app.tools.wallet.balance import get_wallet_balance
from app.tools.wallet.seeds import SeedError, derive_wallet, generate_seed_phrase, validate_seed_phrase
from app.core.session_auth import require_session

router = APIRouter(prefix="/wallets", tags=["wallets"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class CreateWalletRequest(BaseModel):
    name: str

class ImportWalletRequest(BaseModel):
    name: str
    private_key: str  # hex string

class RenameWalletRequest(BaseModel):
    name: str

class ExportWalletRequest(BaseModel):
    passphrase: str

class RestoreSeedRequest(BaseModel):
    seed_phrase: str

class RevealSeedRequest(BaseModel):
    passphrase: str


def _recovery_seed(db: Session) -> tuple[RecoverySeed, str | None]:
    """Return the one recovery seed, generating it on first wallet use."""
    seed = db.query(RecoverySeed).first()
    if seed is not None:
        return seed, None
    phrase = generate_seed_phrase()
    seed = RecoverySeed(id=1, encrypted_seed=encrypt_key(phrase), next_index=0)
    db.add(seed)
    db.flush()  # assigns seed.id without ending the caller's transaction
    return seed, phrase


@router.post("/create", dependencies=[Depends(require_session)])
def create_wallet(req: CreateWalletRequest, db: Session = Depends(get_db)):
    if db.query(Wallet).filter(Wallet.name == req.name).first():
        raise HTTPException(400, "Wallet name already exists")
    chain = "evm"

    try:
        seed, shown_phrase = _recovery_seed(db)
        seed_phrase = decrypt_key(seed.encrypted_seed)
        index = seed.next_index
        derived = derive_wallet(seed_phrase, index)
        encrypted = encrypt_key(derived["private_key"])
        seed.next_index = index + 1
    except WalletLockedError as e:
        raise HTTPException(423, str(e))

    wallet = Wallet(
        name=req.name, chain=chain, address=derived["address"], encrypted_key=encrypted,
        derivation_index=index,
    )
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    result = {
        "id": wallet.id, "name": wallet.name, "chain": wallet.chain, "address": wallet.address,
        "derivation_index": index,
    }
    if shown_phrase:
        # Shown exactly once, at the moment the very first seed is created -
        # every wallet after this never returns it from this endpoint; use
        # POST /wallets/seed/reveal (passphrase-gated) to see it again.
        result["seed_phrase"] = shown_phrase
    return result


@router.get("/seed", dependencies=[Depends(require_session)])
def get_recovery_seed_status(db: Session = Depends(get_db)):
    seed = db.query(RecoverySeed).first()
    return {
        "configured": seed is not None,
        "wallet_count": db.query(Wallet).filter(Wallet.derivation_index.isnot(None)).count(),
        "next_index": seed.next_index if seed else 0,
        "created_at": seed.created_at.isoformat() if seed and seed.created_at else None,
    }


@router.post("/seed/restore", dependencies=[Depends(require_session)])
def restore_recovery_seed(req: RestoreSeedRequest, db: Session = Depends(get_db)):
    """Restore the one recovery phrase before creating derived wallets."""
    if db.query(RecoverySeed).first() is not None:
        raise HTTPException(409, "A recovery phrase is already configured")
    try:
        phrase = validate_seed_phrase(req.seed_phrase)
    except SeedError as e:
        raise HTTPException(400, str(e))
    try:
        seed = RecoverySeed(id=1, encrypted_seed=encrypt_key(phrase), next_index=0)
    except WalletLockedError as e:
        raise HTTPException(423, str(e))
    db.add(seed)
    db.commit()
    return {"status": "restored"}


@router.post("/seed/reveal", dependencies=[Depends(require_session)])
def reveal_seed(req: RevealSeedRequest, db: Session = Depends(get_db)):
    seed = db.query(RecoverySeed).first()
    if not seed:
        raise HTTPException(404, "Seed not found")
    if not req.passphrase or not req.passphrase.strip():
        raise HTTPException(400, "Passphrase cannot be blank")
    if not verify_passphrase(req.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    phrase = None
    try:
        phrase = decrypt_key(seed.encrypted_seed)
        return JSONResponse(
            content={"seed_phrase": phrase},
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        )
    except ValueError as e:
        raise HTTPException(500, str(e))
    finally:
        phrase = None


@router.post("/import", dependencies=[Depends(require_session)])
def import_wallet(req: ImportWalletRequest, db: Session = Depends(get_db)):
    if db.query(Wallet).filter(Wallet.name == req.name).first():
        raise HTTPException(400, "Wallet name already exists")
    chain = "evm"
    from eth_account import Account
    try:
        acct = Account.from_key(req.private_key)
        address = acct.address
    except Exception:
        raise HTTPException(400, "Invalid EVM private key")

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
    wallets = db.query(Wallet).filter(Wallet.chain == "evm").all()
    return [{"id": w.id, "name": w.name, "chain": w.chain, "address": w.address} for w in wallets]

@router.patch("/{wallet_id}", dependencies=[Depends(require_session)])
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

@router.delete("/{wallet_id}", dependencies=[Depends(require_session)])
def delete_wallet(wallet_id: int, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    db.delete(w)
    db.commit()
    return {"deleted": wallet_id}

@router.post("/{wallet_id}/export", dependencies=[Depends(require_session)])
def export_wallet(wallet_id: int, req: ExportWalletRequest, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    if not req.passphrase or not req.passphrase.strip():
        raise HTTPException(400, "Passphrase cannot be blank")
    if not verify_passphrase(req.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    private_key = None
    try:
        private_key = decrypt_key(w.encrypted_key)
        # JSONResponse serializes immediately, allowing this function to drop
        # its plaintext-key reference before returning. Python strings cannot
        # be reliably zeroized, so the real guarantees are no persistence, no
        # logging, and the shortest practical lifetime.
        return JSONResponse(
            content={
                "id": w.id,
                "name": w.name,
                "chain": w.chain,
                "address": w.address,
                "private_key": private_key,
            },
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )
    except ValueError as e:
        raise HTTPException(500, str(e))
    finally:
        private_key = None

@router.get("/{wallet_id}/balance")
def wallet_balance(wallet_id: int, network: Optional[str] = None, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not w:
        raise HTTPException(404, "Wallet not found")
    try:
        return get_wallet_balance(w, network)
    except Exception as e:
        raise HTTPException(502, str(e))


# Note: there is deliberately no direct "send" endpoint here. Every send
# goes through the chat CONFIRM flow (app/routers/chat.py's _stream_send and
# friends) so a human always sees and explicitly confirms exactly what's
# about to move before it's signed. A prior direct-send endpoint bypassed
# that entirely — the per-launch session token (app/core/session_auth.py)
# proves a caller loaded this app's own served page, but it can't prove the
# caller IS that page rather than another local process that also just
# GETs it, so a same-machine attacker with no other foothold could reach it
# and move funds with no confirmation step at all. Removed rather than
# patched: it was unused by the frontend (confirmed — no fetch call
# anywhere in index.html ever hit /wallets/{id}/send), so there was no
# product behavior to preserve.
