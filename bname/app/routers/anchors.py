from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.anchor import queue_anchor_job
from app.crud import get_active_bname, latest_zone, mark_zone_anchored
from app.database import get_db
from app.schemas import AnchorRequest
from app.validation import ValidationError

router = APIRouter(prefix="/v1", tags=["anchors"])


@router.post("/names/{name}/anchor")
def anchor(name: str, req: AnchorRequest, db: Session = Depends(get_db)):
    try:
        bname = get_active_bname(db, name)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        zone = latest_zone(db, bname)
        tx_marker = queue_anchor_job(bname, req.anchor_type, zone.zone_hash)
        updated = mark_zone_anchored(db, bname, req.anchor_type, tx_marker)
        return {
            "status": "queued",
            "name": bname.name,
            "zone_version": updated.zone_version,
            "zone_hash": updated.zone_hash,
            "anchor_type": updated.anchor_type.value,
            "anchor_tx_hash": updated.anchor_tx_hash,
        }
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
