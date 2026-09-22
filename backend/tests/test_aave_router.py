"""Router-level tests for /api/aave: passphrase gating, 423 when locked,
correct ledger direction (a withdrawal must show as money coming IN, not
out), and the audit trail."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Transaction, Wallet
from app.routers import aave as aave_router
from app.tools.lending import aave


class AaveRouterTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.session_auth import require_session
        from app.db.session import get_db

        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

        app = FastAPI()
        app.include_router(aave_router.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def test_position_reports_balance_and_apy(self):
        with patch.object(aave, "get_position", return_value=123.45), \
             patch.object(aave, "get_supply_apy", return_value=4.2):
            resp = self.client.get(f"/api/aave/position?wallet_id={self.wallet.id}&network=base")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["usdc_supplied"], 123.45)
        self.assertEqual(data["supply_apy_pct"], 4.2)

    def test_position_400s_for_unsupported_network(self):
        resp = self.client.get(f"/api/aave/position?wallet_id={self.wallet.id}&network=solana")
        self.assertEqual(resp.status_code, 400)

    def test_supply_423s_when_wallet_is_locked(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=False):
            resp = self.client.post("/api/aave/supply", json={
                "wallet_id": self.wallet.id, "network": "base", "amount": "10", "passphrase": "x",
            })
        self.assertEqual(resp.status_code, 423)

    def test_supply_401s_on_wrong_passphrase_and_never_signs(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.lock.confirm_passphrase", return_value=False), \
             patch.object(aave, "execute_supply") as exec_supply:
            resp = self.client.post("/api/aave/supply", json={
                "wallet_id": self.wallet.id, "network": "base", "amount": "10", "passphrase": "wrong",
            })
        self.assertEqual(resp.status_code, 401)
        exec_supply.assert_not_called()

    def test_supply_rejects_non_numeric_amount_before_touching_the_wallet_key(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key") as dec:
            resp = self.client.post("/api/aave/supply", json={
                "wallet_id": self.wallet.id, "network": "base", "amount": "lots", "passphrase": "x",
            })
        self.assertEqual(resp.status_code, 400)
        dec.assert_not_called()

    def test_successful_supply_records_an_outgoing_ledger_entry(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.lock.confirm_passphrase", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="0xkey"), \
             patch.object(aave, "execute_supply", return_value="0xsupplyhash") as exec_supply:
            resp = self.client.post("/api/aave/supply", json={
                "wallet_id": self.wallet.id, "network": "base", "amount": "100", "passphrase": "correct",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        exec_supply.assert_called_once_with("0xkey", "base", 100_000_000)
        tx = self.db.query(Transaction).filter_by(tx_hash="0xsupplyhash").first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.direction, "outgoing")
        self.assertEqual(tx.category, "aave_supply")
        self.assertEqual(tx.from_address, self.wallet.address)
        self.assertEqual(tx.to_address, aave.POOL_ADDRESSES["base"])

    def test_withdraw_all_passes_none_amount_and_records_incoming(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.lock.confirm_passphrase", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="0xkey"), \
             patch.object(aave, "execute_withdraw", return_value="0xwithdrawhash") as exec_withdraw:
            resp = self.client.post("/api/aave/withdraw", json={
                "wallet_id": self.wallet.id, "network": "polygon", "passphrase": "correct",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        exec_withdraw.assert_called_once_with("0xkey", "polygon", None)
        tx = self.db.query(Transaction).filter_by(tx_hash="0xwithdrawhash").first()
        self.assertIsNotNone(tx)
        # The critical fix: a withdrawal must show as money coming IN, not
        # an outflow, and its counterparty must be the Aave pool, not the
        # user's own wallet address.
        self.assertEqual(tx.direction, "incoming")
        self.assertEqual(tx.from_address, aave.POOL_ADDRESSES["polygon"])
        self.assertEqual(tx.to_address, self.wallet.address)
        self.assertEqual(tx.counterparty, aave.POOL_ADDRESSES["polygon"])

    def test_partial_withdraw_passes_exact_amount(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.lock.confirm_passphrase", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="0xkey"), \
             patch.object(aave, "execute_withdraw", return_value="0xh") as exec_withdraw:
            resp = self.client.post("/api/aave/withdraw", json={
                "wallet_id": self.wallet.id, "network": "polygon", "amount": "50", "passphrase": "correct",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        exec_withdraw.assert_called_once_with("0xkey", "polygon", 50_000_000)

    def test_a_normal_outgoing_send_still_defaults_to_outgoing_direction(self):
        """Regression guard for the shared _record_submitted_transaction
        helper: adding an explicit `direction` param for Aave withdrawals
        must not change the default behaviour every other caller relies on."""
        from app.routers.chat import _record_submitted_transaction
        row = _record_submitted_transaction(
            self.db, wallet_id=self.wallet.id, network="polygon", tx_hash="0xsend",
            from_address=self.wallet.address, to_address="0x" + "99" * 20,
            amount=1.0, amount_raw=1_000_000, decimals=6, token="USDC", category="send",
        )
        self.assertEqual(row.direction, "outgoing")
        self.assertEqual(row.counterparty, "0x" + "99" * 20)


if __name__ == "__main__":
    unittest.main()
