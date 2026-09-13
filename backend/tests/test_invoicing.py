from datetime import datetime, timedelta
from decimal import Decimal
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AlertDestination, Base, DomainEvent, MerchantClient, PaymentRequest, Transaction, Wallet
from app.routers import payments
from app.services import alerts
from app.tools.payments import links, reconcile


class InvoicingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def create_request(self, amount="12.345678", **fields):
        with patch("app.core.assets.network_enabled", return_value=True), \
             patch.object(links, "is_trusted_token", return_value=(True, "USDC")), \
             patch("app.tools.market.paraswap.resolve_token", return_value=("0x" + "22" * 20, 6)):
            row, payload = links.create_payment_request(
                self.db, self.wallet, "polygon", "USDC", Decimal(amount), **fields
            )
        return row, links.decode_payload(payload)

    def test_invoice_preserves_exact_amount_and_customer_fields(self):
        due = datetime.utcnow() + timedelta(days=7)
        row, payload = self.create_request(
            customer_name="Acme", customer_email="accounts@example.com",
            description="September retainer", due_date=due,
        )
        self.assertEqual((row.amount_raw, row.decimals), ("12345678", 6))
        self.assertEqual(payload["amount"], "12.345678")
        self.assertEqual((row.customer_name, row.customer_email), ("Acme", "accounts@example.com"))
        self.assertEqual(row.payment_address, self.wallet.address)

    def test_overdue_invoice_still_reconciles_and_links_receipt(self):
        row, _ = self.create_request(due_date=datetime.utcnow() - timedelta(days=1))
        row.status = "overdue"
        tx = Transaction(wallet_id=self.wallet.id, chain="evm", network="polygon", tx_hash="0xpaid",
                         direction="incoming", amount=12.345678, token="USDC")
        self.db.add(tx)
        self.db.commit()
        with patch.object(reconcile, "check_evm_request", return_value="0xpaid"):
            self.assertTrue(reconcile.check_payment_request(self.db, row))
        self.assertEqual((row.status, row.matched_tx_hash), ("paid", "0xpaid"))
        self.assertEqual((tx.reference, tx.category), (row.reference, "invoice_payment"))
        event = self.db.query(DomainEvent).filter_by(event_type="payment_request.paid").one()
        self.assertIsNone(json.loads(event.payload)["merchant_client_id"])

    def test_merchant_api_key_is_hashed_and_scoped(self):
        key = "sara_live_test-secret"
        import hashlib
        client = MerchantClient(name="Shop", wallet_id=self.wallet.id, api_key_prefix=key[:16],
                                api_key_hash=hashlib.sha256(key.encode()).hexdigest())
        self.db.add(client)
        self.db.commit()
        self.assertEqual(payments._merchant(key, self.db).id, client.id)
        with self.assertRaises(Exception):
            payments._merchant(key + "wrong", self.db)
        self.assertNotIn("test-secret", client.api_key_hash)

    def test_merchant_webhook_receives_only_its_invoice_event(self):
        self.db.add_all([
            AlertDestination(kind="webhook", target="https://one.example/hook",
                             secret=json.dumps({"merchant_client_id": 1, "event_types": ["payment_request.paid"]})),
            AlertDestination(kind="webhook", target="https://two.example/hook",
                             secret=json.dumps({"merchant_client_id": 2, "event_types": ["payment_request.paid"]})),
        ])
        self.db.commit()
        event = SimpleNamespace(id=99, event_type="payment_request.paid")
        with patch.object(alerts, "SessionLocal", return_value=self.db), patch.object(alerts, "_send") as send:
            alerts.deliver_event(event, {"merchant_client_id": 2})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(send.call_args.args[0].secret)["merchant_client_id"], 2)


if __name__ == "__main__":
    unittest.main()
