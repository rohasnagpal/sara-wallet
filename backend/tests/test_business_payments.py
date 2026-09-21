from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core import spending_policy
from app.db.models import (
    AddressBook, Base, PaymentBatch, PaymentBatchItem, PayrollProfile,
    Schedule, ScheduleRun, SpendingPolicy, Transaction, Wallet,
)
from app.routers import address_book, payment_batches, payroll, schedules
from app.services import batch_engine
from app.services import schedules as schedules_service

ADDR_A = "0x" + "22" * 20
ADDR_B = "0x" + "33" * 20
ADDR_C = "0x" + "44" * 20


class BusinessPaymentsTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()
        self.net_patch = patch("app.core.assets.network_enabled", return_value=True)
        self.net_patch.start()
        self.addCleanup(self.net_patch.stop)

    def tearDown(self):
        self.db.close()

    def make_batch(self, kind="payment", created_by="owner", token="USDC", network="polygon"):
        batch = PaymentBatch(kind=kind, status="draft", wallet_id=self.wallet.id, network=network,
                              token=token, created_by=created_by)
        self.db.add(batch)
        self.db.flush()
        return batch

    def add_item(self, batch, row_index, recipient, amount="1.5", decimals=6, counterparty_id=None):
        amount_raw = str(int(Decimal(amount) * (Decimal(10) ** decimals)))
        item = PaymentBatchItem(batch_id=batch.id, row_index=row_index, recipient_address=recipient,
                                 amount_raw=amount_raw, decimals=decimals, counterparty_id=counterparty_id)
        self.db.add(item)
        self.db.flush()
        return item

    def stub_balance_ok(self):
        return (
            patch("app.chains.evm._get_erc20_balance_raw", return_value=1_000_000_000_000),
            patch("app.chains.evm.get_erc20_transfer_preview_raw", return_value={
                "gas_fee": 0.001, "native_balance": 10.0, "native_unit": "POL",
                "has_token_funds": True, "has_gas_funds": True, "amount_raw": 0,
            }),
        )


class DirectoryTests(BusinessPaymentsTestCase):
    """AddressBook is the unified directory (address book + counterparties
    merged at the user's request — see AddressBook's docstring)."""

    def test_create_list_and_delete_entry(self):
        row = address_book.add_entry(
            address_book.DirectoryEntry(nickname="acme", address=ADDR_A, type="vendor", display_name="Acme Vendor"),
            self.db,
        )
        self.assertEqual(row["display_name"], "Acme Vendor")
        self.assertEqual(row["address"], ADDR_A)
        self.assertEqual(row["type"], "vendor")

        self.assertEqual(len(address_book.list_entries(None, None, self.db)), 1)
        self.assertEqual(len(address_book.list_entries("vendor", None, self.db)), 1)
        self.assertEqual(address_book.list_entries("friend", None, self.db), [])

        deleted = address_book.delete_entry(row["nickname"], self.db)
        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual(address_book.list_entries(None, None, self.db), [])

    def test_invalid_address_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            address_book.add_entry(
                address_book.DirectoryEntry(nickname="bad", address="not-an-address", type="vendor"),
                self.db,
            )


class BatchValidationTests(BusinessPaymentsTestCase):
    def test_bad_address_and_duplicate_row_are_rejected(self):
        batch = self.make_batch()
        self.add_item(batch, 0, "not-an-address")
        self.add_item(batch, 1, ADDR_A)
        self.add_item(batch, 2, ADDR_A)  # duplicate of row 1
        result = batch_engine.validate_batch(self.db, batch)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["item_errors"]), 2)  # bad address + one duplicate

    def test_unsupported_token_is_a_batch_error(self):
        batch = self.make_batch(token="NOTATOKEN")
        self.add_item(batch, 0, ADDR_A)
        result = batch_engine.validate_batch(self.db, batch)
        self.assertFalse(result["ok"])
        self.assertTrue(any("NOTATOKEN" in e for e in result["batch_errors"]))

    def test_insufficient_balance_is_a_batch_error(self):
        batch = self.make_batch()
        self.add_item(batch, 0, ADDR_A, amount="5")
        with patch("app.chains.evm._get_erc20_balance_raw", return_value=100_000), \
             patch("app.chains.evm.get_erc20_transfer_preview_raw", return_value={
                 "gas_fee": 0.001, "native_balance": 10.0, "native_unit": "POL",
                 "has_token_funds": False, "has_gas_funds": True, "amount_raw": 0,
             }):
            result = batch_engine.validate_batch(self.db, batch)
        self.assertFalse(result["ok"])
        self.assertTrue(any("insufficient" in e for e in result["batch_errors"]))

    def test_valid_batch_passes(self):
        batch = self.make_batch()
        self.add_item(batch, 0, ADDR_A)
        self.add_item(batch, 1, ADDR_B)
        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            result = batch_engine.validate_batch(self.db, batch)
        self.assertTrue(result["ok"], result)


