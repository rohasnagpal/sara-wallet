import io
import csv
import hashlib
import html
import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Query, Depends, Header
from fastapi.responses import Response, StreamingResponse, HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.db.models import Wallet, PaymentRequest, MerchantClient, AlertDestination, Transaction
from app.tools.payments.links import decode_payload, create_payment_request, encode_payload, parse_eip681
from app.tools.payments.reconcile import check_payment_request
from app.core.session_auth import require_session
from app.core.assets import NETWORKS, token_enabled


def _eip681_uri(token: str, network: str, payment_address: str | None, amount_raw) -> str | None:
    """A wallet-standard (EIP-681) payment URI so scanning the invoice's QR
    with MetaMask or any other wallet app pre-fills the transfer, instead of
    a Sara-only deep link only Sara's own frontend can act on."""
    net = NETWORKS.get(network)
    if not net or not payment_address or amount_raw is None:
        return None
    chain_id = net["chain_id"]
    symbol = token.upper()
    if symbol == net["native"]:
        return f"ethereum:{payment_address}@{chain_id}?value={amount_raw}"
    if symbol == "USDC" and net.get("usdc"):
        return f"ethereum:{net['usdc']}@{chain_id}/transfer?address={payment_address}&uint256={amount_raw}"
    return None

router = APIRouter(prefix="/payments", tags=["payments"])


class CreateInvoiceRequest(BaseModel):
    wallet_id: int | None = None
    customer_name: str = Field("", max_length=120)
    customer_email: str = Field("", max_length=254)
    amount: Decimal
    token: str = "USDC"
    network: str = "polygon"
    due_date: datetime | None = None
    description: str = Field("", max_length=1000)
    note: str = Field("", max_length=200)


def _invoice_dict(row: PaymentRequest, wallet_name: str | None = None, *, public: bool = False, db: Session | None = None) -> dict:
    if row.status == "pending" and row.due_date and row.due_date < datetime.utcnow():
        row.status = "overdue"
    data = {
        "id": row.id, "reference": row.reference, "wallet_name": wallet_name,
        "customer_name": row.customer_name, "amount": str(Decimal(row.amount_raw) / (Decimal(10) ** row.decimals)) if row.amount_raw and row.decimals is not None else str(row.amount),
        "amount_raw": row.amount_raw, "decimals": row.decimals, "token": row.token,
        "chain": row.chain, "network": row.network, "payment_address": row.payment_address,
        "description": row.description, "note": row.note, "due_date": row.due_date.isoformat() if row.due_date else None,
        "status": row.status, "matched_tx_hash": row.matched_tx_hash,
        "created_at": row.created_at.isoformat(),
    }
    if not public: data["customer_email"] = row.customer_email
    if db is not None:
        # Reverse lookup: "Pay to rohas" alongside the raw address, when the
        # receiving wallet owns a live Sara Name (Stage 7.5's product
        # completion — a purely local-index read, no on-chain round trip).
        from app.db.models import SaraName
        name_row = (
            db.query(SaraName)
            .filter(SaraName.wallet_id == row.wallet_id, SaraName.status.in_(("registered", "renewed")))
            .first()
        )
        data["sara_name"] = name_row.label if name_row else None
    return data


def _create_invoice(db: Session, wallet: Wallet, body: CreateInvoiceRequest, merchant_id: int | None = None):
    # USDC-only is deliberate, not a current-scope gap: an invoice fixes an
    # amount at creation time, and reconciliation (reconcile.py) matches it
    # exactly with no price tolerance — a stablecoin is what makes that a
    # fixed receivable rather than something exposed to price movement
    # between invoice creation and payment. The network choice itself isn't
    # similarly constrained: reconciliation already supports all of
    # Ethereum/Arbitrum/Base/Optimism/Polygon via the same Alchemy lookup
    # (see ALCHEMY_NETWORK_SLUGS in app/chains/evm.py), so any network the
    # user has USDC enabled for (Settings -> Manage Networks & Tokens) works.
    if body.token.upper() != "USDC" or not token_enabled("USDC", body.network):
        raise HTTPException(
            400,
            "Invoices currently support USDC only, on a network enabled for USDC in Settings.",
        )
    due_date = body.due_date
    if due_date and due_date.tzinfo:
        due_date = due_date.astimezone(timezone.utc).replace(tzinfo=None)
    if body.customer_email and ("@" not in body.customer_email or body.customer_email.startswith("@")):
        raise HTTPException(400, "Invalid customer email")
    row, payload = create_payment_request(
        db, wallet, body.network, body.token, body.amount, body.note,
        customer_name=body.customer_name, customer_email=body.customer_email,
        description=body.description, due_date=due_date, merchant_client_id=merchant_id,
    )
    if row is None: raise HTTPException(400, payload)
    return row, payload


