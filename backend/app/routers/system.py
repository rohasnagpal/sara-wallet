from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.audit import verify_chain
from app.core.session_auth import require_session
from app.db.models import AuditLog, DomainEvent, SchemaMigration, Transaction
from app.db.session import get_db

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_session)])


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
    }
