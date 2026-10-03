"""Tests for Sara's single BIP-39 recovery phrase and HD wallets."""
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, RecoverySeed, Wallet
from app.tools.wallet.seeds import SeedError, derive_wallet, generate_seed_phrase, validate_seed_phrase


class DerivationTests(unittest.TestCase):
    def test_generates_a_valid_24_word_phrase(self):
        phrase = generate_seed_phrase()
        self.assertEqual(len(phrase.split()), 24)
        self.assertEqual(validate_seed_phrase(phrase), phrase)

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

    def test_validate_rejects_blank_and_non_bip39_words(self):
        with self.assertRaises(SeedError):
            validate_seed_phrase("")
        with self.assertRaises(SeedError):
            validate_seed_phrase("sara wallet is definitely not a real bip39 mnemonic phrase at all here")

    def test_derivation_is_deterministic_and_indexed(self):
        phrase = generate_seed_phrase()
        self.assertEqual(derive_wallet(phrase, 0), derive_wallet(phrase, 0))
        self.assertNotEqual(derive_wallet(phrase, 0)["address"], derive_wallet(phrase, 1)["address"])

    def test_different_phrases_give_different_wallets(self):
        self.assertNotEqual(
            derive_wallet(generate_seed_phrase(), 0)["address"],
            derive_wallet(generate_seed_phrase(), 0)["address"],
        )

    def test_negative_index_rejected(self):
        with self.assertRaises(SeedError):
            derive_wallet(generate_seed_phrase(), -1)

    def test_derived_private_key_controls_the_address(self):
        from eth_account import Account
        result = derive_wallet(generate_seed_phrase(), 3)
        self.assertEqual(Account.from_key(result["private_key"]).address, result["address"])

    def test_derivation_matches_independent_bip44_implementation(self):
        try:
            from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
        except ImportError:
            self.skipTest("bip_utils not installed")
        phrase = generate_seed_phrase()
        account = (
            Bip44.FromSeed(Bip39SeedGenerator(phrase).Generate(), Bip44Coins.ETHEREUM)
            .Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
        )
        for index in (0, 1, 5):
            self.assertEqual(
                derive_wallet(phrase, index)["address"].lower(),
                account.AddressIndex(index).PublicKey().ToAddress().lower(),
            )


class MigrationTests(unittest.TestCase):
    def test_multi_seed_schema_collapses_to_first_recovery_seed(self):
        from app.db.migrations import run_migrations

        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            engine = create_engine(f"sqlite:///{db_file.name}")
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE wallets (id INTEGER PRIMARY KEY, name VARCHAR UNIQUE NOT NULL, "
                    "chain VARCHAR NOT NULL, address VARCHAR NOT NULL, encrypted_key TEXT NOT NULL, "
                    "created_at DATETIME)"
                ))
                conn.execute(text(
                    "CREATE TABLE wallet_seeds (id INTEGER PRIMARY KEY, label VARCHAR NOT NULL, "
                    "encrypted_seed TEXT NOT NULL, next_index INTEGER NOT NULL, created_at DATETIME)"
                ))
                conn.execute(text(
                    "INSERT INTO wallet_seeds VALUES "
                    "(4, 'Default', 'first-ciphertext', 3, CURRENT_TIMESTAMP), "
                    "(5, 'Extra', 'discarded-ciphertext', 1, CURRENT_TIMESTAMP)"
                ))
            Base.metadata.create_all(engine)
            run_migrations(engine)
            run_migrations(engine)

            columns = {c["name"] for c in inspect(engine).get_columns("wallets")}
            self.assertIn("derivation_index", columns)
            self.assertNotIn("seed_id", columns)
            self.assertIn("recovery_seed", inspect(engine).get_table_names())
            self.assertNotIn("wallet_seeds", inspect(engine).get_table_names())
            with engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT id, encrypted_seed, next_index FROM recovery_seed"
                )).one()
            self.assertEqual(tuple(row), (1, "first-ciphertext", 3))


def _fake_encrypt(plaintext: str) -> str:
    return "ENC:" + plaintext


def _fake_decrypt(blob: str) -> str:
    return blob[len("ENC:"):]


