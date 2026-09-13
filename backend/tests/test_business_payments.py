from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core import spending_policy
from app.db.models import (
    Base, Counterparty, PaymentBatch, PaymentBatchItem, PayrollProfile,
    Schedule, ScheduleRun, SpendingPolicy, Transaction, Wallet,
)
from app.routers import counterparties, payment_batches, payroll, schedules
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


class CounterpartyTests(BusinessPaymentsTestCase):
    def test_create_list_and_deactivate_counterparty(self):
        row = counterparties.create_counterparty(
            counterparties.CounterpartyBody(display_name="Acme Vendor", type="vendor", addresses={"polygon": ADDR_A}),
            self.db,
        )
        self.assertEqual(row["display_name"], "Acme Vendor")
        self.assertEqual(row["addresses"], {"polygon": ADDR_A})

        listed = counterparties.list_counterparties(None, None, self.db)
        self.assertEqual(len(listed["counterparties"]), 1)

        deactivated = counterparties.deactivate_counterparty(row["id"], self.db)
        self.assertFalse(deactivated["active"])
        still_listed = counterparties.list_counterparties(None, True, self.db)
        self.assertEqual(still_listed["counterparties"], [])

    def test_invalid_address_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            counterparties.create_counterparty(
                counterparties.CounterpartyBody(display_name="Bad", addresses={"polygon": "not-an-address"}),
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

    def test_self_approval_rejected_when_dual_control_required(self):
        self.db.add(SpendingPolicy(name="dual-control", network="polygon", token="USDC",
                                    require_dual_control=True, active=True))
        self.db.commit()
        batch = self._valid_batch(created_by="owner")
        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            with self.assertRaises(batch_engine.SelfApprovalError):
                batch_engine.approve_batch(self.db, batch, "owner")
        self.assertEqual(batch.status, "draft")
        denial = self.db.query(batch_engine.BatchApproval).filter_by(batch_id=batch.id, action="denied").first()
        self.assertIsNotNone(denial)

    def test_different_actor_can_approve_under_dual_control(self):
        self.db.add(SpendingPolicy(name="dual-control", network="polygon", token="USDC",
                                    require_dual_control=True, active=True))
        self.db.commit()
        batch = self._valid_batch(created_by="owner")
        b1, b2 = self.stub_balance_ok()
        with b1, b2:
            approval = batch_engine.approve_batch(self.db, batch, "bookkeeper")
        self.assertEqual(approval.action, "approved")
        self.assertEqual(batch.status, "approved")
        self.assertEqual(batch.approved_by, "bookkeeper")

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
        counterparty = Counterparty(display_name="Jane", type="employee", addresses='{"polygon": "%s"}' % ADDR_A)
        self.db.add(counterparty)
        self.db.commit()

        profile_row = payroll.create_payroll_person(
            payroll.PayrollPersonBody(
                counterparty_id=counterparty.id, wallet_id=self.wallet.id, network="polygon", token="USDC",
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

    def _create_batch(self, kind="airdrop"):
        resp = self.client.post("/api/payment-batches", json={
            "kind": kind, "wallet_id": self.wallet.id, "network": "polygon", "token": "USDC",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["id"]

    def _upload(self, batch_id, content: bytes):
        return self.client.post(f"/api/payment-batches/{batch_id}/items/import-csv",
                                 files={"file": ("rows.csv", content, "text/csv")})

    def test_valid_csv_imports_every_row(self):
        batch_id = self._create_batch()
        content = f"recipient_address,amount\n{ADDR_A},1.5\n{ADDR_B},2.5\n".encode()
        resp = self._upload(batch_id, content)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["imported"], 2)
        self.assertEqual(data["errors"], [])

    def test_duplicate_recipient_is_a_row_error_not_a_hard_failure(self):
        batch_id = self._create_batch()
        content = f"recipient_address,amount\n{ADDR_A},1\n{ADDR_A},2\n".encode()
        resp = self._upload(batch_id, content)
        data = resp.json()
        self.assertEqual(data["imported"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("duplicate", data["errors"][0]["error"])

    def test_malformed_row_does_not_abort_the_whole_import(self):
        batch_id = self._create_batch()
        content = f"recipient_address,amount\nnot-an-address,1\n{ADDR_C},3\n".encode()
        resp = self._upload(batch_id, content)
        data = resp.json()
        self.assertEqual(data["imported"], 1)
        self.assertEqual(len(data["errors"]), 1)
        self.assertIn("invalid recipient_address", data["errors"][0]["error"])

    def test_missing_required_column_is_rejected(self):
        batch_id = self._create_batch()
        content = b"wallet,amount\n0x1,1\n"
        resp = self._upload(batch_id, content)
        self.assertEqual(resp.status_code, 400)

    def test_oversized_file_is_rejected(self):
        batch_id = self._create_batch()
        row = f"{ADDR_A},1\n".encode()
        content = b"recipient_address,amount\n" + row * ((2 * 1024 * 1024 // len(row)) + 10)
        resp = self._upload(batch_id, content)
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
