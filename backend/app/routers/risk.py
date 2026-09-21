from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.session_auth import require_session
from app.db.models import RiskScreening
from app.db.session import get_db
from app.tools.risk import screening

router = APIRouter(prefix="/risk", tags=["risk"], dependencies=[Depends(require_session)])


def _screening_row(row: RiskScreening) -> dict:
    import json
    return {
        "id": row.id, "address": row.address, "network": row.network, "provider": row.provider,
        "result": row.result, "evidence": json.loads(row.evidence or "[]"), "reason": row.reason,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


class ScreenBody(BaseModel):
    address: str
    network: str = "polygon"


@router.post("/screen")
def screen(body: ScreenBody, db: Session = Depends(get_db)):
    from web3 import Web3
    if not Web3.is_address(body.address.strip()):
        raise HTTPException(400, "That doesn't look like a valid address. It should start with 0x and be 42 characters long.")
    result = screening.screen_address(db, body.address.strip(), body.network)
    return {
        "address": result.address, "network": result.network, "provider": result.provider,
        "result": result.result, "evidence": result.evidence, "reason": result.reason,
        "checked_at": result.checked_at.isoformat() if result.checked_at else None,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "mandatory": settings.RISK_SCREENING_MANDATORY,
    }


@router.get("/screenings")
def list_screenings(address: str | None = None, db: Session = Depends(get_db)):
    query = db.query(RiskScreening)
    if address:
        query = query.filter(RiskScreening.address == address.lower())
    rows = query.order_by(RiskScreening.checked_at.desc()).limit(200).all()
    return {"screenings": [_screening_row(r) for r in rows]}
