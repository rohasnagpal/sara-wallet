"""Router-level tests for /api/onramp: the CDP key is validated before
being persisted, stored encrypted, never returned back to the client, and
creating a session never signs or moves any wallet funds - only unlock
(to decrypt the stored secret) is required, no passphrase confirmation."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, OnrampSettings, Wallet
from app.routers import onramp as onramp_router
from app.tools.payments import cdp_onramp


class OnrampRouterTests(unittest.TestCase):
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
        self.wallet = Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

        app = FastAPI()
        app.include_router(onramp_router.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def test_get_settings_when_unconfigured(self):
        resp = self.client.get("/api/onramp/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])

    def test_save_settings_rejects_a_bad_key_before_persisting_anything(self):
        with patch.object(cdp_onramp, "build_jwt", side_effect=cdp_onramp.OnrampError("bad key")):
            resp = self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "not-a-real-key",
            })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.db.query(OnrampSettings).count(), 0)

    def test_save_settings_encrypts_the_secret_and_never_returns_it(self):
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB") as enc:
            resp = self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertNotIn("topsecret", resp.text)
        enc.assert_called_once_with("topsecret")
        row = self.db.query(OnrampSettings).filter_by(id=1).first()
        self.assertEqual(row.encrypted_cdp_secret, "ENCRYPTED_BLOB")
        self.assertNotIn("topsecret", row.encrypted_cdp_secret)

        get_resp = self.client.get("/api/onramp/settings")
        self.assertTrue(get_resp.json()["configured"])
        self.assertEqual(get_resp.json()["cdp_key_id"], "orgs/x/apiKeys/y")
        self.assertNotIn("ENCRYPTED_BLOB", get_resp.text)

    def test_save_settings_423s_when_locked_and_persists_nothing(self):
        from app.tools.wallet.lock import WalletLockedError
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", side_effect=WalletLockedError("locked")):
            resp = self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        self.assertEqual(resp.status_code, 423)
        self.assertEqual(self.db.query(OnrampSettings).count(), 0)

    def test_delete_settings(self):
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB"):
            self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        resp = self.client.delete("/api/onramp/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["configured"])
        self.assertEqual(self.db.query(OnrampSettings).count(), 0)

    def test_create_session_requires_settings_configured_first(self):
        resp = self.client.post("/api/onramp/session", json={"wallet_id": self.wallet.id, "network": "base"})
        self.assertEqual(resp.status_code, 400)

    def test_create_session_404s_for_unknown_wallet(self):
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB"):
            self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        resp = self.client.post("/api/onramp/session", json={"wallet_id": 999999, "network": "base"})
        self.assertEqual(resp.status_code, 404)

    def test_create_session_423s_when_locked(self):
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB"):
            self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        with patch("app.tools.wallet.lock.is_unlocked", return_value=False):
            resp = self.client.post("/api/onramp/session", json={"wallet_id": self.wallet.id, "network": "base"})
        self.assertEqual(resp.status_code, 423)

    def test_create_session_needs_no_passphrase_only_unlock(self):
        """Unlike a send/Aave/CCTP transaction, this never signs anything
        - only reading the stored secret is gated (on unlock), there's no
        separate confirm_passphrase step, and the request body never even
        has a passphrase field."""
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB"):
            self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="topsecret") as dec, \
             patch.object(cdp_onramp, "create_session",
                          return_value={"onramp_url": "https://pay.coinbase.com/buy?x", "quote": None}) as create:
            resp = self.client.post("/api/onramp/session", json={
                "wallet_id": self.wallet.id, "network": "base", "amount_usd": "100",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["onramp_url"], "https://pay.coinbase.com/buy?x")
        dec.assert_called_once_with("ENCRYPTED_BLOB")
        create.assert_called_once_with(
            "orgs/x/apiKeys/y", "topsecret", destination_address=self.wallet.address,
            network="base", payment_amount_usd="100", country=None,
        )

    def test_a_coinbase_error_is_surfaced_as_a_clean_400(self):
        with patch.object(cdp_onramp, "build_jwt", return_value="fake.jwt.token"), \
             patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB"):
            self.client.post("/api/onramp/settings", json={
                "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="topsecret"), \
             patch.object(cdp_onramp, "create_session", side_effect=cdp_onramp.OnrampError("unsupported network")):
            resp = self.client.post("/api/onramp/session", json={"wallet_id": self.wallet.id, "network": "nowhere"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unsupported network", resp.text)


if __name__ == "__main__":
    unittest.main()