@router.post("/invoices", dependencies=[Depends(require_session)])
def create_invoice(body: CreateInvoiceRequest, db: Session = Depends(get_db)):
    if body.wallet_id is None: raise HTTPException(400, "wallet_id is required")
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet: raise HTTPException(404, "EVM wallet not found")
    row, payload = _create_invoice(db, wallet, body)
    data = _invoice_dict(row, wallet.name)
    data.update({"payload": payload, "payment_page": f"/api/payments/page/{row.reference}"})
    return data


@router.get("/invoices", dependencies=[Depends(require_session)])
def list_invoices(check: bool = True, db: Session = Depends(get_db)):
    rows = db.query(PaymentRequest).order_by(PaymentRequest.created_at.desc()).all()
    wallets = {w.id: w.name for w in db.query(Wallet).all()}
    for row in rows:
        if check and row.status in ("pending", "overdue"): check_payment_request(db, row)
    result = [_invoice_dict(row, wallets.get(row.wallet_id)) for row in rows]
    db.commit()
    return result


@router.get("/public/{reference}")
def public_invoice(reference: str, db: Session = Depends(get_db)):
    row = db.query(PaymentRequest).filter(PaymentRequest.reference == reference).first()
    if not row: raise HTTPException(404, "Invoice not found")
    if row.status in ("pending", "overdue"): check_payment_request(db, row)
    data = _invoice_dict(row, public=True, db=db)
    db.commit()
    payload = encode_payload({"v":1,"ref":row.reference,"to":row.payment_address,"chain":row.chain,
                              "network":row.network,"token":row.token,"amount":data["amount"],"note":row.note})
    payment_uri = _eip681_uri(row.token, row.network, row.payment_address, row.amount_raw)
    return {**data, "payload": payload, "payment_uri": payment_uri}


