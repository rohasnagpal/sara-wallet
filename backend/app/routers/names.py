"""Sara Names API (CLAUDE_STAGES_3_TO_7.md Stage 6.5). Commit/reveal
registration, renewal, transfer, resolution, and signed off-chain records,
all backed by the deployed SaraNamesRegistry contract on Polygon Amoy —
see app.tools.names.sara_names for the chain client and
app.tools.names.eip712_records for record signing/verification.
"""
from __future__ import annotations

import os
import secrets as secrets_module
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.audit import append_audit
from app.core.events import publish
from app.core.session_auth import require_session
from app.db.models import SaraName, SaraNameRecordCache, Wallet
from app.db.session import get_db
from app.tools.names import eip712_records, sara_names

router = APIRouter(prefix="/names", tags=["names"])


# ── deliberately public, rate-limited resolution ────────────────────────
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = 30
_rate_buckets: dict[str, list[float]] = {}


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    bucket = [t for t in _rate_buckets.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW_SECONDS]
    if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(429, "Too many resolution requests — try again shortly.")
    bucket.append(now)
    _rate_buckets[client_ip] = bucket


@router.get("/public/resolve/{name}")
def public_resolve(name: str, request: Request):
    """Returns only verified public data: current owner and expiry. No
    session token required — this is the one deliberately public endpoint,
    matching the doc's minimum API surface."""
    _check_rate_limit(request.client.host if request.client else "unknown")
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance")
    result = sara_names.resolve(name)
    if not result:
        raise HTTPException(404, "Name not found, not live, or invalid")
    return result


# ── everything else requires the local session ──────────────────────────

