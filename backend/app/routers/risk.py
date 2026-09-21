from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.config import settings
from app.core.session_auth import require_session
from app.db.models import RiskReview, RiskScreening
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


class ReviewBody(BaseModel):
    address: str
    network: str = "polygon"
    decision: str
    reason: str = Field(..., max_length=1000)


@router.post("/reviews")
def create_review(body: ReviewBody, db: Session = Depends(get_db)):
    """An audited manual override — recorded, never an invisible bypass."""
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be 'approved' or 'rejected'")
    row = RiskReview(
        address=body.address.lower(), network=body.network.lower(), decision=body.decision,
        actor="local-owner", reason=body.reason,
    )
    db.add(row)
    db.flush()
    append_audit(db, "risk.manual_review", "risk_review", resource_id=str(row.id),
                 details={"address": row.address, "network": row.network, "decision": row.decision}, actor_id=row.actor)
    db.commit()
    return {"id": row.id}


@router.get("/reviews")
def list_reviews(address: str | None = None, db: Session = Depends(get_db)):
    query = db.query(RiskReview)
    if address:
        query = query.filter(RiskReview.address == address.lower())
    rows = query.order_by(RiskReview.created_at.desc()).all()
    return {"reviews": [{
        "id": r.id, "address": r.address, "network": r.network, "decision": r.decision,
        "actor": r.actor, "reason": r.reason, "created_at": r.created_at.isoformat(),
    } for r in rows]}
