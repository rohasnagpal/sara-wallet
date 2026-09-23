from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.session import SessionLocal
from app.db.models import Wallet, WalletSeed
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
    chain: str = "evm"
    # Which seed to derive this wallet from - omit to use the default (the
    # first one ever created, auto-creating it if none exists yet).
    # "Add another seed" (POST /wallets/seeds) is the advanced path for
    # anyone who wants a second, separate one instead.
    seed_id: Optional[int] = None

class ImportWalletRequest(BaseModel):
    name: str
    chain: str
    private_key: str  # hex string

class RenameWalletRequest(BaseModel):
    name: str

class ExportWalletRequest(BaseModel):
    passphrase: str

class AddSeedRequest(BaseModel):
    label: str = "Default"
    # Omit to generate a brand-new phrase; provide an existing one (e.g.
    # recovering onto a fresh Sara install, or bringing in a phrase you
    # already use elsewhere) to import it instead.
    seed_phrase: Optional[str] = None

class RevealSeedRequest(BaseModel):
    passphrase: str


def _default_seed(db: Session) -> tuple[WalletSeed, Optional[str]]:
    """The first seed ever created - auto-created here the first time it's
    needed, so a brand-new Sara install needs no separate onboarding step.
    Returns (seed, phrase) where phrase is only non-None on the one call
    that actually created it - every wallet derived from it after that
    shares the one phrase shown at that moment; no phrase is ever re-shown
    by this path."""
    seed = db.query(WalletSeed).order_by(WalletSeed.id).first()
    if seed is not None:
        return seed, None
    phrase = generate_seed_phrase()
    seed = WalletSeed(label="Default", encrypted_seed=encrypt_key(phrase), next_index=0)
    db.add(seed)
    db.flush()  # assigns seed.id without ending the caller's transaction
    return seed, phrase


@router.post("/create", dependencies=[Depends(require_session)])
def create_wallet(req: CreateWalletRequest, db: Session = Depends(get_db)):
    if db.query(Wallet).filter(Wallet.name == req.name).first():
        raise HTTPException(400, "Wallet name already exists")
    chain = req.chain.lower()
    if chain != "evm":
        raise HTTPException(400, "Sara now supports EVM wallets only")

    try:
        if req.seed_id is not None:
            seed = db.query(WalletSeed).filter(WalletSeed.id == req.seed_id).first()
            if seed is None:
                raise HTTPException(404, "Seed not found")
            shown_phrase = None
        else:
            seed, shown_phrase = _default_seed(db)

        seed_phrase = decrypt_key(seed.encrypted_seed)
        index = seed.next_index
        derived = derive_wallet(seed_phrase, index)
        encrypted = encrypt_key(derived["private_key"])
        seed.next_index = index + 1
    except WalletLockedError as e:
        raise HTTPException(423, str(e))

    wallet = Wallet(
        name=req.name, chain=chain, address=derived["address"], encrypted_key=encrypted,
        seed_id=seed.id, derivation_index=index,
    )
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    result = {
        "id": wallet.id, "name": wallet.name, "chain": wallet.chain, "address": wallet.address,
        "seed_id": seed.id, "derivation_index": index,
    }
    if shown_phrase:
        # Shown exactly once, at the moment the very first seed is created -
        # every wallet after this (from this seed or a deliberately-added
        # other one) never returns a phrase from this endpoint again; use
        # POST /wallets/seeds/{id}/reveal (passphrase-gated) to see it again.
        result["seed_phrase"] = shown_phrase
        result["seed_label"] = seed.label
    return result


@router.get("/seeds", dependencies=[Depends(require_session)])
def list_seeds(db: Session = Depends(get_db)):
    """Labels and wallet counts only - never the phrase itself. Use
    POST /wallets/seeds/{id}/reveal (passphrase-gated) to see the words."""
    from collections import Counter
    seeds = db.query(WalletSeed).order_by(WalletSeed.id).all()
    seed_ids_in_use = [w.seed_id for w in db.query(Wallet).filter(Wallet.seed_id.isnot(None)).all()]
    counts = Counter(seed_ids_in_use)
    return [
        {"id": s.id, "label": s.label, "created_at": s.created_at.isoformat() if s.created_at else None,
         "wallet_count": counts.get(s.id, 0), "next_index": s.next_index}
        for s in seeds
    ]


@router.post("/seeds", dependencies=[Depends(require_session)])
def add_seed(req: AddSeedRequest, db: Session = Depends(get_db)):
    """"Add another seed" - the advanced option. With no seed_phrase, mints
    a brand-new one (returned exactly once, same as the first wallet's
    auto-created default). With one, imports it - e.g. recovering onto a
    fresh Sara install."""
    label = (req.label or "").strip() or "Default"
    if req.seed_phrase:
        try:
            phrase = validate_seed_phrase(req.seed_phrase)
        except SeedError as e:
            raise HTTPException(400, str(e))
        existing = db.query(WalletSeed).all()
        try:
            for row in existing:
                if decrypt_key(row.encrypted_seed) == phrase:
                    raise HTTPException(400, f'This phrase is already added, as "{row.label}".')
        except WalletLockedError as e:
            raise HTTPException(423, str(e))
        is_new = False
    else:
        phrase = generate_seed_phrase()
        is_new = True

    try:
        seed = WalletSeed(label=label, encrypted_seed=encrypt_key(phrase), next_index=0)
    except WalletLockedError as e:
        raise HTTPException(423, str(e))
    db.add(seed)
    db.commit()
    db.refresh(seed)
    result = {"id": seed.id, "label": seed.label}
    if is_new:
        result["seed_phrase"] = phrase  # shown exactly once, same rule as the default seed
    return result


@router.post("/seeds/{seed_id}/reveal", dependencies=[Depends(require_session)])
def reveal_seed(seed_id: int, req: RevealSeedRequest, db: Session = Depends(get_db)):
    seed = db.query(WalletSeed).filter(WalletSeed.id == seed_id).first()
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
            content={"id": seed.id, "label": seed.label, "seed_phrase": phrase},
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
    chain = req.chain.lower()
    if chain == "evm":
        from eth_account import Account
        try:
            acct = Account.from_key(req.private_key)
            address = acct.address
        except Exception:
            raise HTTPException(400, "Invalid EVM private key")
    else:
        raise HTTPException(400, "Sara now supports EVM wallets only")

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
