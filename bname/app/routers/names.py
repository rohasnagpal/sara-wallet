from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import build_signing_message, create_nonce, verify_nonce_and_signature
from app.crud import get_active_bname, register_bname, upsert_record
from app.database import get_db
from app.models import BName, BNameRecord, BNameRecordVersion, RecordStatus
from app.schemas import NameResponse, NonceRequest, NonceResponse, RecordUpsertRequest, RegisterRequest
from app.validation import ValidationError, validate_evm_address, validate_root_name

router = APIRouter(prefix="/v1", tags=["names"])


@router.post("/nonce", response_model=NonceResponse)
def nonce(req: NonceRequest, db: Session = Depends(get_db)):
    try:
        item = create_nonce(db, req.owner_address, req.purpose)
        return NonceResponse(
            owner_address=item.owner_address,
            nonce=item.nonce,
            purpose=item.purpose,
            expires_at=item.expires_at,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/register", response_model=NameResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    try:
        root = validate_root_name(req.name)
        owner = validate_evm_address(req.owner_address)
        bname = register_bname(db, root, owner)
        return bname
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/names/{name}", response_model=NameResponse)
def name_info(name: str, db: Session = Depends(get_db)):
    try:
        bname = get_active_bname(db, name)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        return bname
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/addresses/{address}/names", response_model=list[NameResponse])
def names_for_address(address: str, db: Session = Depends(get_db)):
    try:
        owner = validate_evm_address(address)
        return db.query(BName).filter(BName.owner_address == owner).order_by(BName.name.asc()).all()
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/names/{name}/records")
def create_or_update_record(name: str, req: RecordUpsertRequest, db: Session = Depends(get_db)):
    try:
        bname = get_active_bname(db, name)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        fields = {
            "name": bname.name,
            "subname": req.subname or "@",
            "record_key": req.record_key,
            "record_type": req.record_type,
            "record_value": req.record_value,
            "ttl": str(req.ttl),
            "nonce": req.nonce,
        }
        message = build_signing_message("update_record", fields)
        signer = verify_nonce_and_signature(
            db=db,
            expected_owner=bname.owner_address,
            purpose="update_record",
            nonce_value=req.nonce,
            message=message,
            signature=req.signature,
        )
        record = upsert_record(
            db=db,
            bname=bname,
            subname=req.subname or "@",
            record_type=req.record_type,
            record_key=req.record_key,
            record_value=req.record_value,
            ttl=req.ttl,
            signature=req.signature,
            signer_address=signer,
        )
        return {"status": "ok", "record_id": record.id, "version": record.current_version}
    except ValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/names/{name}/history")
def history(name: str, db: Session = Depends(get_db)):
    try:
        bname = get_active_bname(db, name)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        rows = (
            db.query(BNameRecordVersion)
            .filter(BNameRecordVersion.bname_id == bname.id)
            .order_by(BNameRecordVersion.created_at.desc())
            .all()
        )
        return [
            {
                "subname": row.subname,
                "record_type": row.record_type.value,
                "record_key": row.record_key,
                "record_value": row.record_value,
                "ttl": row.ttl,
                "version": row.version,
                "record_hash": row.record_hash,
                "created_at": row.created_at,
                "anchor_type": row.anchor_type.value,
                "anchor_tx_hash": row.anchor_tx_hash,
            }
            for row in rows
        ]
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/names/{name}/records/{record_id}")
def delete_record(name: str, record_id: str, db: Session = Depends(get_db)):
    try:
        bname = get_active_bname(db, name)
        if not bname:
            raise HTTPException(status_code=404, detail="bName not found")
        record = (
            db.query(BNameRecord)
            .filter(BNameRecord.id == record_id, BNameRecord.bname_id == bname.id)
            .one_or_none()
        )
        if not record:
            raise HTTPException(status_code=404, detail="record not found")
        record.status = RecordStatus.deleted
        db.add(record)
        db.commit()
        return {"status": "deleted"}
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
