"""Tests for BIP-39/44 seed-phrase wallets: one recovery phrase backs up
many wallets (the MetaMask model), with a single default seed created
automatically on first use and additional seeds as an explicit, advanced
action - never automatic. See app/tools/wallet/seeds.py.

Wallets created before this feature (or ever imported from a raw private
key) have no seed at all (seed_id/derivation_index stay NULL) - that's
their permanent, correct state, not a migration gap; keygen.py's
independent, purely-CSPRNG-generated wallets are untouched and still
reachable via POST /wallets/import.
"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Wallet, WalletSeed
from app.tools.wallet import seeds
from app.tools.wallet.seeds import SeedError, derive_wallet, generate_seed_phrase, validate_seed_phrase


class DerivationTests(unittest.TestCase):
    """The actual cryptography - no DB, no router, just the math."""

    def test_generates_a_valid_24_word_phrase(self):
        phrase = generate_seed_phrase()
        self.assertEqual(len(phrase.split()), 24)
        self.assertEqual(validate_seed_phrase(phrase), phrase)  # round-trips through validation unchanged

    def test_two_generated_phrases_are_different(self):
        self.assertNotEqual(generate_seed_phrase(), generate_seed_phrase())

    def test_validate_normalizes_case_and_whitespace(self):
        phrase = generate_seed_phrase()
        messy = "  " + phrase.upper().replace(" ", "   ") + "  "
        self.assertEqual(validate_seed_phrase(messy), phrase)

    def test_validate_rejects_a_bad_checksum(self):
        phrase = generate_seed_phrase()
        words = phrase.split()
        words[0] = "abandon" if words[0] != "abandon" else "zoo"
        with self.assertRaises(SeedError):
            validate_seed_phrase(" ".join(words))

    def test_validate_rejects_blank(self):
        with self.assertRaises(SeedError):
            validate_seed_phrase("")
        with self.assertRaises(SeedError):
            validate_seed_phrase("   ")

    def test_validate_rejects_words_not_in_the_bip39_wordlist(self):
        with self.assertRaises(SeedError):
            validate_seed_phrase("sara wallet is definitely not a real bip39 mnemonic phrase at all here")

    def test_derivation_is_deterministic(self):
        phrase = generate_seed_phrase()
        a = derive_wallet(phrase, 0)
        b = derive_wallet(phrase, 0)
        self.assertEqual(a, b)

    def test_different_indices_give_different_wallets(self):
        phrase = generate_seed_phrase()
        a = derive_wallet(phrase, 0)
        b = derive_wallet(phrase, 1)
        self.assertNotEqual(a["address"], b["address"])
        self.assertNotEqual(a["private_key"], b["private_key"])

    def test_different_phrases_give_different_wallets_at_the_same_index(self):
        a = derive_wallet(generate_seed_phrase(), 0)
        b = derive_wallet(generate_seed_phrase(), 0)
        self.assertNotEqual(a["address"], b["address"])

    def test_negative_index_rejected(self):
        with self.assertRaises(SeedError):
            derive_wallet(generate_seed_phrase(), -1)

    def test_derived_private_key_actually_controls_the_derived_address(self):
        """Not just internally consistent - the returned private_key must
        actually be the key for the returned address, via eth_account
        itself, independent of how derive_wallet built either value."""
        from eth_account import Account
        phrase = generate_seed_phrase()
        result = derive_wallet(phrase, 3)
        self.assertEqual(Account.from_key(result["private_key"]).address, result["address"])

    def test_derivation_matches_an_independent_implementation(self):
        """Cross-checked against bip_utils (a completely separate BIP-44
        implementation) so this is a genuinely standard, MetaMask-portable
        phrase - not something only Sara's own code agrees with itself
        about. Skips gracefully if bip_utils isn't installed (it's a
        verification tool for this test only, not a runtime dependency)."""
        try:
            from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
        except ImportError:
            self.skipTest("bip_utils not installed - independent cross-check skipped")
        phrase = generate_seed_phrase()
        seed_bytes = Bip39SeedGenerator(phrase).Generate()
        account = (
            Bip44.FromSeed(seed_bytes, Bip44Coins.ETHEREUM)
            .Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
        )
        for index in (0, 1, 5):
            expected = account.AddressIndex(index).PublicKey().ToAddress()
            actual = derive_wallet(phrase, index)["address"]
            self.assertEqual(actual.lower(), expected.lower())


class MigrationTests(unittest.TestCase):
    def test_wallets_predating_seeds_get_nullable_columns_added(self):
        import tempfile
        from app.db.migrations import run_migrations

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            engine = create_engine(f"sqlite:///{db_file.name}")
            with engine.begin() as conn:
                # A wallets table shaped exactly like it was before this feature.
                conn.execute(text(
                    "CREATE TABLE wallets (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE NOT NULL, "
                    "chain VARCHAR NOT NULL, address VARCHAR NOT NULL, encrypted_key TEXT NOT NULL, "
                    "created_at DATETIME)"
                ))
                conn.execute(text(
                    "INSERT INTO wallets (name, chain, address, encrypted_key) "
                    "VALUES ('old', 'evm', '0x" + "11" * 20 + "', 'deadbeef')"
                ))
            Base.metadata.create_all(engine)
            run_migrations(engine)
            run_migrations(engine)  # idempotent

            columns = {c["name"] for c in inspect(engine).get_columns("wallets")}
            self.assertIn("seed_id", columns)
            self.assertIn("derivation_index", columns)
            self.assertIn("wallet_seeds", inspect(engine).get_table_names())

            session = sessionmaker(bind=engine)()
            old_wallet = session.query(Wallet).filter_by(name="old").first()
            self.assertIsNotNone(old_wallet)
            self.assertIsNone(old_wallet.seed_id)
            self.assertIsNone(old_wallet.derivation_index)


def _fake_encrypt(plaintext: str) -> str:
    return "ENC:" + plaintext


def _fake_decrypt(blob: str) -> str:
    return blob[len("ENC:"):]


class WalletSeedRouterTests(unittest.TestCase):
    """Real content-preserving fake encryption (not a fixed dummy value) so
    multiple seeds/wallets round-trip distinctly within one test, without
    needing a real unlock session set up. The actual AES-GCM path is
    already covered by app/tools/wallet/encrypt.py's own tests; what's
    under test here is the seed/index bookkeeping and the API surface."""

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.session_auth import require_session
        from app.routers import wallets as wallets_router

        self.wallets_router = wallets_router
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = Session()

        app = FastAPI()
        app.include_router(wallets_router.router, prefix="/api")
        app.dependency_overrides[wallets_router.get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

        self._enc_patch = patch.object(wallets_router, "encrypt_key", side_effect=_fake_encrypt)
        self._dec_patch = patch.object(wallets_router, "decrypt_key", side_effect=_fake_decrypt)
        self._enc_patch.start()
        self._dec_patch.start()

    def tearDown(self):
        self._enc_patch.stop()
        self._dec_patch.stop()
        self.db.close()

    def test_first_wallet_auto_creates_a_default_seed_and_returns_the_phrase_once(self):
        resp = self.client.post("/api/wallets/create", json={"name": "w1"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertIn("seed_phrase", data)
        self.assertEqual(len(data["seed_phrase"].split()), 24)
        self.assertEqual(data["seed_label"], "Default")
        self.assertEqual(data["derivation_index"], 0)
        self.assertEqual(self.db.query(WalletSeed).count(), 1)

    def test_second_wallet_reuses_the_default_seed_without_reshowing_the_phrase(self):
        first = self.client.post("/api/wallets/create", json={"name": "w1"}).json()
        second = self.client.post("/api/wallets/create", json={"name": "w2"}).json()
        self.assertNotIn("seed_phrase", second)
        self.assertEqual(second["seed_id"], first["seed_id"])
        self.assertEqual(second["derivation_index"], 1)
        self.assertEqual(self.db.query(WalletSeed).count(), 1)  # still just the one default

    def test_two_wallets_from_the_same_seed_get_different_addresses(self):
        first = self.client.post("/api/wallets/create", json={"name": "w1"}).json()
        second = self.client.post("/api/wallets/create", json={"name": "w2"}).json()
        self.assertNotEqual(first["address"], second["address"])

    def test_create_wallet_with_an_unknown_seed_id_404s(self):
        resp = self.client.post("/api/wallets/create", json={"name": "w1", "seed_id": 999})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.db.query(Wallet).count(), 0)

    def test_import_wallet_is_unaffected_and_stays_seedless(self):
        from eth_account import Account
        acct = Account.create()
        resp = self.client.post("/api/wallets/import", json={
            "name": "imported", "chain": "evm", "private_key": acct.key.hex(),
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        row = self.db.query(Wallet).filter_by(name="imported").first()
        self.assertIsNone(row.seed_id)
        self.assertIsNone(row.derivation_index)
        self.assertEqual(self.db.query(WalletSeed).count(), 0)  # importing never creates a seed

    def test_list_seeds_never_includes_the_phrase(self):
        self.client.post("/api/wallets/create", json={"name": "w1"})
        resp = self.client.get("/api/wallets/seeds")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["label"], "Default")
        self.assertEqual(data[0]["wallet_count"], 1)
        self.assertNotIn("seed_phrase", data[0])
        self.assertNotIn("encrypted_seed", data[0])

    def test_add_seed_generates_a_new_one_and_returns_it_once(self):
        resp = self.client.post("/api/wallets/seeds", json={"label": "Business"})
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["label"], "Business")
        self.assertEqual(len(data["seed_phrase"].split()), 24)
        self.assertEqual(self.db.query(WalletSeed).count(), 1)

    def test_new_wallet_can_target_the_second_seed_explicitly(self):
        default_seed_id = self.client.post("/api/wallets/create", json={"name": "w1"}).json()["seed_id"]
        second = self.client.post("/api/wallets/seeds", json={"label": "Business"}).json()
        w2 = self.client.post("/api/wallets/create", json={"name": "w2", "seed_id": second["id"]}).json()
        self.assertNotEqual(w2["seed_id"], default_seed_id)
        self.assertEqual(w2["derivation_index"], 0)  # its own seed's own index sequence, independent of the default's

    def test_add_seed_imports_an_existing_phrase_without_reshowing_it(self):
        phrase = generate_seed_phrase()
        resp = self.client.post("/api/wallets/seeds", json={"label": "Recovered", "seed_phrase": phrase})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertNotIn("seed_phrase", resp.json())  # the caller already has it - no need to echo it back

    def test_add_seed_rejects_an_invalid_phrase(self):
        resp = self.client.post("/api/wallets/seeds", json={"label": "Bad", "seed_phrase": "not a real phrase"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.db.query(WalletSeed).count(), 0)

    def test_add_seed_rejects_a_phrase_already_added(self):
        phrase = generate_seed_phrase()
        self.client.post("/api/wallets/seeds", json={"label": "First", "seed_phrase": phrase})
        resp = self.client.post("/api/wallets/seeds", json={"label": "Dupe", "seed_phrase": phrase})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already added", resp.text)
        self.assertEqual(self.db.query(WalletSeed).count(), 1)

    def test_reveal_seed_requires_the_correct_passphrase(self):
        seed_id = self.client.post("/api/wallets/seeds", json={"label": "X"}).json()["id"]
        with patch.object(self.wallets_router, "verify_passphrase", return_value=False):
            resp = self.client.post(f"/api/wallets/seeds/{seed_id}/reveal", json={"passphrase": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_reveal_seed_returns_the_real_phrase_on_a_correct_passphrase(self):
        created = self.client.post("/api/wallets/seeds", json={"label": "X"}).json()
        with patch.object(self.wallets_router, "verify_passphrase", return_value=True):
            resp = self.client.post(f"/api/wallets/seeds/{created['id']}/reveal", json={"passphrase": "right"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["seed_phrase"], created["seed_phrase"])

    def test_reveal_seed_404s_for_an_unknown_seed(self):
        with patch.object(self.wallets_router, "verify_passphrase", return_value=True):
            resp = self.client.post("/api/wallets/seeds/999/reveal", json={"passphrase": "right"})
        self.assertEqual(resp.status_code, 404)

    def test_reveal_seed_rejects_a_blank_passphrase_before_checking_it(self):
        seed_id = self.client.post("/api/wallets/seeds", json={"label": "X"}).json()["id"]
        with patch.object(self.wallets_router, "verify_passphrase") as mock_verify:
            resp = self.client.post(f"/api/wallets/seeds/{seed_id}/reveal", json={"passphrase": "  "})
        self.assertEqual(resp.status_code, 400)
        mock_verify.assert_not_called()

    def test_create_wallet_423s_when_locked_and_persists_nothing(self):
        from app.tools.wallet.lock import WalletLockedError
        with patch.object(self.wallets_router, "encrypt_key", side_effect=WalletLockedError("locked")):
            resp = self.client.post("/api/wallets/create", json={"name": "w1"})
        self.assertEqual(resp.status_code, 423)
        self.assertEqual(self.db.query(Wallet).count(), 0)
        self.assertEqual(self.db.query(WalletSeed).count(), 0)


if __name__ == "__main__":
    unittest.main()
