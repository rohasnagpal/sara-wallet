from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.tools.wallet import lock as lock_state

router = APIRouter(prefix="/lock", tags=["lock"])


class PassphraseBody(BaseModel):
    passphrase: str


@router.get("/status")
def status():
    return {"configured": lock_state.is_configured(), "unlocked": lock_state.is_unlocked()}


@router.post("/setup")
def setup(body: PassphraseBody):
    try:
        lock_state.setup_passphrase(body.passphrase)
    except (ValueError, lock_state.WalletLockedError) as e:
        raise HTTPException(400, str(e))
    return {"status": "unlocked"}


@router.post("/unlock")
def unlock(body: PassphraseBody):
    try:
        ok = lock_state.unlock(body.passphrase)
    except (ValueError, lock_state.WalletLockedError) as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(401, "Incorrect passphrase.")
    return {"status": "unlocked"}


@router.post("/lock")
def do_lock():
    lock_state.lock()
    return {"status": "locked"}
