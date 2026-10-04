import json
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.session_auth import require_session
from app.db.models import AlertDestination, BalanceMonitor, Wallet
from app.db.session import get_db

router = APIRouter(prefix="/safety", tags=["safety"], dependencies=[Depends(require_session)])

class MonitorBody(BaseModel):
    wallet_id: int
    network: str = "polygon"
    token: str = "USDC"
    condition: str = "below"
    threshold: str


@router.get("/monitors")
def list_monitors(db: Session = Depends(get_db)):
    return [{"id": m.id, "wallet_id": m.wallet_id, "network": m.network, "token": m.token,
             "condition": m.condition, "threshold_raw": m.threshold_raw, "decimals": m.decimals,
             "last_value_raw": m.last_value_raw, "triggered": m.triggered, "enabled": m.enabled}
            for m in db.query(BalanceMonitor).order_by(BalanceMonitor.id).all()]


@router.post("/monitors")
def create_monitor(body: MonitorBody, db: Session = Depends(get_db)):
    from app.core.amounts import to_base_units
    from app.core.assets import NETWORKS
    from app.tools.market.paraswap import resolve_token
    if body.condition not in ("below", "above"):
        raise HTTPException(400, "condition must be below or above")
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    native = NETWORKS.get(body.network, {}).get("native")
    if body.token.upper() == native:
        address, decimals = None, 18
    else:
        resolved = resolve_token(body.token, body.network)
        if not resolved:
            raise HTTPException(400, "Token is disabled or unsupported")
        address, decimals = resolved
    monitor = BalanceMonitor(wallet_id=wallet.id, network=body.network, token=body.token.upper(),
                             token_address=address, decimals=decimals, condition=body.condition,
                             threshold_raw=str(to_base_units(body.threshold, decimals, body.token)))
    db.add(monitor); db.commit(); db.refresh(monitor)
    return {"id": monitor.id, "status": "created"}


@router.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)):
    row = db.query(BalanceMonitor).filter(BalanceMonitor.id == monitor_id).first()
    if not row: raise HTTPException(404, "Monitor not found")
    db.delete(row); db.commit(); return {"deleted": monitor_id}


class DestinationBody(BaseModel):
    kind: str = "telegram"
    target: str                 # the Telegram chat ID
    config: dict = {}           # {"bot_token": "...", "event_types": [...] (optional)}


# What a destination can be told about. Anything not listed here stays quiet.
ALERT_EVENT_GROUPS = [
    ("balance", "Balance alerts", ["balance.threshold_reached"]),
    ("invoices", "Invoices paid", ["payment_request.paid"]),
    ("transactions", "Transactions confirmed or failed",
     ["transaction.confirmed", "transaction.failed", "transaction.reorg_detected"]),
    ("batches", "Batch payments executed", ["payment_batch.executed"]),
    ("schedules", "Scheduled payments created", ["schedule.materialized"]),
]
_KNOWN_EVENTS = {e for _, _, events in ALERT_EVENT_GROUPS for e in events}
_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_CHAT_ID_RE = re.compile(r"^(-?\d+|@[A-Za-z][A-Za-z0-9_]{3,})$")


def _validate_telegram(target: str, config: dict) -> tuple[str, str]:
    token = str(config.get("bot_token", "")).strip()
    chat_id = target.strip()
    if not token:
        raise HTTPException(400, "Enter your bot token (the long code @BotFather gave you).")
    if not _BOT_TOKEN_RE.match(token):
        raise HTTPException(400, "That doesn't look like a bot token. It looks like 123456789:ABC-DEF..., with a colon in the middle.")
    if not chat_id:
        raise HTTPException(400, "Enter your chat ID (the number @userinfobot sent you).")
    if not _CHAT_ID_RE.match(chat_id):
        raise HTTPException(400, "The chat ID should be a number such as 123456789 (or @yourchannel for a public channel).")
    return token, chat_id


def _validate_events(config: dict) -> None:
    events = config.get("event_types")
    if not isinstance(events, list) or not events:
        raise HTTPException(400, "Choose at least one thing to be told about.")
    unknown = [e for e in events if e not in _KNOWN_EVENTS]
    if unknown:
        raise HTTPException(400, f"Unknown alert type: {unknown[0]}")


@router.get("/alerts/options")
def alert_options():
    return {"groups": [{"id": gid, "label": label, "event_types": events} for gid, label, events in ALERT_EVENT_GROUPS]}


@router.get("/alerts")
def list_destinations(db: Session = Depends(get_db)):
    out = []
    for d in db.query(AlertDestination).order_by(AlertDestination.id).all():
        try:
            config = json.loads(d.secret or "{}")
        except ValueError:
            config = {}
        # the bot token / any secret is never returned
        out.append({"id": d.id, "kind": d.kind, "target": d.target, "enabled": d.enabled,
                    "event_types": config.get("event_types") or sorted(_KNOWN_EVENTS)})
    return out


@router.post("/alerts")
def create_destination(body: DestinationBody, db: Session = Depends(get_db)):
    if body.kind != "telegram":
        raise HTTPException(400, "Only Telegram alerts are supported.")
    token, chat_id = _validate_telegram(body.target, body.config)
    _validate_events(body.config)
    config = {"bot_token": token, "event_types": body.config["event_types"]}
    row = AlertDestination(kind="telegram", target=chat_id, secret=json.dumps(config))
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "status": "created"}


_TEST_MESSAGE = "✅ Test message from Sara. You'll get your alerts here."


def _send_test(token: str, chat_id: str) -> dict:
    from app.services.alerts import AlertError, send_telegram
    try:
        send_telegram(token, chat_id, _TEST_MESSAGE)
    except AlertError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.post("/alerts/test")
def test_new_destination(body: DestinationBody):
    """Send a test message using details that haven't been saved yet."""
    if body.kind != "telegram":
        raise HTTPException(400, "Only Telegram alerts are supported.")
    token, chat_id = _validate_telegram(body.target, body.config)
    return _send_test(token, chat_id)


@router.post("/alerts/{destination_id}/test")
def test_saved_destination(destination_id: int, db: Session = Depends(get_db)):
    row = db.query(AlertDestination).filter(AlertDestination.id == destination_id).first()
    if not row:
        raise HTTPException(404, "Alert destination not found")
    if row.kind != "telegram":
        raise HTTPException(400, "This destination type is no longer supported. Delete it and add a Telegram one.")
    config = json.loads(row.secret or "{}")
    return _send_test(str(config.get("bot_token", "")), row.target)


@router.delete("/alerts/{destination_id}")
def delete_destination(destination_id: int, db: Session = Depends(get_db)):
    row = db.query(AlertDestination).filter(AlertDestination.id == destination_id).first()
    if not row: raise HTTPException(404, "Alert destination not found")
    db.delete(row); db.commit(); return {"deleted": destination_id}
