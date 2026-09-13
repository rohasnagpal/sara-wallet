from datetime import datetime
import hashlib
import hmac
import ipaddress
import json
import smtplib
import socket
import ssl
from email.message import EmailMessage
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


def _send(destination: AlertDestination, event_type: str, payload: dict) -> None:
    config = json.loads(destination.secret or "{}")
    message = f"Sara alert: {event_type}\n{json.dumps(payload, sort_keys=True, default=str)}"
    if destination.kind == "telegram":
        token = config.get("bot_token", "")
        if not token:
            raise ValueError("Telegram bot token is missing")
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": destination.target, "text": message}, timeout=15,
        )
        response.raise_for_status()
    elif destination.kind == "webhook":
        validate_webhook_url(destination.target)
        body = json.dumps({"event": event_type, "data": payload}, sort_keys=True, separators=(",", ":"), default=str)
        signature = hmac.new(config.get("signing_secret", "").encode(), body.encode(), hashlib.sha256).hexdigest()
        response = requests.post(
            destination.target, data=body, timeout=15, allow_redirects=False,
            headers={"Content-Type": "application/json", "X-Sara-Signature-256": f"sha256={signature}"},
        )
        response.raise_for_status()
    elif destination.kind == "email":
        msg = EmailMessage()
        msg["Subject"] = f"Sara alert: {event_type}"
        msg["From"] = config["from_address"]
        msg["To"] = destination.target
        msg.set_content(message)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config["smtp_host"], int(config.get("smtp_port", 465)), timeout=15, context=context) as smtp:
            if config.get("username"):
                smtp.login(config["username"], config.get("password", ""))
            smtp.send_message(msg)
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
