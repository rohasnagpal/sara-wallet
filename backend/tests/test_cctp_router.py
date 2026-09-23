"""Router-level tests for /api/cctp: passphrase gating, the two-transaction
flow (burn always attempted, mint attempted best-effort), correct ledger
directions for both legs, and that a stalled attestation leaves a
transfer safely "burned" rather than erroring or losing track of it."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, CctpTransfer, Transaction, Wallet
from app.routers import cctp as cctp_router
from app.tools.trading import cctp


class CctpRouterTests(unittest.TestCase):
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
        app.include_router(cctp_router.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def _unlocked(self):
        return patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
               patch("app.tools.wallet.lock.confirm_passphrase", return_value=True), \
               patch("app.tools.wallet.encrypt.decrypt_key", return_value="0xkey")

    def test_transfer_403_when_locked(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=False):
            resp = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "x",
            })
        self.assertEqual(resp.status_code, 423)

    def test_transfer_401s_on_wrong_passphrase_and_never_signs(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.lock.confirm_passphrase", return_value=False), \
             patch.object(cctp, "execute_burn") as burn:
            resp = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "wrong",
            })
        self.assertEqual(resp.status_code, 401)
        burn.assert_not_called()

    def test_full_success_burns_mints_and_records_both_ledger_legs(self):
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", return_value="0xburnhash") as burn, \
             patch.object(cctp, "wait_for_attestation",
                          return_value=cctp.Attestation(message="0xaa", attestation="0xbb")), \
             patch.object(cctp, "execute_mint", return_value="0xminthash") as mint:
            resp = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["burn_tx_hash"], "0xburnhash")
        self.assertEqual(data["mint_tx_hash"], "0xminthash")

        burn.assert_called_once_with("0xkey", "base", "arbitrum", 10_000_000, self.wallet.address, fast=True)
        mint.assert_called_once()

        burn_tx = self.db.query(Transaction).filter_by(tx_hash="0xburnhash").first()
        mint_tx = self.db.query(Transaction).filter_by(tx_hash="0xminthash").first()
        self.assertEqual(burn_tx.direction, "outgoing")
        self.assertEqual(burn_tx.network, "base")
        self.assertEqual(mint_tx.direction, "incoming")
        self.assertEqual(mint_tx.network, "arbitrum")

    def test_stalled_attestation_leaves_the_transfer_burned_not_failed(self):
        """The burn already succeeded - a slow/unavailable attestation must
        not be treated as an error. The transfer stays completable later."""
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", return_value="0xburnhash"), \
             patch.object(cctp, "wait_for_attestation", return_value=None), \
             patch.object(cctp, "execute_mint") as mint:
            resp = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "burned")
        self.assertIsNone(data["mint_tx_hash"])
        mint.assert_not_called()
        row = self.db.query(CctpTransfer).filter_by(burn_tx_hash="0xburnhash").first()
        self.assertEqual(row.status, "burned")

    def test_a_failed_burn_never_creates_a_transfer_row(self):
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", side_effect=cctp.CctpError("insufficient balance")):
            resp = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.db.query(CctpTransfer).count(), 0)

    def test_complete_finishes_a_previously_burned_transfer(self):
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", return_value="0xburnhash"), \
             patch.object(cctp, "wait_for_attestation", return_value=None):
            create = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        transfer_id = create.json()["id"]

        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "fetch_attestation",
                          return_value=cctp.Attestation(message="0xaa", attestation="0xbb")), \
             patch.object(cctp, "execute_mint", return_value="0xminthash"):
            resp = self.client.post(f"/api/cctp/transfers/{transfer_id}/complete", json={"passphrase": "correct"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["mint_tx_hash"], "0xminthash")

    def test_complete_409s_when_attestation_still_not_ready(self):
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", return_value="0xburnhash"), \
             patch.object(cctp, "wait_for_attestation", return_value=None):
            create = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        transfer_id = create.json()["id"]

        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, patch.object(cctp, "fetch_attestation", return_value=None):
            resp = self.client.post(f"/api/cctp/transfers/{transfer_id}/complete", json={"passphrase": "correct"})
        self.assertEqual(resp.status_code, 409)

    def test_complete_on_an_already_complete_transfer_is_a_no_op(self):
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", return_value="0xburnhash"), \
             patch.object(cctp, "wait_for_attestation",
                          return_value=cctp.Attestation(message="0xaa", attestation="0xbb")), \
             patch.object(cctp, "execute_mint", return_value="0xminthash") as mint:
            create = self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        transfer_id = create.json()["id"]
        mint.reset_mock()

        resp = self.client.post(f"/api/cctp/transfers/{transfer_id}/complete", json={"passphrase": "correct"})
        self.assertEqual(resp.status_code, 200)
        mint.assert_not_called()

    def test_list_transfers(self):
        u1, u2, u3 = self._unlocked()
        with u1, u2, u3, \
             patch.object(cctp, "execute_burn", return_value="0xburnhash"), \
             patch.object(cctp, "wait_for_attestation", return_value=None):
            self.client.post("/api/cctp/transfer", json={
                "wallet_id": self.wallet.id, "source_network": "base", "destination_network": "arbitrum",
                "amount": "10", "passphrase": "correct",
            })
        resp = self.client.get("/api/cctp/transfers")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["transfers"]), 1)


if __name__ == "__main__":
    unittest.main()
