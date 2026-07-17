import io
import csv
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Wallet, PaymentRequest
from app.tools.payments.links import decode_payload, create_payment_request
from app.tools.payments.reconcile import check_payment_request
from app.core.session_auth import require_session

router = APIRouter(prefix="/payments", tags=["payments"])


class CreateLinkRequest(BaseModel):
    wallet_name: str
    amount: float
    token: str
    network: Optional[str] = None
    note: Optional[str] = None


class UpdateRequestStatus(BaseModel):
    status: str  # "pending" | "paid" | "cancelled"


def _default_network(wallet: Wallet) -> str:
    if wallet.chain == "solana":
        return "solana"
    if wallet.chain == "tron":
        return "tron"
    return "ethereum"


@router.post("/link", dependencies=[Depends(require_session)])
def create_link(req: CreateLinkRequest, db: Session = Depends(get_db)):
    w = db.query(Wallet).filter(Wallet.name.ilike(req.wallet_name)).first()
    if not w:
        raise HTTPException(404, f"Wallet '{req.wallet_name}' not found")
    network = (req.network or _default_network(w)).lower()
    row, result = create_payment_request(db, w, network, req.token, req.amount, req.note or "")
    if row is None:
        raise HTTPException(400, result)
    return {
        "payload": result, "reference": row.reference, "to": w.address, "wallet_name": w.name,
        "chain": w.chain, "network": network, "token": row.token,
        "amount": row.amount, "note": row.note,
    }


@router.get("/parse")
def parse_link(payload: str = Query(..., max_length=2000)):
    try:
        data = decode_payload(payload)
    except Exception:
        raise HTTPException(400, "Could not read this payment link — it may be corrupted or incomplete.")
    required = ("to", "chain", "network", "token", "amount")
    if not all(k in data for k in required):
        raise HTTPException(400, "This payment link is missing required fields.")
    return data


@router.get("/qr")
def payment_qr(data: str = Query(..., max_length=2000)):
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/requests")
def list_requests(check: bool = Query(default=True), db: Session = Depends(get_db)):
    rows = db.query(PaymentRequest).order_by(PaymentRequest.created_at.desc()).all()
    if check:
        for r in rows:
            if r.status == "pending":
                check_payment_request(db, r)
    wallets = {w.id: w.name for w in db.query(Wallet).all()}
    return [{
        "id": r.id, "reference": r.reference, "wallet_name": wallets.get(r.wallet_id, "?"),
        "chain": r.chain, "network": r.network, "token": r.token, "amount": r.amount,
        "note": r.note, "status": r.status, "matched_tx_hash": r.matched_tx_hash,
        "created_at": r.created_at.isoformat(),
    } for r in rows]


@router.post("/requests/{request_id}/check", dependencies=[Depends(require_session)])
def check_request(request_id: int, db: Session = Depends(get_db)):
    row = db.query(PaymentRequest).filter(PaymentRequest.id == request_id).first()
    if not row:
        raise HTTPException(404, "Payment request not found")
    matched = check_payment_request(db, row)
    return {"id": row.id, "status": row.status, "matched": matched, "matched_tx_hash": row.matched_tx_hash}


@router.patch("/requests/{request_id}", dependencies=[Depends(require_session)])
def update_request(request_id: int, req: UpdateRequestStatus, db: Session = Depends(get_db)):
    row = db.query(PaymentRequest).filter(PaymentRequest.id == request_id).first()
    if not row:
        raise HTTPException(404, "Payment request not found")
    if req.status not in ("pending", "paid", "cancelled"):
        raise HTTPException(400, "status must be pending, paid, or cancelled")
    row.status = req.status
    db.commit()
    return {"id": row.id, "status": row.status}


@router.delete("/requests/{request_id}", dependencies=[Depends(require_session)])
def delete_request(request_id: int, db: Session = Depends(get_db)):
    row = db.query(PaymentRequest).filter(PaymentRequest.id == request_id).first()
    if not row:
        raise HTTPException(404, "Payment request not found")
    db.delete(row)
    db.commit()
    return {"deleted": request_id}


def _csv_safe(value):
    """Neutralize CSV formula injection: a cell starting with =, +, -, @, or a
    tab/CR makes Excel/Sheets treat it as a formula rather than text when the
    file is opened — a wallet name or note is user-chosen text, but this
    export is meant to be shared/forwarded (an accountant, a business
    partner), so it has to be safe in whoever else's spreadsheet app opens
    it, not just the original user's."""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@router.get("/requests/export")
def export_requests(db: Session = Depends(get_db)):
    rows = db.query(PaymentRequest).order_by(PaymentRequest.created_at.desc()).all()
    wallets = {w.id: w.name for w in db.query(Wallet).all()}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["reference", "created_at", "wallet", "chain", "network", "token", "amount", "note", "status", "matched_tx_hash"])
    for r in rows:
        writer.writerow([_csv_safe(v) for v in [
            r.reference, r.created_at.isoformat(), wallets.get(r.wallet_id, "?"),
            r.chain, r.network, r.token, r.amount, r.note, r.status, r.matched_tx_hash or "",
        ]])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sara-payment-requests.csv"},
    )
