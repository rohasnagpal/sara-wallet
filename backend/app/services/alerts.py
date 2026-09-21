from datetime import datetime
import ipaddress
import json
import socket
from urllib.parse import urlparse

import requests

from app.db.models import AlertDelivery, AlertDestination
from app.db.session import SessionLocal


def validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Webhook URL must be an HTTPS URL without embedded credentials")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("Webhook URL must resolve to a public address")


class AlertError(Exception):
    """A delivery problem, worded for the user. Never contains the bot token."""


def telegram_error_text(status: int | None, description: str | None) -> str:
    d = (description or "").lower()
    if status == 401 or "unauthorized" in d:
        return "Telegram rejected the bot token. Check that it was copied in full."
    if "chat not found" in d:
        return ("Telegram couldn't find that chat ID. Check the number, and open your bot in Telegram "
                "and press Start first.")
    if status == 403 or "blocked" in d or "forbidden" in d:
        return "Your bot isn't allowed to message that chat. Open the bot in Telegram and press Start."
    if status == 400 and description:
        return f"Telegram refused the message: {description}"
    return f"Telegram couldn't deliver the message (error {status})."


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    """Sends one message. The Telegram URL contains the bot token, so raw
    request errors (whose text includes the URL) are never propagated."""
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=15,
        )
    except requests.RequestException:
        raise AlertError("Couldn't reach Telegram. Check your internet connection and try again.") from None
    if not response.ok:
        try:
            description = response.json().get("description")
        except ValueError:
            description = None
        raise AlertError(telegram_error_text(response.status_code, description))


def _pretty(event_type: str) -> str:
    return event_type.replace("_", " ").replace(".", ": ").capitalize()


def format_message(event_type: str, payload: dict) -> str:
    """A readable message instead of raw JSON."""
    if event_type == "balance.threshold_reached":
        try:
            decimals = int(payload.get("decimals", 6))
            balance = int(payload["balance_raw"]) / 10 ** decimals
            limit = int(payload["threshold_raw"]) / 10 ** decimals
            return (f"⚠️ Balance alert\n{payload.get('wallet', 'A wallet')} on {str(payload.get('network', '')).capitalize()}: "
                    f"{balance:g} {payload.get('token', '')} is now {payload.get('condition', '')} your limit of {limit:g}.")
        except (KeyError, TypeError, ValueError):
            pass
    if event_type == "payment_request.paid":
        return (f"✅ Invoice paid\n{payload.get('reference', '')}: {payload.get('amount', '')} {payload.get('token', '')} "
                f"on {str(payload.get('network', '')).capitalize()}")
    lines = [f"Sara: {_pretty(event_type)}"]
    for key, value in list(payload.items())[:6]:
        if isinstance(value, (str, int, float)) and len(str(value)) <= 80:
            lines.append(f"{key.replace('_', ' ')}: {value}")
    return "\n".join(lines)


def _send(destination: AlertDestination, event_type: str, payload: dict) -> None:
    config = json.loads(destination.secret or "{}")
    if destination.kind == "telegram":
        token = config.get("bot_token", "")
        if not token:
            raise AlertError("The Telegram bot token is missing.")
        send_telegram(token, destination.target, format_message(event_type, payload))
    else:
        raise ValueError("Unsupported alert destination")


def deliver_event(event, payload: dict) -> None:
    db = SessionLocal()
    try:
        destinations = db.query(AlertDestination).filter(AlertDestination.enabled.is_(True)).all()
        errors = []
        for destination in destinations:
            config = json.loads(destination.secret or "{}")
            configured_types = config.get("event_types")
            if configured_types and event.event_type not in configured_types:
                continue
            merchant_id = config.get("merchant_client_id")
            if merchant_id is not None and payload.get("merchant_client_id") != merchant_id:
                continue
            delivery = db.query(AlertDelivery).filter_by(event_id=event.id, destination_id=destination.id).first()
            if delivery and delivery.status == "delivered":
                continue
            delivery = delivery or AlertDelivery(event_id=event.id, destination_id=destination.id)
            db.add(delivery)
            try:
                _send(destination, event.event_type, payload)
                delivery.status = "delivered"
                delivery.delivered_at = datetime.utcnow()
                delivery.last_error = None
            except Exception as exc:
                delivery.status = "failed"
                delivery.last_error = str(exc)[:1000]
                errors.append(f"destination {destination.id}: {exc}")
            db.commit()
        if errors:
            raise RuntimeError("; ".join(errors))
    finally:
        db.close()


def register_alert_handlers() -> None:
    from app.core.events import subscribe
    subscribe("*", deliver_event)
