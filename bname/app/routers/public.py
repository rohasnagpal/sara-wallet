from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.crud import get_active_bname, latest_record_hash, latest_zone
from app.database import get_db
from app.models import BNameRecord, RecordStatus
from app.schemas import RecordResponse, ZoneRecord, ZoneResponse
from app.validation import ValidationError, split_query_name

router = APIRouter(prefix="/v1", tags=["public"])


def _record_response(db: Session, bname, record: BNameRecord) -> RecordResponse:
    zone = latest_zone(db, bname)
    return RecordResponse(
        name=bname.name if record.subname == "@" else f"{record.subname}.{bname.name}",
        root=bname.name,
        subname=record.subname,
        record_type=record.record_type.value,
        record_key=record.record_key,
        record_value=record.record_value,
        version=record.current_version,
        ttl=record.ttl,
        record_hash=latest_record_hash(db, record),
        zone_version=zone.zone_version,
        zone_hash=zone.zone_hash,
        anchor={"type": zone.anchor_type.value, "tx_hash": zone.anchor_tx_hash},
    )


@router.get("/resolve/{name:path}", response_model=RecordResponse)
def resolve(name: str, response: Response, db: Session = Depends(get_db)):
    try:
        root, subname = split_query_name(name)
        bname = get_active_bname(db, root)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        record = (
            db.query(BNameRecord)
            .filter(
                BNameRecord.bname_id == bname.id,
                BNameRecord.subname == subname,
                BNameRecord.status == RecordStatus.active,
            )
            .order_by(BNameRecord.record_type.asc(), BNameRecord.record_key.asc())
            .first()
        )
        if not record:
            raise HTTPException(status_code=404, detail="record not found")
        payload = _record_response(db, bname, record)
        response.headers["Cache-Control"] = f"public, max-age={payload.ttl}"
        response.headers["ETag"] = payload.record_hash
        response.headers["X-BName-Version"] = str(payload.version)
        response.headers["X-BName-Zone-Version"] = str(payload.zone_version)
        response.headers["X-BName-Source"] = "authoritative"
        response.headers["X-BName-Anchor"] = payload.anchor["type"]
        return payload
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/zones/{name}", response_model=ZoneResponse)
def zone(name: str, response: Response, db: Session = Depends(get_db)):
    try:
        bname = get_active_bname(db, name)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        zone_version = latest_zone(db, bname)
        active_records = [record for record in bname.records if record.status == RecordStatus.active]
        ttl = min([record.ttl for record in active_records], default=300)
        response.headers["Cache-Control"] = f"public, max-age={ttl}"
        response.headers["ETag"] = zone_version.zone_hash
        response.headers["X-BName-Zone-Version"] = str(zone_version.zone_version)
        return ZoneResponse(
            zone=bname.name,
            owner_address=bname.owner_address,
            zone_version=zone_version.zone_version,
            zone_hash=zone_version.zone_hash,
            records=[
                ZoneRecord(
                    subname=record.subname,
                    record_type=record.record_type.value,
                    record_key=record.record_key,
                    record_value=record.record_value,
                    ttl=record.ttl,
                    version=record.current_version,
                )
                for record in sorted(active_records, key=lambda r: (r.subname, r.record_type.value, r.record_key))
            ],
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
