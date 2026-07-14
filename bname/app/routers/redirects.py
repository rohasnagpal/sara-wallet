from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BNameRecord, RecordStatus, RecordType
from app.validation import ValidationError, split_query_name
from app.crud import get_active_bname

router = APIRouter(tags=["redirects"])


@router.get("/r/{name:path}")
def redirect_name(name: str, db: Session = Depends(get_db)):
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
                BNameRecord.record_type.in_([RecordType.url, RecordType.social]),
            )
            .first()
        )
        if not record:
            raise HTTPException(status_code=404, detail="redirect record not found")
        return RedirectResponse(record.record_value, status_code=302)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
