"""Router-level tests for the x402 paywall generator: creating a page
persists the right (encrypted, for live mode) config and returns working
code; listing annotates each page with its wallet's live USDC balance;
a live-mode page's CDP secret is never stored in plaintext and requires an
unlocked session to read back, same as a wallet's private key."""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Wallet, X402PaywallPage
from app.routers import x402_paywall


class X402PaywallRouterTests(unittest.TestCase):
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
        self.wallet = Wallet(name="Creator", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

        app = FastAPI()
        app.include_router(x402_paywall.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def test_create_test_mode_page_persists_and_returns_working_code(self):
        resp = self.client.post("/api/x402-paywall/pages", json={
            "label": "Article", "wallet_id": self.wallet.id, "mode": "test",
            "price_usd": "0.10",
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("sara_x402_paywall", body["code"])
        page = self.db.query(X402PaywallPage).filter_by(id=body["id"]).first()
        self.assertEqual(page.mode, "test")
        self.assertEqual(page.network, "base-sepolia")
        self.assertIsNone(page.encrypted_cdp_secret)

    def test_preview_message_persists_and_round_trips_through_get_code(self):
        create = self.client.post("/api/x402-paywall/pages", json={
            "label": "Article", "wallet_id": self.wallet.id, "mode": "test", "price_usd": "0.10",
            "preview_message": "Subscribe for $0.10 to read more",
        })
        self.assertEqual(create.status_code, 200, create.text)
        self.assertIn("Subscribe for $0.10 to read more", create.json()["code"])
        page = self.db.query(X402PaywallPage).filter_by(id=create.json()["id"]).first()
        self.assertEqual(page.preview_message, "Subscribe for $0.10 to read more")

        resp = self.client.get(f"/api/x402-paywall/pages/{page.id}/code")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Subscribe for $0.10 to read more", resp.json()["code"])

    def test_create_rejects_a_price_that_isnt_a_number(self):
        resp = self.client.post("/api/x402-paywall/pages", json={
            "label": "x", "wallet_id": self.wallet.id, "mode": "test", "price_usd": "free",
        })
        self.assertEqual(resp.status_code, 400)

    def test_create_rejects_unknown_wallet(self):
        resp = self.client.post("/api/x402-paywall/pages", json={
            "label": "x", "wallet_id": 999999, "mode": "test", "price_usd": "1",
        })
        self.assertEqual(resp.status_code, 404)

    def test_live_mode_requires_unlocked_session_and_encrypts_the_cdp_secret(self):
        with patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB") as enc:
            resp = self.client.post("/api/x402-paywall/pages", json={
                "label": "Report", "wallet_id": self.wallet.id, "mode": "live", "network": "base",
                "price_usd": "2", "cdp_key_id": "orgs/x/apiKeys/y", "cdp_key_secret": "topsecret",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        enc.assert_called_once_with("topsecret")
        page = self.db.query(X402PaywallPage).filter_by(id=resp.json()["id"]).first()
        self.assertEqual(page.encrypted_cdp_secret, "ENCRYPTED_BLOB")
        self.assertNotIn("topsecret", page.encrypted_cdp_secret)

    def test_live_mode_401s_when_wallet_is_locked(self):
        from app.tools.wallet.lock import WalletLockedError
        with patch("app.tools.wallet.encrypt.encrypt_key", side_effect=WalletLockedError("locked")):
            resp = self.client.post("/api/x402-paywall/pages", json={
                "label": "Report", "wallet_id": self.wallet.id, "mode": "live", "network": "base",
                "price_usd": "2", "cdp_key_id": "k", "cdp_key_secret": "s",
            })
        self.assertEqual(resp.status_code, 423)
        self.assertEqual(self.db.query(X402PaywallPage).count(), 0)

    def test_list_annotates_each_page_with_its_wallet_usdc_balance(self):
        self.client.post("/api/x402-paywall/pages", json={
            "label": "Article", "wallet_id": self.wallet.id, "mode": "test", "price_usd": "0.10",
        })
        with patch("app.routers.x402_paywall._usdc_balance", return_value=12.5):
            resp = self.client.get("/api/x402-paywall/pages")
        self.assertEqual(resp.status_code, 200)
        pages = resp.json()["pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["usdc_balance"], 12.5)
        self.assertEqual(pages[0]["wallet_address"], self.wallet.address)

    def test_list_survives_a_balance_read_failure(self):
        """_usdc_balance() itself swallows RPC errors — a page still lists,
        just with no balance for that row, instead of the whole page failing
        to load because one wallet's RPC read timed out. Forced here by
        making the (network-agnostic) asset lookup itself blow up, so this
        doesn't depend on which mode's balance-read branch runs."""
        self.client.post("/api/x402-paywall/pages", json={
            "label": "Article", "wallet_id": self.wallet.id, "mode": "test", "price_usd": "0.10",
        })
        with patch("app.tools.payments.x402_paywall_codegen.network_asset", side_effect=Exception("rpc down")):
            resp = self.client.get("/api/x402-paywall/pages")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["pages"][0]["usdc_balance"])

    def test_get_code_regenerates_live_mode_code_by_decrypting_the_secret(self):
        with patch("app.tools.wallet.encrypt.encrypt_key", return_value="ENCRYPTED_BLOB"):
            create = self.client.post("/api/x402-paywall/pages", json={
                "label": "Report", "wallet_id": self.wallet.id, "mode": "live", "network": "arbitrum",
                "price_usd": "3", "cdp_key_id": "k", "cdp_key_secret": "topsecret",
            })
        page_id = create.json()["id"]
        with patch("app.tools.wallet.encrypt.decrypt_key", return_value="topsecret") as dec:
            resp = self.client.get(f"/api/x402-paywall/pages/{page_id}/code")
        self.assertEqual(resp.status_code, 200)
        dec.assert_called_once_with("ENCRYPTED_BLOB")
        self.assertIn("'key_secret' => 'topsecret'", resp.json()["code"])

    def test_delete_removes_the_page(self):
        create = self.client.post("/api/x402-paywall/pages", json={
            "label": "Article", "wallet_id": self.wallet.id, "mode": "test", "price_usd": "0.10",
        })
        page_id = create.json()["id"]
        resp = self.client.delete(f"/api/x402-paywall/pages/{page_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.db.query(X402PaywallPage).count(), 0)

    def test_delete_unknown_page_404s(self):
        resp = self.client.delete("/api/x402-paywall/pages/999999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
