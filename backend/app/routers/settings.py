from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Config
from app.tools.wallet.encrypt import save_master_key
import os

router = APIRouter(prefix="/settings", tags=["settings"])

ALLOWED_KEYS = {
    "LLM_PROVIDER":       "Active AI Provider",
    "LLM_MODEL":          "Model Name",
    "GROQ_API_KEY":       "Groq API Key",
    "OPENAI_API_KEY":     "OpenAI API Key (ChatGPT)",
    "ANTHROPIC_API_KEY":  "Anthropic API Key (Claude)",
    "XAI_API_KEY":        "xAI API Key (Grok)",
    "GOOGLE_API_KEY":     "Google API Key (Gemini)",
    "COINGECKO_API_KEY":   "CoinGecko API Key (optional, higher rate limits)",
    "CRYPTOPANIC_API_KEY": "CryptoPanic API Key (news & sentiment)",
    "ALCHEMY_API_KEY":    "Alchemy API Key (token balances + faster RPCs)",
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
        result[key] = {"label": label, "masked": _mask(val), "set": bool(val), "raw": val if key in ("LLM_PROVIDER", "LLM_MODEL") else ""}
    return result


class SettingBody(BaseModel):
    key: str
    value: str


@router.post("")
def save_setting(body: SettingBody, db: Session = Depends(get_db)):
    if body.key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key: {body.key}")
    value = body.value
    if body.key == "SARA_MASTER_KEY":
        try:
            value = save_master_key(body.value)
        except ValueError as e:
            raise HTTPException(400, str(e))
    row = db.query(Config).filter(Config.key == body.key).first()
    if row:
        row.value = value
    else:
        db.add(Config(key=body.key, value=value))
    db.commit()
    os.environ[body.key] = value
    return {"status": "saved", "key": body.key}