@router.get("/page/{reference}", response_class=HTMLResponse)
def payment_page(reference: str, db: Session = Depends(get_db)):
    data = public_invoice(reference, db)
    payload = data["payload"]
    # A standard wallet-payment URI (EIP-681) when we have one, so MetaMask
    # and other wallet apps' QR scanners can pre-fill the transfer directly;
    # falls back to Sara's own deep link (only Sara itself can open that).
    qr_url = "/api/payments/qr?data=" + quote(data.get("payment_uri") or ("/?pay=" + payload), safe="")
    status = html.escape(data["status"])
    return HTMLResponse(f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Invoice {html.escape(reference)}</title><style>body{{font-family:system-ui;background:#f5f3ee;color:#1d1b17;display:grid;place-items:center;min-height:100vh}}main{{background:white;padding:28px;border-radius:16px;max-width:420px;text-align:center;box-shadow:0 8px 30px #0001}}img{{width:220px}}code{{word-break:break-all}}a{{display:inline-block;padding:11px 18px;background:#254f3d;color:white;border-radius:8px;text-decoration:none}}</style></head><body><main><h1>{html.escape(reference)}</h1><p>{html.escape(data.get('customer_name') or '')}</p><h2>{html.escape(data['amount'])} {html.escape(data['token'])}</h2><p>{html.escape(data.get('description') or '')}</p><img src='{qr_url}' alt='Payment QR'><p><code>{html.escape(data['payment_address'] or '')}</code></p><p>Status: <strong>{status}</strong></p>{'' if status in ('paid','cancelled') else f"<a href='/?pay={payload}'>Pay with Sara</a>"}</main></body></html>""", headers={"Cache-Control":"no-store"})


@router.get("/invoices/{reference}/receipt", dependencies=[Depends(require_session)])
def invoice_receipt(reference: str, db: Session = Depends(get_db)):
    row = db.query(PaymentRequest).filter(PaymentRequest.reference == reference).first()
    if not row: raise HTTPException(404, "Invoice not found")
    if row.status != "paid" or not row.matched_tx_hash: raise HTTPException(409, "Invoice is not paid")
    tx = db.query(Transaction).filter(Transaction.network == row.network, Transaction.tx_hash == row.matched_tx_hash).first()
    return {"receipt_type":"invoice_payment","reference":row.reference,"status":"paid",
            "amount":_invoice_dict(row, public=True)["amount"],"amount_raw":row.amount_raw,"decimals":row.decimals,
            "token":row.token,"network":row.network,"from":tx.from_address if tx else None,
            "to":row.payment_address,"timestamp":tx.timestamp.isoformat() if tx and tx.timestamp else None,
            "transaction_hash":row.matched_tx_hash,"customer_name":row.customer_name}


class MerchantClientBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    wallet_id: int
    webhook_url: str | None = None


@router.post("/merchant/clients", dependencies=[Depends(require_session)])
def create_merchant_client(body: MerchantClientBody, db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet: raise HTTPException(404, "EVM wallet not found")
    if db.query(MerchantClient).filter(MerchantClient.name == body.name).first(): raise HTTPException(409, "Merchant client name exists")
    key = "sara_live_" + secrets.token_urlsafe(32)
    client = MerchantClient(name=body.name, wallet_id=wallet.id, api_key_prefix=key[:16], api_key_hash=hashlib.sha256(key.encode()).hexdigest())
    db.add(client); db.flush()
    webhook_secret = None
    if body.webhook_url:
        from app.services.alerts import validate_webhook_url
        try: validate_webhook_url(body.webhook_url)
        except ValueError as exc: raise HTTPException(400, str(exc))
        webhook_secret = secrets.token_urlsafe(32)
        destination = AlertDestination(kind="webhook", target=body.webhook_url,
            secret=json.dumps({"signing_secret":webhook_secret,"event_types":["payment_request.paid"],"merchant_client_id":client.id}))
        db.add(destination); db.flush(); client.alert_destination_id = destination.id
    db.commit()
    return {"id":client.id,"name":client.name,"api_key":key,"api_key_prefix":client.api_key_prefix,
            "webhook_signing_secret":webhook_secret,"warning":"The API key and webhook secret are shown only once."}


def _merchant(key: str, db: Session) -> MerchantClient:
    if not key:
        raise HTTPException(401, "Merchant API key is required")
    digest = hashlib.sha256(key.encode()).hexdigest()
    client = db.query(MerchantClient).filter(
        MerchantClient.api_key_prefix == key[:16], MerchantClient.enabled.is_(True)
    ).first()
    if client and not secrets.compare_digest(client.api_key_hash, digest):
        client = None
    if not client: raise HTTPException(401, "Invalid merchant API key")
    return client


@router.get("/merchant/clients", dependencies=[Depends(require_session)])
def list_merchant_clients(db: Session = Depends(get_db)):
    rows = db.query(MerchantClient).order_by(MerchantClient.created_at.desc()).all()
    return [{"id":r.id, "name":r.name, "wallet_id":r.wallet_id,
             "api_key_prefix":r.api_key_prefix, "webhook_configured":bool(r.alert_destination_id),
             "enabled":r.enabled, "created_at":r.created_at.isoformat()} for r in rows]


@router.delete("/merchant/clients/{client_id}", dependencies=[Depends(require_session)])
def disable_merchant_client(client_id: int, db: Session = Depends(get_db)):
    client = db.query(MerchantClient).filter(MerchantClient.id == client_id).first()
    if not client: raise HTTPException(404, "Merchant client not found")
    client.enabled = False
    if client.alert_destination_id:
        destination = db.query(AlertDestination).filter(AlertDestination.id == client.alert_destination_id).first()
        if destination: destination.enabled = False
    db.commit()
    return {"ok": True}


@router.post("/merchant/invoices")
def merchant_create_invoice(body: CreateInvoiceRequest, x_sara_merchant_key: str = Header(""), db: Session = Depends(get_db)):
    client = _merchant(x_sara_merchant_key, db)
    wallet = db.query(Wallet).filter(Wallet.id == client.wallet_id).first()
    row, payload = _create_invoice(db, wallet, body, client.id)
    return {**_invoice_dict(row), "payment_page":f"/api/payments/page/{row.reference}", "payload":payload}


@router.get("/merchant/invoices/{reference}")
def merchant_invoice(reference: str, x_sara_merchant_key: str = Header(""), db: Session = Depends(get_db)):
    client = _merchant(x_sara_merchant_key, db)
    row = db.query(PaymentRequest).filter(PaymentRequest.reference == reference, PaymentRequest.merchant_client_id == client.id).first()
    if not row: raise HTTPException(404, "Invoice not found")
    if row.status in ("pending", "overdue"): check_payment_request(db, row)
    data = _invoice_dict(row)
    db.commit()
    return data


class UpdateRequestStatus(BaseModel):
    status: str  # "pending" | "paid" | "cancelled"


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


@router.get("/parse-uri")
def parse_uri(uri: str = Query(..., max_length=2000)):
    """Reads a wallet-standard (EIP-681) payment URI — the QR an invoice shows
    — so Scan to Pay works on it. Only Sara's trusted USDC contract and each
    network's native asset are accepted."""
    from app.core.assets import token_enabled
    try:
        data = parse_eip681(uri)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not token_enabled(data["token"], data["network"]):
        raise HTTPException(400, f"{data['token']} is disabled on {data['network'].capitalize()} in Sara's settings.")
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


@router.patch("/requests/{request_id}", dependencies=[Depends(require_session)])
def update_request(request_id: int, req: UpdateRequestStatus, db: Session = Depends(get_db)):
    row = db.query(PaymentRequest).filter(PaymentRequest.id == request_id).first()
    if not row:
        raise HTTPException(404, "Payment request not found")
    if req.status not in ("pending", "paid", "cancelled"):
        raise HTTPException(400, "status must be pending, paid, or cancelled")
    old_status = row.status
    row.status = req.status
    from app.core.audit import append_audit
    from app.core.events import publish
    details = {"reference": row.reference, "old_status": old_status, "new_status": req.status}
    publish(
        db, "payment_request.status_changed", details,
        aggregate_type="payment_request", aggregate_id=str(row.id),
    )
    append_audit(
        db, "payment_request.status_changed", "payment_request",
        resource_id=str(row.id), details=details,
    )
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
