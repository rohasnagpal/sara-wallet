"""Alerts are Telegram-only: validated input, a test message before saving,
readable messages, only the chosen events delivered, and the bot token never
appearing in any error text (Telegram's URL contains it)."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AlertDestination, Base
from app.routers import safety
from app.services import alerts

TOKEN = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ-1234567"
CHAT = "987654321"


def resp(status=200, body=None):
    r = MagicMock()
    r.ok = 200 <= status < 300
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


class AlertsTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()

    def tearDown(self):
        self.db.close()

    def create(self, target=CHAT, **config):
        return safety.create_destination(
            safety.DestinationBody(kind="telegram", target=target, config={"bot_token": TOKEN, **config}), self.db)


class ValidationTests(AlertsTestCase):
    def rejected(self, **kw):
        with self.assertRaises(HTTPException) as ctx:
            safety.create_destination(safety.DestinationBody(**kw), self.db)
        self.assertEqual(ctx.exception.status_code, 400)
        return ctx.exception.detail

    def test_only_telegram_can_be_added(self):
        for kind in ("email", "webhook"):
            self.assertIn("Only Telegram", self.rejected(kind=kind, target="x", config={}))

    def test_missing_or_malformed_token_and_chat_id_get_plain_messages(self):
        self.assertIn("bot token", self.rejected(target=CHAT, config={}))
        self.assertIn("doesn't look like a bot token", self.rejected(target=CHAT, config={"bot_token": "nope"}))
        self.assertIn("chat ID", self.rejected(target="", config={"bot_token": TOKEN}))
        self.assertIn("should be a number", self.rejected(target="not a number", config={"bot_token": TOKEN}))

    def test_group_chat_ids_and_channel_names_are_accepted(self):
        for target in ("-1001234567890", "@mychannel"):
            self.assertEqual(self.create(target=target)["status"], "created")

    def test_event_choices_are_validated(self):
        self.assertIn("at least one", self.rejected(target=CHAT, config={"bot_token": TOKEN, "event_types": []}))
        self.assertIn("Unknown alert type", self.rejected(target=CHAT, config={"bot_token": TOKEN, "event_types": ["made.up"]}))
        self.create(event_types=["balance.threshold_reached"])   # ok
        self.create()                                             # no filter = everything, ok

    def test_saved_list_never_returns_the_bot_token(self):
        self.create(event_types=["balance.threshold_reached"])
        listing = safety.list_destinations(self.db)
        self.assertEqual(listing[0]["event_types"], ["balance.threshold_reached"])
        self.assertNotIn(TOKEN, json.dumps(listing))

    def test_options_lists_the_trigger_groups(self):
        groups = {g["id"]: g for g in safety.alert_options()["groups"]}
        self.assertEqual(groups["balance"]["event_types"], ["balance.threshold_reached"])
        self.assertIn("invoices", groups)


class TestMessageTests(AlertsTestCase):
    def test_a_test_message_is_sent_without_saving(self):
        with patch("requests.post", return_value=resp(200, {"ok": True})) as post:
            result = safety.test_new_destination(safety.DestinationBody(target=CHAT, config={"bot_token": TOKEN}))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], CHAT)
        self.assertEqual(self.db.query(AlertDestination).count(), 0)

    def test_telegram_failures_are_explained_in_plain_words(self):
        cases = [
            (401, {"description": "Unauthorized"}, "rejected the bot token"),
            (400, {"description": "Bad Request: chat not found"}, "press Start first"),
            (403, {"description": "Forbidden: bot can't initiate conversation with a user"}, "isn't allowed"),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status), patch("requests.post", return_value=resp(status, body)):
                with self.assertRaises(HTTPException) as ctx:
                    safety.test_new_destination(safety.DestinationBody(target=CHAT, config={"bot_token": TOKEN}))
                self.assertIn(expected, ctx.exception.detail)
                self.assertNotIn(TOKEN, ctx.exception.detail)

    def test_a_network_failure_never_leaks_the_token(self):
        boom = requests.ConnectionError(f"HTTPSConnectionPool: url /bot{TOKEN}/sendMessage failed")
        with patch("requests.post", side_effect=boom):
            with self.assertRaises(HTTPException) as ctx:
                safety.test_new_destination(safety.DestinationBody(target=CHAT, config={"bot_token": TOKEN}))
        self.assertIn("Couldn't reach Telegram", ctx.exception.detail)
        self.assertNotIn(TOKEN, ctx.exception.detail)

    def test_saved_destination_can_be_tested_and_legacy_kinds_are_explained(self):
        rid = self.create()["id"]
        with patch("requests.post", return_value=resp(200, {"ok": True})):
            self.assertEqual(safety.test_saved_destination(rid, self.db), {"ok": True})
        legacy = AlertDestination(kind="email", target="a@b.c", secret="{}")
        self.db.add(legacy); self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            safety.test_saved_destination(legacy.id, self.db)
        self.assertIn("no longer supported", ctx.exception.detail)


class DeliveryTests(AlertsTestCase):
    def test_delivery_errors_stored_for_retry_never_contain_the_token(self):
        row = AlertDestination(kind="telegram", target=CHAT, secret=json.dumps({"bot_token": TOKEN}))
        boom = requests.ConnectionError(f"url /bot{TOKEN}/sendMessage")
        with patch("requests.post", side_effect=boom):
            with self.assertRaises(alerts.AlertError) as ctx:
                alerts._send(row, "balance.threshold_reached", {})
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_only_the_chosen_events_are_delivered(self):
        self.create(event_types=["balance.threshold_reached"])
        sent = []
        with patch.object(alerts, "SessionLocal", return_value=self.db), \
             patch.object(alerts, "_send", side_effect=lambda d, t, p: sent.append(t)):
            for event_type in ("transaction.indexed", "balance.threshold_reached", "accounting.legs_matched"):
                alerts.deliver_event(SimpleNamespace(id=hash(event_type) % 10_000, event_type=event_type), {})
        self.assertEqual(sent, ["balance.threshold_reached"])

    def test_unsupported_legacy_kind_fails_clearly_instead_of_sending(self):
        row = AlertDestination(kind="webhook", target="https://x.example", secret="{}")
        with self.assertRaises(ValueError):
            alerts._send(row, "x.y", {})

    def test_messages_are_readable_not_raw_json(self):
        text = alerts.format_message("balance.threshold_reached", {
            "wallet": "rohas", "network": "polygon", "token": "USDC", "balance_raw": "3200000",
            "threshold_raw": "5000000", "decimals": 6, "condition": "below"})
        self.assertIn("rohas on Polygon", text)
        self.assertIn("3.2 USDC", text)
        self.assertIn("below your limit of 5", text)
        self.assertNotIn("{", text)
        self.assertIn("Invoice paid", alerts.format_message(
            "payment_request.paid", {"reference": "INV-1", "amount": "10", "token": "USDC", "network": "base"}))


if __name__ == "__main__":
    unittest.main()
