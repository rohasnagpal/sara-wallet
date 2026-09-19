from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Config
from app.core.session_auth import require_session
import os
import time
import requests

router = APIRouter(prefix="/settings", tags=["settings"])

ALLOWED_KEYS = {
    "LLM_PROVIDER":       "Active AI Provider",
    "LLM_MODEL":          "Model Name",
    "OPENROUTER_API_KEY": "OpenRouter API Key",
    "COINGECKO_API_KEY":   "CoinGecko API Key (optional, higher rate limits)",
    "ALCHEMY_API_KEY":    "Alchemy API Key (token balances + faster RPCs)",
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


class AssetSettingsBody(BaseModel):
    enabled_networks: list[str]
    usdc_networks: list[str]


@router.post("", dependencies=[Depends(require_session)])
def save_setting(body: SettingBody, db: Session = Depends(get_db)):
    if body.key not in ALLOWED_KEYS:
        raise HTTPException(400, f"Unknown key: {body.key}")
    value = body.value
    row = db.query(Config).filter(Config.key == body.key).first()
    if row:
        row.value = value
    else:
        db.add(Config(key=body.key, value=value))
    db.commit()
    os.environ[body.key] = value
    return {"status": "saved", "key": body.key}


@router.get("/assets")
def get_asset_settings():
    from app.core.assets import serialize_preferences
    return serialize_preferences()


@router.post("/assets", dependencies=[Depends(require_session)])
def save_asset_settings(body: AssetSettingsBody, db: Session = Depends(get_db)):
    from app.core.assets import ALL_NETWORKS, serialize_preferences
    allowed = set(ALL_NETWORKS)
    enabled = set(body.enabled_networks)
    usdc = set(body.usdc_networks)
    if not enabled:
        raise HTTPException(400, "Keep at least one network enabled")
    unknown = (enabled | usdc) - allowed
    if unknown:
        raise HTTPException(400, f"Unsupported network: {sorted(unknown)[0]}")
    if not usdc.issubset(enabled):
        raise HTTPException(400, "USDC can only be enabled on an enabled network")

    values = {
        "SARA_ENABLED_NETWORKS": ",".join(n for n in ALL_NETWORKS if n in enabled),
        "SARA_USDC_NETWORKS": ",".join(n for n in ALL_NETWORKS if n in usdc),
    }
    for key, value in values.items():
        row = db.query(Config).filter(Config.key == key).first()
        if row:
            row.value = value
        else:
            db.add(Config(key=key, value=value))
        os.environ[key] = value
    db.commit()
    return serialize_preferences()


_models_cache: dict = {"data": None, "fetched_at": 0}
_MODELS_CACHE_TTL = 3600


@router.get("/openrouter-models")
def get_openrouter_models():
    now = time.time()
    if _models_cache["data"] is not None and now - _models_cache["fetched_at"] < _MODELS_CACHE_TTL:
        return _models_cache["data"]
    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", timeout=10)
        resp.raise_for_status()
        models = [
            {"id": m["id"], "name": m.get("name", m["id"])}
            for m in resp.json().get("data", [])
        ]
        models.sort(key=lambda m: m["name"].lower())
    except Exception as e:
        raise HTTPException(502, f"Could not fetch OpenRouter models: {e}")
    _models_cache["data"] = models
    _models_cache["fetched_at"] = now
    return models