class ApprovalTests(BusinessPaymentsTestCase):
    def _valid_batch(self, created_by="owner"):
        batch = self.make_batch(created_by=created_by)
        self.add_item(batch, 0, ADDR_A)
        return batch

    def test_approve_batch_succeeds(self):
        batch = self._valid_batch(created_by="owner")
        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            approval = batch_engine.approve_batch(self.db, batch, "owner")
        self.assertEqual(approval.action, "approved")
        self.assertEqual(batch.status, "approved")
        self.assertEqual(batch.approved_by, "owner")

    def test_editing_item_after_approval_invalidates_it(self):
        batch = self._valid_batch(created_by="owner")
        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            batch_engine.approve_batch(self.db, batch, "owner")
        self.assertEqual(batch.status, "approved")
        batch_engine.invalidate_approval(self.db, batch, "item edited")
        self.assertEqual(batch.status, "draft")
        self.assertIsNone(batch.approved_by)
        invalidation = self.db.query(batch_engine.BatchApproval).filter_by(batch_id=batch.id, action="invalidated").first()
        self.assertIsNotNone(invalidation)
        self.assertEqual(invalidation.reason, "item edited")


class SpendingPolicyTests(BusinessPaymentsTestCase):
    def test_max_amount_denies_oversized_payment(self):
        self.db.add(SpendingPolicy(name="cap", network="polygon", token="USDC",
                                    max_amount_raw="1000000", active=True))
        self.db.commit()
        result = spending_policy.evaluate(self.db, wallet_id=self.wallet.id, network="polygon", token="USDC",
                                           counterparty_id=None, destination_address=ADDR_A, amount_raw=2_000_000)
        self.assertFalse(result.allowed)
        self.assertTrue(any("caps a single payment" in r for r in result.denial_reasons))

    def test_cumulative_period_limit_denies_after_prior_spend(self):
        self.db.add(SpendingPolicy(name="daily-cap", network="polygon", token="USDC",
                                    period="day", period_limit_raw="1000000", active=True))
        batch = self.make_batch()
        item = self.add_item(batch, 0, ADDR_A, amount="0.9", decimals=6)
        item.status = "confirmed"
        self.db.add(Transaction(
            wallet_id=self.wallet.id, chain="evm", network="polygon", tx_hash="0xprior",
            from_address=self.wallet.address, to_address=ADDR_A, amount=0.9,
            amount_raw="900000", decimals=6, token="USDC", status="confirmed", direction="outgoing",
        ))
        self.db.commit()
        result = spending_policy.evaluate(self.db, wallet_id=self.wallet.id, network="polygon", token="USDC",
                                           counterparty_id=None, destination_address=ADDR_B, amount_raw=200_000)
        self.assertFalse(result.allowed)
        self.assertTrue(any("cumulative spend" in r for r in result.denial_reasons))

    def test_non_matching_policy_does_not_apply(self):
        self.db.add(SpendingPolicy(name="other-network", network="ethereum", token="USDC",
                                    max_amount_raw="1", active=True))
        self.db.commit()
        result = spending_policy.evaluate(self.db, wallet_id=self.wallet.id, network="polygon", token="USDC",
                                           counterparty_id=None, destination_address=ADDR_A, amount_raw=5_000_000)
        self.assertTrue(result.allowed)


