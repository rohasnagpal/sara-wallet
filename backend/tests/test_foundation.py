from datetime import datetime
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.audit import append_audit, verify_chain
from app.core.events import _handlers, process_pending, publish
from app.db.migrations import run_migrations
from app.db.models import AuditLog, Base, DomainEvent, Transaction, Wallet
from app.services import transaction_monitor
from app.routers import chat


class MigrationTests(unittest.TestCase):
    def test_legacy_database_is_upgraded_and_versioned(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            engine = create_engine(f"sqlite:///{db_file.name}")
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE transactions (id INTEGER PRIMARY KEY, wallet_id INTEGER NOT NULL, "
                    "chain VARCHAR, tx_hash VARCHAR, to_address VARCHAR, amount FLOAT, token VARCHAR, "
                    "status VARCHAR, timestamp DATETIME)"
                ))
                conn.execute(text(
                    "CREATE TABLE payment_requests (id INTEGER PRIMARY KEY, chain VARCHAR NOT NULL, "
                    "network VARCHAR NOT NULL)"
                ))
            # New tables are created by normal startup before migrations run.
            Base.metadata.create_all(engine)
            run_migrations(engine)
            run_migrations(engine)  # explicitly idempotent

            tx_columns = {c["name"] for c in inspect(engine).get_columns("transactions")}
            self.assertTrue({"network", "amount_raw", "confirmations", "fiat_usd_value", "tags"} <= tx_columns)
            invoice_columns = {c["name"] for c in inspect(engine).get_columns("payment_requests")}
            self.assertTrue({"amount_raw", "decimals", "customer_name", "due_date", "payment_address", "merchant_client_id"} <= invoice_columns)
            with engine.connect() as conn:
                versions = conn.execute(text("SELECT version FROM schema_migrations")).all()
            self.assertEqual([v[0] for v in versions], [
                "001_legacy_payment_fields", "002_transaction_foundation", "003_wallet_intelligence",
                "004_activity_identity", "005_invoicing", "006_payment_safety",
            ])


class AuditAndEventTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        _handlers.clear()

    def tearDown(self):
        _handlers.clear()

    def test_audit_chain_detects_tampering(self):
        db = self.Session()
        append_audit(db, "transaction.submitted", "transaction", resource_id="1", details={"a": 1})
        append_audit(db, "transaction.confirmed", "transaction", resource_id="1", details={"b": 2})
        db.commit()
        self.assertTrue(verify_chain(db))
        first = db.query(AuditLog).order_by(AuditLog.id).first()
        first.details = json.dumps({"a": 999})
        db.commit()
        self.assertFalse(verify_chain(db))
        db.close()

    def test_outbox_retries_then_processes(self):
        from app.core.events import subscribe
        db = self.Session()
        publish(db, "test.event", {"value": 7}, event_key="stable-key")
        db.commit()
        calls = []

        def handler(event, payload):
            calls.append(payload["value"])
            if len(calls) == 1:
                raise RuntimeError("temporary")

        subscribe("test.event", handler)
        process_pending(db)
        row = db.query(DomainEvent).one()
        self.assertEqual((row.status, row.attempts), ("retry", 1))
        row.available_at = datetime.utcnow()
        db.commit()
        process_pending(db)
        self.assertEqual(row.status, "processed")
        self.assertEqual(calls, [7, 7])
        db.close()

    def test_unhandled_events_do_not_starve_registered_handlers(self):
        from app.core.events import subscribe
        db = self.Session()
        publish(db, "future.unhandled", {"value": 1}, event_key="unhandled")
        publish(db, "active.handler", {"value": 2}, event_key="handled")
        db.commit()
        calls = []
        subscribe("active.handler", lambda event, payload: calls.append(payload["value"]))
        process_pending(db, limit=1)
        self.assertEqual(calls, [2])
        self.assertEqual(db.query(DomainEvent).filter_by(event_key="unhandled").one().status, "pending")
        self.assertEqual(db.query(DomainEvent).filter_by(event_key="handled").one().status, "processed")
        db.close()

    def test_post_broadcast_persistence_failure_is_not_reported_as_send_failure(self):
        db = self.Session()
        with patch.object(db, "commit", side_effect=OSError("disk full")):
            with self.assertRaises(chat.BroadcastPersistenceError) as raised:
                chat._record_submitted_transaction(
                    db, wallet_id=1, network="polygon", tx_hash="0xabc",
                    from_address="0x" + "11" * 20, to_address="0x" + "22" * 20,
                    amount=1, amount_raw=1_000_000, decimals=6,
                    token="USDC", category="transfer",
                )
        message = chat._execution_error("Send", raised.exception)
        self.assertIn("was **broadcast**", message)
        self.assertIn("Do not submit it again", message)
        self.assertIn("0xabc", message)
        db.close()


class TransactionMonitorTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_receipt_confirms_and_missing_receipt_detects_reorg(self):
        db = self.Session()
        wallet = Wallet(name="main", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        db.add(wallet)
        db.flush()
        tx = Transaction(
            wallet_id=wallet.id, chain="evm", network="polygon", tx_hash="0xabc",
            amount=1.0, amount_raw="1000000", decimals=6, token="USDC",
            status="submitted", direction="outgoing", timestamp=datetime.utcnow(),
        )
        db.add(tx)
        db.commit()

        receipt = {
            "blockNumber": 100, "blockHash": "0xblock", "status": 1,
            "gasUsed": 21000, "effectiveGasPrice": 2,
        }
        eth = SimpleNamespace(
            get_transaction_receipt=lambda _: receipt,
            get_transaction=lambda _: {"from": wallet.address},
            get_block=lambda _: {"timestamp": 1_700_000_000},
            block_number=163,
        )
        w3 = SimpleNamespace(eth=eth)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch.object(transaction_monitor, "_snapshot_valuation"):
            self.assertTrue(transaction_monitor.check_transaction(db, tx))
        self.assertEqual(tx.status, "confirmed")
        self.assertEqual(tx.confirmations, 64)
        self.assertEqual(tx.fee_raw, "42000")

        eth.get_transaction_receipt = lambda _: None
        with patch("app.chains.evm.get_web3", return_value=w3):
            transaction_monitor.check_transaction(db, tx)
        self.assertEqual(tx.status, "submitted")
        self.assertIsNone(tx.block_hash)
        self.assertEqual(
            db.query(DomainEvent).filter(DomainEvent.event_type == "transaction.reorg_detected").count(), 1,
        )
        db.close()


if __name__ == "__main__":
    unittest.main()
