from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.audit import verify_chain
from app.core.session_auth import require_session
from app.db.models import AuditLog, DomainEvent, SaraName, SchemaMigration, Transaction
from app.db.session import get_db

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_session)])


def _sara_names_status(db: Session) -> dict:
    """No wallet secrets or personal data here — counts and a block-cursor
    lag number only (Stage 7 monitoring)."""
    from app.tools.names import sara_names
    from app.services.names_indexer import cursor_lag

    if not sara_names.is_configured():
        return {"configured": False}
    return {
        "configured": True,
        "names": {
            status: db.query(SaraName).filter(SaraName.status == status).count()
            for status in ("pending", "committed", "registered", "renewed", "transferred_away")
        },
        "indexer": cursor_lag(db),
    }


@router.get("/foundation")
def foundation_status(db: Session = Depends(get_db)):
    return {
        "schema_migrations": [
            row.version for row in db.query(SchemaMigration).order_by(SchemaMigration.version).all()
        ],
        "transactions": {
            status: db.query(Transaction).filter(Transaction.status == status).count()
            for status in ("submitted", "confirmed", "failed")
        },
        "events": {
            status: db.query(DomainEvent).filter(DomainEvent.status == status).count()
            for status in ("pending", "retry", "processed", "failed")
        },
        "audit_entries": db.query(AuditLog).count(),
        "audit_chain_valid": verify_chain(db),
        "sara_names": _sara_names_status(db),
    }