class ExecutionTests(BusinessPaymentsTestCase):
    def _approved_batch(self, n_items=2):
        batch = self.make_batch()
        for i in range(n_items):
            self.add_item(batch, i, [ADDR_A, ADDR_B, ADDR_C][i])
        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            batch_engine.approve_batch(self.db, batch, "owner")
        return batch

    def test_partial_failure_does_not_abort_batch(self):
        batch = self._approved_batch(2)
        prepared = {"tx_hash": "0xhash1", "raw_transaction": "01", "nonce": 1}
        with patch("app.chains.evm.prepare_erc20_transfer_raw", side_effect=[prepared, ValueError("insufficient token balance")]), \
             patch("app.chains.evm.broadcast_raw_transaction", return_value="0xhash1"):
            summary = batch_engine.execute_batch(self.db, batch, self.wallet, "fake-key")
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(batch.status, "completed")
        items = self.db.query(PaymentBatchItem).filter_by(batch_id=batch.id).order_by(PaymentBatchItem.row_index).all()
        self.assertEqual(items[0].status, "submitted")
        self.assertEqual(items[1].status, "failed")
        self.assertIsNotNone(items[1].failure_reason)

    def test_restart_recovery_does_not_resend_already_submitted_items(self):
        batch = self._approved_batch(2)
        items = self.db.query(PaymentBatchItem).filter_by(batch_id=batch.id).order_by(PaymentBatchItem.row_index).all()
        # Simulate item 0 already broadcast in a prior (crashed) run.
        tx = Transaction(wallet_id=self.wallet.id, chain="evm", network="polygon", tx_hash="0xalready",
                          from_address=self.wallet.address, to_address=items[0].recipient_address,
                          amount=1.5, amount_raw=items[0].amount_raw, decimals=6, token="USDC", status="submitted")
        self.db.add(tx)
        self.db.flush()
        items[0].status = "submitted"
        items[0].tx_hash = "0xalready"
        items[0].transaction_id = tx.id
        self.db.commit()

        prepared = {"tx_hash": "0xhash2", "raw_transaction": "02", "nonce": 2}
        with patch("app.chains.evm.prepare_erc20_transfer_raw", return_value=prepared) as sender, \
             patch("app.chains.evm.broadcast_raw_transaction", return_value="0xhash2"):
            summary = batch_engine.execute_batch(self.db, batch, self.wallet, "fake-key")
        sender.assert_called_once()  # only item 1 was actually sent
        self.assertEqual(summary["submitted"], 2)
        tx_count = self.db.query(Transaction).filter_by(wallet_id=self.wallet.id).count()
        self.assertEqual(tx_count, 2)  # one pre-existing + exactly one new

    def test_cannot_execute_a_batch_that_is_not_approved(self):
        batch = self.make_batch()
        with self.assertRaises(batch_engine.BatchExecutionError):
            batch_engine.execute_batch(self.db, batch, self.wallet, "fake-key")

    def test_execution_lock_prevents_concurrent_claim(self):
        from sqlalchemy import update
        batch = self._approved_batch(1)
        first = self.db.execute(
            update(PaymentBatch).where(PaymentBatch.id == batch.id, PaymentBatch.execution_lock_token.is_(None),
                                        PaymentBatch.status == "approved")
            .values(execution_lock_token="lock-1", status="executing")
        )
        self.db.commit()
        self.assertEqual(first.rowcount, 1)
        second = self.db.execute(
            update(PaymentBatch).where(PaymentBatch.id == batch.id, PaymentBatch.execution_lock_token.is_(None),
                                        PaymentBatch.status == "approved")
            .values(execution_lock_token="lock-2", status="executing")
        )
        self.db.commit()
        self.assertEqual(second.rowcount, 0)  # a concurrent claim is rejected

    def test_double_click_execute_is_rejected(self):
        batch = self._approved_batch(1)
        batch.status = "executing"
        batch.execution_lock_token = "in-progress"
        self.db.commit()
        with self.assertRaises(batch_engine.BatchExecutionError):
            batch_engine.execute_batch(self.db, batch, self.wallet, "fake-key")


