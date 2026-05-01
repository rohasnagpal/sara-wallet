from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Config
import os

router = APIRouter(prefix="/settings", tags=["settings"])

ALLOWED_KEYS = {
    "GROQ_API_KEY":       "Groq API Key (LLM — required)",
    "COINGECKO_API_KEY":  "CoinGecko API Key (optional, higher rate limits)",
    "ALCHEMY_API_KEY":    "Alchemy API Key (optional, faster EVM RPCs)",
    "HELIUS_RPC":         "Helius RPC URL (optional, Solana)",
    "SARA_MASTER_KEY":    "Encryption Key (protects stored private keys)",
}


def _mask(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 8:
        return "•" * len(val)
    return val[:4] + "•" * (len(val) - 8) + val[-4:]


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    result = {}
    for key, label in ALLOWED_KEYS.items():
        row = db.query(Config).filter(Config.key == key).first()
        val = row.value if row else os.getenv(key, "")
        result[key] = {"label": label, "masked": _mask(val), "set": bool(val)}
    return result


class SettingBody(BaseModel):
    key: str
    value: str


@router.post("")
def save_setting(body: SettingBody, db: Session = Depends(get_db)):
    if body.key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key: {body.key}")
    row = db.query(Config).filter(Config.key == body.key).first()
    if row:
        row.value = body.value
    else:
        db.add(Config(key=body.key, value=body.value))
    db.commit()
    os.environ[body.key] = body.value
    return {"status": "saved", "key": body.key}
