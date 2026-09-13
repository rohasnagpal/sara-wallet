"""Transactional domain-event outbox with bounded exponential retries."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import uuid

from sqlalchemy.orm import Session

from app.db.models import DomainEvent

log = logging.getLogger("sara.events")
_handlers: dict[str, list] = {}
MAX_ATTEMPTS = 8


def subscribe(event_type: str, handler) -> None:
    _handlers.setdefault(event_type, []).append(handler)


def publish(
    db: Session,
    event_type: str,
    payload: dict,
    *,
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    event_key: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_key=event_key or uuid.uuid4().hex,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
    )
    db.add(event)
    return event


def process_pending(db: Session, *, limit: int = 50, now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    wildcard = _handlers.get("*", [])
    handled_types = [name for name, handlers in _handlers.items() if name != "*" and handlers]
    if not wildcard and not handled_types:
        return 0
    query = db.query(DomainEvent).filter(
        DomainEvent.status.in_(("pending", "retry")), DomainEvent.available_at <= now,
    )
    if not wildcard:
        # Unhandled historical events must not occupy the first page forever
        # and starve later security-relevant alert deliveries.
        query = query.filter(DomainEvent.event_type.in_(handled_types))
    rows = (
        query
        .order_by(DomainEvent.id)
        .limit(limit)
        .all()
    )
    processed = 0
    for event in rows:
        handlers = _handlers.get(event.event_type, []) + _handlers.get("*", [])
        try:
            payload = json.loads(event.payload)
            for handler in handlers:
                handler(event, payload)
            event.status = "processed"
            event.processed_at = now
            event.last_error = None
            processed += 1
        except Exception as exc:
            event.attempts += 1
            event.last_error = str(exc)[:1000]
            if event.attempts >= MAX_ATTEMPTS:
                event.status = "failed"
                log.error("Event %s exhausted retries: %s", event.event_key, event.last_error)
            else:
                event.status = "retry"
                event.available_at = now + timedelta(seconds=min(3600, 2 ** event.attempts))
        db.commit()
    return processed