class ScheduleTests(BusinessPaymentsTestCase):
    def make_schedule(self, start, next_run=None, end=None):
        row = Schedule(kind="recurring_payment", wallet_id=self.wallet.id, network="polygon", token="USDC",
                       recipient_address=ADDR_A, amount_raw="1000000", rrule="FREQ=DAILY;INTERVAL=1",
                       start_date=start, next_run_at=next_run or start, end_date=end, active=True)
        self.db.add(row)
        self.db.commit()
        return row

    def test_calling_materialize_twice_does_not_duplicate(self):
        start = datetime(2026, 1, 1)
        schedule = self.make_schedule(start)
        created1 = schedules_service.materialize_due_schedules(self.db, now=start)
        created2 = schedules_service.materialize_due_schedules(self.db, now=start)
        self.assertEqual(created1, 1)
        self.assertEqual(created2, 0)
        self.assertEqual(self.db.query(PaymentBatch).count(), 1)

    def test_unique_constraint_is_the_final_idempotency_guard(self):
        start = datetime(2026, 1, 1)
        schedule = self.make_schedule(start)
        key = f"{schedule.id}:{start.isoformat()}"
        self.db.add(ScheduleRun(schedule_id=schedule.id, occurrence_key=key, occurrence_date=start, status="materialized"))
        self.db.commit()
        with self.assertRaises(IntegrityError):
            self.db.add(ScheduleRun(schedule_id=schedule.id, occurrence_key=key, occurrence_date=start, status="materialized"))
            self.db.commit()
        self.db.rollback()

    def test_missed_periods_materialize_only_the_latest_occurrence(self):
        start = datetime(2026, 1, 1)
        now = datetime(2026, 1, 6)  # five days late: 6 occurrences (day 1..6) are due
        schedule = self.make_schedule(start, next_run=start)
        created = schedules_service.materialize_due_schedules(self.db, now=now)
        self.assertEqual(created, 1)
        self.assertEqual(self.db.query(PaymentBatch).count(), 1)
        runs = self.db.query(ScheduleRun).filter_by(schedule_id=schedule.id).all()
        skipped = [r for r in runs if r.status == "skipped"]
        materialized = [r for r in runs if r.status == "materialized"]
        self.assertEqual(len(materialized), 1)
        self.assertEqual(materialized[0].occurrence_date, now)
        self.assertTrue(len(skipped) >= 4)


class PayrollTests(BusinessPaymentsTestCase):
    def test_payroll_run_reuses_the_batch_engine_pipeline(self):
        entry = AddressBook(nickname="jane.sara", address=ADDR_A, chain="evm", type="employee", display_name="Jane")
        self.db.add(entry)
        self.db.commit()

        profile_row = payroll.create_payroll_person(
            payroll.PayrollPersonBody(
                counterparty_id=entry.id, wallet_id=self.wallet.id, network="polygon", token="USDC",
                amount="500", rrule="FREQ=MONTHLY;INTERVAL=1", start_date=datetime(2026, 1, 1),
            ),
            self.db,
        )
        self.assertIsNotNone(profile_row["schedule_id"])

        run_result = payroll.create_payroll_run(
            payroll.PayrollRunBody(wallet_id=self.wallet.id, network="polygon", token="USDC", period_label="2026-01"),
            self.db,
        )
        self.assertEqual(run_result["items"], 1)
        batch = self.db.query(PaymentBatch).filter_by(id=run_result["batch_id"]).first()
        self.assertEqual(batch.kind, "payroll")

        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            batch_engine.approve_batch(self.db, batch, "owner")
        prepared = {"tx_hash": "0xpayroll1", "raw_transaction": "03", "nonce": 3}
        with patch("app.chains.evm.prepare_erc20_transfer_raw", return_value=prepared), \
             patch("app.chains.evm.broadcast_raw_transaction", return_value="0xpayroll1"):
            summary = batch_engine.execute_batch(self.db, batch, self.wallet, "fake-key")
        self.assertEqual(summary["submitted"], 1)
        tx = self.db.query(Transaction).filter_by(category="payroll").first()
        self.assertIsNotNone(tx)

        # Running the same period again must not pay the person twice.
        second_run = payroll.create_payroll_run(
            payroll.PayrollRunBody(wallet_id=self.wallet.id, network="polygon", token="USDC", period_label="2026-01"),
            self.db,
        )
        self.assertEqual(second_run["items"], 0)
        self.assertEqual(len(second_run["skipped"]), 1)


