"""Tamper-evident append-only audit logging."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def append_audit(
    db: Session,
    action: str,
    resource_type: str,
    *,
    resource_id: str | None = None,
    details: dict | None = None,
    actor_type: str = "local_user",
    actor_id: str = "owner",
    created_at: datetime | None = None,
) -> AuditLog:
    created_at = created_at or datetime.utcnow()
    previous = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    previous_hash = previous.entry_hash if previous else None
    canonical = json.dumps({
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": details or {},
        "previous_hash": previous_hash,
        "created_at": created_at.isoformat(timespec="microseconds"),
    }, sort_keys=True, separators=(",", ":"), default=str)
    row = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=json.dumps(details or {}, sort_keys=True, separators=(",", ":"), default=str),
        previous_hash=previous_hash,
        entry_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        created_at=created_at,
    )
    db.add(row)
    return row


def verify_chain(db: Session) -> bool:
    previous_hash = None
    for row in db.query(AuditLog).order_by(AuditLog.id).all():
        canonical = json.dumps({
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "details": json.loads(row.details),
            "previous_hash": previous_hash,
            "created_at": row.created_at.isoformat(timespec="microseconds"),
        }, sort_keys=True, separators=(",", ":"), default=str)
        expected = hashlib.sha256(canonical.encode()).hexdigest()
        if row.previous_hash != previous_hash or row.entry_hash != expected:
            return False
        previous_hash = row.entry_hash
    return True