class RecoverySeedRouterTests(unittest.TestCase):
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
        session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = session()

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

    def test_first_wallet_generates_the_only_seed_and_returns_phrase_once(self):
        first = self.client.post("/api/wallets/create", json={"name": "w1"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(first.json()["seed_phrase"].split()), 24)
        self.assertEqual(first.json()["derivation_index"], 0)
        self.assertNotIn("seed_id", first.json())
        self.assertEqual(self.db.query(RecoverySeed).count(), 1)

        second = self.client.post("/api/wallets/create", json={"name": "w2"})
        self.assertNotIn("seed_phrase", second.json())
        self.assertEqual(second.json()["derivation_index"], 1)
        self.assertNotEqual(first.json()["address"], second.json()["address"])
        self.assertEqual(self.db.query(RecoverySeed).count(), 1)

    def test_imported_private_key_does_not_create_or_join_seed(self):
        from eth_account import Account
        account = Account.create()
        response = self.client.post("/api/wallets/import", json={
            "name": "imported", "private_key": account.key.hex(),
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(self.db.query(Wallet).one().derivation_index)
        self.assertEqual(self.db.query(RecoverySeed).count(), 0)

    def test_status_never_exposes_seed_material(self):
        self.client.post("/api/wallets/create", json={"name": "w1"})
        response = self.client.get("/api/wallets/seed")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["configured"])
        self.assertEqual(response.json()["wallet_count"], 1)
        self.assertNotIn("seed_phrase", response.json())
        self.assertNotIn("encrypted_seed", response.json())

    def test_restored_phrase_derives_the_expected_first_wallet(self):
        phrase = generate_seed_phrase()
        restored = self.client.post("/api/wallets/seed/restore", json={"seed_phrase": phrase})
        self.assertEqual(restored.status_code, 200, restored.text)
        wallet = self.client.post("/api/wallets/create", json={"name": "restored"}).json()
        self.assertEqual(wallet["address"], derive_wallet(phrase, 0)["address"])
        self.assertNotIn("seed_phrase", wallet)

    def test_restore_rejects_invalid_or_second_phrase(self):
        invalid = self.client.post("/api/wallets/seed/restore", json={"seed_phrase": "not a phrase"})
        self.assertEqual(invalid.status_code, 400)
        phrase = generate_seed_phrase()
        self.assertEqual(
            self.client.post("/api/wallets/seed/restore", json={"seed_phrase": phrase}).status_code,
            200,
        )
        second = self.client.post("/api/wallets/seed/restore", json={"seed_phrase": generate_seed_phrase()})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.db.query(RecoverySeed).count(), 1)

    def test_reveal_requires_seed_nonblank_and_correct_passphrase(self):
        missing = self.client.post("/api/wallets/seed/reveal", json={"passphrase": "right"})
        self.assertEqual(missing.status_code, 404)
        created = self.client.post("/api/wallets/create", json={"name": "w1"}).json()
        with patch.object(self.wallets_router, "verify_passphrase", return_value=False):
            wrong = self.client.post("/api/wallets/seed/reveal", json={"passphrase": "wrong"})
        self.assertEqual(wrong.status_code, 401)
        with patch.object(self.wallets_router, "verify_passphrase") as verify:
            blank = self.client.post("/api/wallets/seed/reveal", json={"passphrase": "  "})
        self.assertEqual(blank.status_code, 400)
        verify.assert_not_called()
        with patch.object(self.wallets_router, "verify_passphrase", return_value=True):
            revealed = self.client.post("/api/wallets/seed/reveal", json={"passphrase": "right"})
        self.assertEqual(revealed.status_code, 200)
        self.assertEqual(revealed.json()["seed_phrase"], created["seed_phrase"])

    def test_create_wallet_while_locked_persists_nothing(self):
        from app.tools.wallet.lock import WalletLockedError
        with patch.object(self.wallets_router, "encrypt_key", side_effect=WalletLockedError("locked")):
            response = self.client.post("/api/wallets/create", json={"name": "w1"})
        self.assertEqual(response.status_code, 423)
        self.assertEqual(self.db.query(Wallet).count(), 0)
        self.assertEqual(self.db.query(RecoverySeed).count(), 0)


if __name__ == "__main__":
    unittest.main()