def _wallet_or_404(db: Session, wallet_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    return wallet


@router.get("/availability", dependencies=[Depends(require_session)])
def availability(name: str):
    error = sara_names.validate_name(name)
    if error:
        raise HTTPException(400, error)
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance (SARA_NAME_REGISTRAR_ADDRESS)")
    is_root = "." not in name
    if not is_root:
        raise HTTPException(400, "Only root names can be checked for availability; subnames are parent-controlled")
    try:
        available = sara_names.is_available(name)
        price_1yr = sara_names.price_for(name, 365 * 86400)
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the Amoy registry: {exc}")
    return {"name": name, "available": available, "price_1yr_raw": str(price_1yr), "price_1yr": sara_names.price_decimal(price_1yr)}


class CommitBody(BaseModel):
    wallet_id: int
    label: str


@router.post("/commit", dependencies=[Depends(require_session)])
def commit_name(body: CommitBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key

    error = sara_names.validate_name(body.label)
    if error or "." in body.label:
        raise HTTPException(400, error or "Only a root label can be committed")
    wallet = _wallet_or_404(db, body.wallet_id)

    secret = secrets_module.token_bytes(32)
    commitment = sara_names.compute_commitment(body.label, wallet.address, secret)
    key = decrypt_key(wallet.encrypted_key)
    try:
        tx_hash = sara_names.commit(key, commitment)
    finally:
        key = None

    node = sara_names.node_hex(body.label)
    row = db.query(SaraName).filter(SaraName.node == node).first()
    if not row:
        row = SaraName(node=node, label=body.label, wallet_id=wallet.id, status="committed", commit_tx_hash=tx_hash)
        db.add(row)
    else:
        row.status = "committed"
        row.commit_tx_hash = tx_hash
    db.flush()
    append_audit(db, "sara_name.committed", "sara_name", resource_id=str(row.id),
                 details={"label": body.label, "tx_hash": tx_hash})
    db.commit()
    return {
        "label": body.label, "node": node, "commitment": "0x" + commitment.hex(),
        "secret": "0x" + secret.hex(),  # the caller MUST hold onto this to reveal — Sara does not persist it in plaintext
        "tx_hash": tx_hash,
        "reveal_ready_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    }


class RegisterBody(BaseModel):
    wallet_id: int
    label: str
    owner_address: str | None = None
    duration_seconds: int = 365 * 86400
    secret: str  # 0x-prefixed hex, from /commit's response
    passphrase: str


@router.post("/register", dependencies=[Depends(require_session)])
def register_name(body: RegisterBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    error = sara_names.validate_name(body.label)
    if error or "." in body.label:
        raise HTTPException(400, error or "Only a root label can be registered")
    wallet = _wallet_or_404(db, body.wallet_id)
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    owner_address = body.owner_address or wallet.address

    try:
        price = sara_names.price_for(body.label, body.duration_seconds)
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the Amoy registry: {exc}")

    secret_bytes = bytes.fromhex(body.secret[2:] if body.secret.startswith("0x") else body.secret)
    key = decrypt_key(wallet.encrypted_key)
    try:
        approval_tx = sara_names.ensure_usdc_allowance(key, price)
        tx_hash = sara_names.register(key, body.label, owner_address, body.duration_seconds, secret_bytes)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    node = sara_names.node_hex(body.label)
    row = db.query(SaraName).filter(SaraName.node == node).first()
    if not row:
        row = SaraName(node=node, label=body.label, wallet_id=wallet.id)
        db.add(row)
    row.status = "registered"
    row.register_tx_hash = tx_hash
    try:
        info = sara_names.get_node(sara_names.namehash_name(body.label))
        if info and info["expiry"]:
            row.expiry = datetime.utcfromtimestamp(info["expiry"])
    except Exception:
        pass  # expiry backfills on the next indexer/reminder pass if this read fails
    db.flush()
    # Revenue operations (Stage 7.3): registration spend must show up in the
    # paying wallet's own ledger/accounting, not disappear once broadcast.
    from app.routers.chat import _record_submitted_transaction
    _record_submitted_transaction(
        db, wallet_id=wallet.id, network="amoy", tx_hash=tx_hash,
        from_address=wallet.address, to_address=sara_names.registry_address(),
        amount=float(price) / 1_000000, amount_raw=price, decimals=6, token="USDC",
        category="name_registration", reference=body.label,
    )
    publish(db, "sara_name.registered", {"label": body.label, "node": node, "tx_hash": tx_hash, "owner": owner_address},
            aggregate_type="sara_name", aggregate_id=str(row.id), event_key=f"sara_name:register:{tx_hash}")
    append_audit(db, "sara_name.registered", "sara_name", resource_id=str(row.id),
                 details={"label": body.label, "tx_hash": tx_hash, "approval_tx": approval_tx})
    db.commit()
    return {"label": body.label, "node": node, "tx_hash": tx_hash, "approval_tx_hash": approval_tx}


class RenewBody(BaseModel):
    wallet_id: int
    label: str
    duration_seconds: int = 365 * 86400
    passphrase: str


@router.post("/renew", dependencies=[Depends(require_session)])
def renew_name(body: RenewBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    wallet = _wallet_or_404(db, body.wallet_id)
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    try:
        price = sara_names.price_for(body.label, body.duration_seconds)
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the Amoy registry: {exc}")

    key = decrypt_key(wallet.encrypted_key)
    try:
        approval_tx = sara_names.ensure_usdc_allowance(key, price)
        tx_hash = sara_names.renew(key, body.label, body.duration_seconds)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    node = sara_names.node_hex(body.label)
    row = db.query(SaraName).filter(SaraName.node == node).first()
    if row:
        row.status = "renewed"
        row.last_renew_tx_hash = tx_hash
        try:
            info = sara_names.get_node(sara_names.namehash_name(body.label))
            if info and info["expiry"]:
                row.expiry = datetime.utcfromtimestamp(info["expiry"])
        except Exception:
            pass
    from app.routers.chat import _record_submitted_transaction
    _record_submitted_transaction(
        db, wallet_id=wallet.id, network="amoy", tx_hash=tx_hash,
        from_address=wallet.address, to_address=sara_names.registry_address(),
        amount=float(price) / 1_000000, amount_raw=price, decimals=6, token="USDC",
        category="name_renewal", reference=body.label,
    )
    append_audit(db, "sara_name.renewed", "sara_name", resource_id=str(row.id) if row else None,
                 details={"label": body.label, "tx_hash": tx_hash})
    db.commit()
    return {"label": body.label, "tx_hash": tx_hash, "approval_tx_hash": approval_tx}


class TransferBody(BaseModel):
    wallet_id: int
    label: str
    new_owner: str
    passphrase: str


@router.post("/transfer", dependencies=[Depends(require_session)])
def transfer_name(body: TransferBody, db: Session = Depends(get_db)):
    from web3 import Web3
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    if not Web3.is_address(body.new_owner):
        raise HTTPException(400, "Invalid new_owner address")
    wallet = _wallet_or_404(db, body.wallet_id)
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")

    key = decrypt_key(wallet.encrypted_key)
    try:
        tx_hash = sara_names.transfer_root(key, body.label, body.new_owner)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    node = sara_names.node_hex(body.label)
    row = db.query(SaraName).filter(SaraName.node == node).first()
    if row:
        row.status = "transferred_away"
        row.last_transfer_tx_hash = tx_hash
    append_audit(db, "sara_name.transferred", "sara_name", resource_id=str(row.id) if row else None,
                 details={"label": body.label, "new_owner": body.new_owner, "tx_hash": tx_hash})
    db.commit()
    return {"label": body.label, "tx_hash": tx_hash}


# ── operator/fee-recipient side (Stage 7.3) — registered BEFORE /{name} so
# "admin" is never swallowed as a name path parameter. ─────────────────────

@router.get("/admin/fees", dependencies=[Depends(require_session)])
def admin_fees():
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance")
    try:
        accumulated = sara_names.accumulated_fees()
        recipient = sara_names.fee_recipient()
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the Amoy registry: {exc}")
    return {"accumulated_raw": str(accumulated), "accumulated": sara_names.price_decimal(accumulated), "fee_recipient": recipient}


class WithdrawFeesBody(BaseModel):
    wallet_id: int
    amount: str  # decimal USDC amount
    passphrase: str


@router.post("/admin/fees/withdraw", dependencies=[Depends(require_session)])
def admin_withdraw_fees(body: WithdrawFeesBody, db: Session = Depends(get_db)):
    from datetime import datetime as _dt
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase
    from app.core.amounts import to_base_units
    from app.db.models import Transaction

    wallet = _wallet_or_404(db, body.wallet_id)
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance")
    try:
        amount_raw = to_base_units(body.amount, 6, "USDC")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    key = decrypt_key(wallet.encrypted_key)
    try:
        # The contract itself enforces caller == feeRecipient or owner();
        # an unauthorised wallet gets the revert reason surfaced here
        # rather than Sara pretending to gatekeep it locally.
        tx_hash = sara_names.withdraw_fees(key, amount_raw)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    # Not app.routers.chat._record_submitted_transaction here: that helper
    # hardcodes direction="outgoing", but a fee withdrawal moves USDC INTO
    # this wallet — recording it that way would show registry revenue as an
    # expense in the ledger/accounting, backwards from reality.
    amount = float(amount_raw) / 1_000000
    row = Transaction(
        wallet_id=wallet.id, chain="evm", network="amoy", tx_hash=tx_hash,
        from_address=sara_names.registry_address(), to_address=wallet.address,
        amount=amount, amount_raw=str(amount_raw), decimals=6, token="USDC",
        status="submitted", direction="incoming", category="name_registry_revenue",
        counterparty=sara_names.registry_address(), timestamp=_dt.utcnow(),
    )
    db.add(row)
    db.flush()
    publish(db, "sara_name.fees_withdrawn", {"tx_hash": tx_hash, "amount_raw": str(amount_raw)},
            aggregate_type="transaction", aggregate_id=str(row.id), event_key=f"tx:amoy:{tx_hash}:submitted")
    append_audit(db, "sara_name.fees_withdrawn", "sara_name_registry", details={"amount_raw": str(amount_raw), "tx_hash": tx_hash})
    db.commit()
    return {"tx_hash": tx_hash, "amount": body.amount}


@router.get("/{name}", dependencies=[Depends(require_session)])
def get_name(name: str, db: Session = Depends(get_db)):
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance")
    node = sara_names.node_hex(name)
    info = sara_names.get_node(sara_names.namehash_name(name))
    local = db.query(SaraName).filter(SaraName.node == node).first()
    return {
        "name": name, "node": node,
        "on_chain": info,
        "local_status": local.status if local else None,
        "local_wallet_id": local.wallet_id if local else None,
    }


@router.get("/{name}/subnames", dependencies=[Depends(require_session)])
def list_subnames(name: str, db: Session = Depends(get_db)):
    """Lists subnames Sara itself has created/tracked for this name. The
    registry doesn't offer on-chain enumeration by parent, so this is a
    local index over Sara's own SubnameCreated activity, not a full
    external indexer — a documented scope choice, not a bug."""
    node = sara_names.node_hex(name)
    rows = db.query(SaraName).filter(SaraName.parent_node == node).all()
    return {"subnames": [{"label": r.label, "node": r.node, "status": r.status, "wallet_id": r.wallet_id} for r in rows]}


class CreateSubnameBody(BaseModel):
    wallet_id: int
    parent_name: str
    label: str
    owner_address: str | None = None
    passphrase: str


@router.post("/{name}/subnames", dependencies=[Depends(require_session)])
def create_subname(name: str, body: CreateSubnameBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    error = sara_names.validate_label(body.label)
    if error:
        raise HTTPException(400, error)
    wallet = _wallet_or_404(db, body.wallet_id)
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    owner_address = body.owner_address or wallet.address
    parent_node = sara_names.namehash_name(body.parent_name)

    key = decrypt_key(wallet.encrypted_key)
    try:
        tx_hash = sara_names.create_subname(key, parent_node, body.label, owner_address)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None

    sub_node = "0x" + sara_names.namehash_label(parent_node, body.label).hex()
    row = SaraName(node=sub_node, label=body.label, parent_node="0x" + parent_node.hex(),
                    wallet_id=wallet.id, status="registered", register_tx_hash=tx_hash)
    db.add(row)
    db.flush()
    append_audit(db, "sara_name.subname_created", "sara_name", resource_id=str(row.id),
                 details={"parent_name": body.parent_name, "label": body.label, "tx_hash": tx_hash})
    db.commit()
    return {"label": body.label, "node": sub_node, "tx_hash": tx_hash}


# ── signed off-chain records ─────────────────────────────────────────────

class PublishRecordBody(BaseModel):
    wallet_id: int
    addresses: list[dict] = Field(default_factory=list)  # [{"network": "eip155:137", "addr": "0x..."}]
    preferred_network: str = ""
    preferred_token: str = ""
    ttl_seconds: int = Field(30 * 86400, ge=60, le=365 * 86400)
    passphrase: str


@router.post("/{name}/records", dependencies=[Depends(require_session)])
def publish_record(name: str, body: PublishRecordBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase
    from app.core.config import settings

    wallet = _wallet_or_404(db, body.wallet_id)
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance")

    node = sara_names.namehash_name(name)
    info = sara_names.get_node(node)
    authorised = {str(info.get("owner", "")).lower(), str(info.get("record_signer", "")).lower()} if info else set()
    if not info or wallet.address.lower() not in authorised:
        raise HTTPException(403, "Only the current on-chain owner or authorised record signer can publish this record")

    cached = db.query(SaraNameRecordCache).filter(SaraNameRecordCache.node == "0x" + node.hex()).first()
    next_sequence = (cached.sequence + 1) if cached else 1
    now = int(datetime.now(timezone.utc).timestamp())
    record = eip712_records.NameRecord(
        node="0x" + node.hex(), record_epoch=info["record_epoch"], sequence=next_sequence,
        issued_at=now, expires_at=now + body.ttl_seconds, addresses=body.addresses,
        preferred_network=body.preferred_network, preferred_token=body.preferred_token,
    ).with_content_hash()
    valid, reason = record.validate_addresses()
    if not valid:
        raise HTTPException(400, reason)

    key = decrypt_key(wallet.encrypted_key)
    try:
        signature = eip712_records.sign_record(
            record, chain_id=sara_names.AMOY_CHAIN_ID, registry_address=sara_names.registry_address(), private_key=key,
        )
    finally:
        key = None

    if cached:
        cached.sequence = record.sequence
        cached.record_epoch = record.record_epoch
        cached.signed_payload = record.canonical_json()
        cached.signature = signature
        cached.revalidated_at = datetime.utcnow()
    else:
        db.add(SaraNameRecordCache(
            node="0x" + node.hex(), sequence=record.sequence, record_epoch=record.record_epoch,
            signed_payload=record.canonical_json(), signature=signature, revalidated_at=datetime.utcnow(),
        ))

    published_externally = False
    if settings.SARA_NAME_SERVICE_URL:
        import requests
        try:
            requests.post(
                f"{settings.SARA_NAME_SERVICE_URL.rstrip('/')}/records",
                json={"node": "0x" + node.hex(), "payload": record.canonical_json(), "signature": signature},
                timeout=15,
            )
            published_externally = True
        except Exception:
            pass  # local cache still holds the record; external publish is best-effort

    append_audit(db, "sara_name.record_published", "sara_name", resource_id="0x" + node.hex(),
                 details={"name": name, "sequence": record.sequence, "published_externally": published_externally})
    db.commit()
    return {"node": "0x" + node.hex(), "sequence": record.sequence, "signature": signature, "published_externally": published_externally}


@router.get("/{name}/records", dependencies=[Depends(require_session)])
def get_record(name: str, db: Session = Depends(get_db)):
    """Fetches the best-known signed record (local cache, else the external
    service if configured) and re-verifies it against live on-chain state
    before returning it — a cached or externally-fetched record is never
    trusted on its own."""
    if not sara_names.is_configured():
        raise HTTPException(503, "Sara Names is not configured on this instance")
    node_bytes = sara_names.namehash_name(name)
    node = "0x" + node_bytes.hex()
    info = sara_names.get_node(node_bytes)
    if not info:
        raise HTTPException(404, "Name not found on-chain")

    cached = db.query(SaraNameRecordCache).filter(SaraNameRecordCache.node == node).first()
    payload_json, signature = (cached.signed_payload, cached.signature) if cached else (None, None)

    if payload_json is None:
        from app.core.config import settings
        if settings.SARA_NAME_SERVICE_URL:
            import requests
            try:
                resp = requests.get(f"{settings.SARA_NAME_SERVICE_URL.rstrip('/')}/records/{node}", timeout=15)
                if resp.ok:
                    body = resp.json()
                    payload_json, signature = body.get("payload"), body.get("signature")
            except Exception:
                pass

    if payload_json is None:
        return {"node": node, "verified": False, "reason": "no record found (local cache empty, no service configured or reachable)"}

    import json as _json
    fields = _json.loads(payload_json)
    record = eip712_records.NameRecord(
        node=fields["node"], record_epoch=int(fields["recordEpoch"]), sequence=int(fields["sequence"]),
        issued_at=int(fields["issuedAt"]), expires_at=int(fields["expiresAt"]), addresses=fields["addresses"],
        preferred_network=fields["preferredNetwork"], preferred_token=fields["preferredToken"],
        content_hash=fields.get("contentHash", "0x" + "00" * 32),
    )
    valid, reason = eip712_records.verify_record(
        record, signature, chain_id=sara_names.AMOY_CHAIN_ID, registry_address=sara_names.registry_address(),
        expected_node=node, current_owner=info["owner"], current_record_signer=info["record_signer"],
        current_epoch=info["record_epoch"],
    )
    if not valid:
        return {"node": node, "verified": False, "reason": reason}
    return {
        "node": node, "verified": True, "addresses": record.addresses,
        "preferred_network": record.preferred_network, "preferred_token": record.preferred_token,
        "sequence": record.sequence, "issued_at": record.issued_at, "expires_at": record.expires_at,
    }