class CsvImportTests(unittest.TestCase):
    """Airdrop CSV import goes through FastAPI's own multipart parsing, so
    this one class uses a real (in-process) TestClient rather than calling
    the endpoint function directly."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.session_auth import require_session
        from app.db.session import get_db

        from sqlalchemy.pool import StaticPool
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

        app = FastAPI()
        app.include_router(payment_batches.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)
        self.net_patch = patch("app.core.assets.network_enabled", return_value=True)
        self.net_patch.start()
        self.addCleanup(self.net_patch.stop)

    def tearDown(self):
        self.db.close()

    def _upload(self, content: bytes, kind="payment", **overrides):
        data = {"kind": kind, "wallet_id": str(self.wallet.id), "network": "polygon", "token": "USDC", **overrides}
        return self.client.post("/api/payment-batches/import", data=data,
                                 files={"file": ("rows.csv", content, "text/csv")})

    def _valid(self):
        return patch.object(batch_engine, "validate_batch",
                            return_value={"ok": True, "item_errors": {}, "batch_errors": []})

    def _batch_count(self):
        return self.db.query(PaymentBatch).count()

    def test_valid_csv_creates_a_validated_draft_ready_to_send(self):
        content = f"recipient_address,amount\n{ADDR_A},1.5\n{ADDR_B},2.5\n".encode()
        with self._valid():
            resp = self._upload(content)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual((data["batch"]["status"], data["batch"]["item_count"], data["batch"]["total_amount"]),
                         ("draft", 2, "4"))
        self.assertEqual(self._batch_count(), 1)

    def test_duplicate_recipient_rejects_the_whole_file(self):
        content = f"recipient_address,amount\n{ADDR_A},1\n{ADDR_A},2\n".encode()
        with self._valid():
            data = self._upload(content).json()
        self.assertFalse(data["ok"])
        self.assertEqual(len(data["row_errors"]), 1)
        self.assertIn("duplicate", data["row_errors"][0]["error"])
        self.assertEqual(self._batch_count(), 0)

    def test_malformed_row_rejects_the_whole_file_and_creates_nothing(self):
        content = f"recipient_address,amount\nnot-an-address,1\n{ADDR_C},3\n".encode()
        with self._valid():
            data = self._upload(content).json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["row_errors"][0]["row"], 2)
        self.assertIn("invalid recipient_address", data["row_errors"][0]["error"])
        self.assertEqual(self._batch_count(), 0)
        self.assertEqual(self.db.query(PaymentBatchItem).count(), 0)

    def test_spending_policy_block_is_reported_per_row(self):
        self.db.add(SpendingPolicy(name="cap", network="polygon", token="USDC", max_amount_raw="1000000", active=True))
        self.db.commit()
        content = f"recipient_address,amount\n{ADDR_A},0.5\n{ADDR_B},5\n".encode()
        with self._valid():
            data = self._upload(content).json()
        self.assertFalse(data["ok"])
        self.assertEqual([e["row"] for e in data["row_errors"]], [3])
        self.assertIn("spending policy", data["row_errors"][0]["error"])
        self.assertEqual(self._batch_count(), 0)

    def test_batch_level_failure_such_as_low_balance_creates_nothing(self):
        content = f"recipient_address,amount\n{ADDR_A},1\n".encode()
        failed = {"ok": False, "item_errors": {}, "batch_errors": ["insufficient USDC: 0 available"]}
        with patch.object(batch_engine, "validate_batch", return_value=failed):
            data = self._upload(content).json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["batch_errors"], ["insufficient USDC: 0 available"])
        self.assertEqual(self._batch_count(), 0)
        self.assertEqual(self.db.query(PaymentBatchItem).count(), 0)

    def test_empty_csv_is_rejected(self):
        with self._valid():
            data = self._upload(b"recipient_address,amount\n").json()
        self.assertFalse(data["ok"])
        self.assertEqual(self._batch_count(), 0)

    def test_missing_required_column_is_rejected(self):
        resp = self._upload(b"wallet,amount\n0x1,1\n")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._batch_count(), 0)

    def test_oversized_file_is_rejected(self):
        row = f"{ADDR_A},1\n".encode()
        content = b"recipient_address,amount\n" + row * ((2 * 1024 * 1024 // len(row)) + 10)
        self.assertEqual(self._upload(content).status_code, 400)

    def test_unknown_kind_is_rejected(self):
        self.assertEqual(self._upload(f"recipient_address,amount\n{ADDR_A},1\n".encode(), kind="payroll").status_code, 400)


class DeleteBatchTests(BusinessPaymentsTestCase):
    def _delete(self, batch_id):
        return payment_batches.delete_batch(batch_id, self.db)

    def _batch(self, status="draft", kind="payment", item_status="draft", tx_hash=None):
        batch = self.make_batch(kind=kind)
        batch.status = status
        item = self.add_item(batch, 0, ADDR_A)
        item.status = item_status
        item.tx_hash = tx_hash
        self.db.commit()
        return batch

    def test_unused_draft_and_cancelled_batches_can_be_deleted(self):
        for status in ("draft", "cancelled"):
            with self.subTest(status=status):
                batch = self._batch(status)
                self.assertEqual(self._delete(batch.id), {"deleted": batch.id})
                self.assertIsNone(self.db.get(PaymentBatch, batch.id))
                self.assertEqual(self.db.query(PaymentBatchItem).filter_by(batch_id=batch.id).count(), 0)

    def test_approved_executing_and_completed_batches_are_kept(self):
        from fastapi import HTTPException
        for status in ("approved", "executing", "completed"):
            with self.subTest(status=status):
                batch = self._batch(status)
                with self.assertRaises(HTTPException) as ctx:
                    self._delete(batch.id)
                self.assertEqual(ctx.exception.status_code, 409)
                self.assertIsNotNone(self.db.get(PaymentBatch, batch.id))

    def test_a_batch_that_already_broadcast_is_kept_even_if_cancelled(self):
        from fastapi import HTTPException
        batch = self._batch("cancelled", item_status="submitted", tx_hash="0xabc")
        with self.assertRaises(HTTPException) as ctx:
            self._delete(batch.id)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_schedule_and_payroll_batches_cannot_be_deleted(self):
        from fastapi import HTTPException
        for kind in ("recurring", "payroll"):
            with self.subTest(kind=kind):
                batch = self._batch("draft", kind=kind)
                with self.assertRaises(HTTPException) as ctx:
                    self._delete(batch.id)
                self.assertEqual(ctx.exception.status_code, 409)
                self.assertIsNotNone(self.db.get(PaymentBatch, batch.id))

    def test_list_filters_by_several_kinds(self):
        for kind in ("payment", "airdrop", "recurring", "payroll"):
            self._batch(kind=kind)
        kinds = lambda spec: sorted(b["kind"] for b in payment_batches.list_batches(kind=spec, db=self.db)["batches"])
        self.assertEqual(kinds("payment,airdrop"), ["airdrop", "payment"])
        self.assertEqual(kinds("recurring"), ["recurring"])


class SendBatchTests(BusinessPaymentsTestCase):
    def _send(self, batch_id, passphrase="pw"):
        return payment_batches.send_batch(batch_id, payment_batches.ExecuteBody(passphrase=passphrase), self.db)

    def _draft(self):
        batch = self.make_batch()
        self.add_item(batch, 0, ADDR_A)
        self.db.commit()
        return batch

    def test_wrong_passphrase_leaves_a_draft_untouched(self):
        from fastapi import HTTPException
        batch = self._draft()
        with patch("app.tools.wallet.lock.confirm_passphrase", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                self._send(batch.id, "wrong")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(self.db.get(PaymentBatch, batch.id).status, "draft")

    def test_send_approves_a_draft_then_executes_it(self):
        batch = self._draft()
        calls = []
        with patch("app.tools.wallet.lock.confirm_passphrase", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="key"), \
             patch.object(batch_engine, "approve_batch", side_effect=lambda *a, **k: calls.append("approve")), \
             patch.object(batch_engine, "execute_batch", side_effect=lambda *a, **k: calls.append("execute") or {"submitted": 1, "failed": 0}):
            result = self._send(batch.id)
        self.assertEqual(calls, ["approve", "execute"])
        self.assertEqual(result["submitted"], 1)

    def test_validation_failure_blocks_send_before_anything_is_signed(self):
        from fastapi import HTTPException
        batch = self._draft()
        bad = batch_engine.BatchValidationError("batch has unresolved validation errors", {"ok": False, "item_errors": {}, "batch_errors": ["x"]})
        with patch("app.tools.wallet.lock.confirm_passphrase", return_value=True), \
             patch.object(batch_engine, "approve_batch", side_effect=bad), \
             patch.object(batch_engine, "execute_batch") as execute:
            with self.assertRaises(HTTPException) as ctx:
                self._send(batch.id)
        self.assertEqual(ctx.exception.status_code, 400)
        execute.assert_not_called()

    def test_cancelled_batch_cannot_be_sent(self):
        from fastapi import HTTPException
        batch = self._draft()
        batch.status = "cancelled"
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            self._send(batch.id)
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
