"""Minimal role/permission substrate for future approval workflows."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import Principal, PrincipalRole, Role

OWNER_PERMISSIONS = ("wallet.read", "transaction.prepare", "transaction.approve", "settings.manage")


def ensure_local_owner(db: Session) -> Principal:
    principal = db.query(Principal).filter(Principal.external_id == "local-owner").first()
    if principal is None:
        principal = Principal(external_id="local-owner", display_name="Local owner")
        db.add(principal)
        db.flush()
    role = db.query(Role).filter(Role.name == "owner").first()
    if role is None:
        role = Role(name="owner", permissions=json.dumps(list(OWNER_PERMISSIONS)))
        db.add(role)
        db.flush()
    link = db.query(PrincipalRole).filter(
        PrincipalRole.principal_id == principal.id,
        PrincipalRole.role_id == role.id,
    ).first()
    if link is None:
        db.add(PrincipalRole(principal_id=principal.id, role_id=role.id))
    db.commit()
    return principal


def has_permission(db: Session, principal_id: int, permission: str) -> bool:
    roles = (
        db.query(Role)
        .join(PrincipalRole, PrincipalRole.role_id == Role.id)
        .filter(PrincipalRole.principal_id == principal_id)
        .all()
    )
    return any(permission in json.loads(role.permissions) or "*" in json.loads(role.permissions) for role in roles)
