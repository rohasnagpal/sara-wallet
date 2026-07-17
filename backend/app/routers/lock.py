from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.tools.wallet import lock as lock_state
from app.core.session_auth import require_session

router = APIRouter(prefix="/lock", tags=["lock"])


class PassphraseBody(BaseModel):
    passphrase: str


@router.get("/status")
def status():
    return {"configured": lock_state.is_configured(), "unlocked": lock_state.is_unlocked()}


@router.post("/setup", dependencies=[Depends(require_session)])
def setup(body: PassphraseBody):
    try:
        lock_state.setup_passphrase(body.passphrase)
    except (ValueError, lock_state.WalletLockedError) as e:
        raise HTTPException(400, str(e))
    return {"status": "unlocked"}


@router.post("/unlock", dependencies=[Depends(require_session)])
def unlock(body: PassphraseBody):
    try:
        ok = lock_state.unlock(body.passphrase)
    except lock_state.WalletThrottledError as e:
        raise HTTPException(429, str(e))
    except (ValueError, lock_state.WalletLockedError) as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(401, "Incorrect passphrase.")
    return {"status": "unlocked"}


@router.post("/lock", dependencies=[Depends(require_session)])
def do_lock():
    lock_state.lock()
    return {"status": "locked"}
