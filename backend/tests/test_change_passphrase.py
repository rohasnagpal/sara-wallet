import pathlib
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.models import Base, ProofRecord, Wallet
from app.tools.wallet import encrypt
from app.tools.wallet import lock as lock_state


class ChangePassphraseTests(unittest.TestCase):
    """lock.change_passphrase() and its shared _stage_and_reencrypt() core.

    lock.py's session state and encrypt.py's env-file path are process-wide
    module globals, so every test isolates its own temp .env.local and its
    own in-memory DB rather than touching the real repo-root .env.local or
    leaking state between tests.
    """

    def setUp(self):
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        env_file = pathlib.Path(tmp_dir.name) / ".env.local"
        pending_file = env_file.with_name(env_file.name + ".migration-pending")
        for patcher in (
            patch.object(encrypt, "_ENV_FILE", env_file),
            patch.object(encrypt, "_PENDING_MIGRATION_FILE", pending_file),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        session_local_patcher = patch.object(db_session, "SessionLocal", self.Session)
        session_local_patcher.start()
        self.addCleanup(session_local_patcher.stop)

        lock_state._session_key = None
        lock_state._last_activity = 0.0
        lock_state._failed_attempts = 0
        lock_state._locked_until = 0.0
        self.addCleanup(lock_state.lock)

    def _make_wallet_and_proof(self):
        db = self.Session()
        db.add(Wallet(
            name="main", chain="evm", address="0x" + "11" * 20,
            encrypted_key=encrypt.encrypt_key("super-secret-private-key"),
        ))
        db.add(ProofRecord(
            checkout_id="CO_1", wallet_id=1, wallet_address="0x" + "11" * 20,
            encrypted_details=encrypt.encrypt_key('{"a":1}'),
            encrypted_proof=encrypt.encrypt_key('{"b":2}'),
            encrypted_evidence=encrypt.encrypt_bytes(b"evidence-bytes"),
        ))
        db.commit()
        db.close()

    def test_change_passphrase_reencrypts_wallets_and_proofs(self):
        lock_state.setup_passphrase("original-passphrase")
        self._make_wallet_and_proof()

        self.assertTrue(lock_state.change_passphrase("original-passphrase", "brand-new-passphrase"))

        # The old passphrase must no longer unlock — it was verified
        # against the *new* salt/verifier now, not the one it originally set.
        lock_state.lock()
        self.assertFalse(lock_state.unlock("original-passphrase"))

        # The new passphrase unlocks, and every re-encrypted field decrypts
        # back to its original plaintext under the new key.
        self.assertTrue(lock_state.unlock("brand-new-passphrase"))
        db = self.Session()
        wallet = db.query(Wallet).first()
        proof = db.query(ProofRecord).first()
        self.assertEqual(encrypt.decrypt_key(wallet.encrypted_key), "super-secret-private-key")
        self.assertEqual(encrypt.decrypt_key(proof.encrypted_details), '{"a":1}')
        self.assertEqual(encrypt.decrypt_key(proof.encrypted_proof), '{"b":2}')
        self.assertEqual(encrypt.decrypt_bytes(proof.encrypted_evidence), b"evidence-bytes")
        db.close()

    def test_change_passphrase_keeps_session_unlocked_under_new_key(self):
        lock_state.setup_passphrase("original-passphrase")
        self._make_wallet_and_proof()

        lock_state.change_passphrase("original-passphrase", "brand-new-passphrase")

        self.assertTrue(lock_state.is_unlocked())

    def test_wrong_old_passphrase_changes_nothing(self):
        lock_state.setup_passphrase("original-passphrase")
        self._make_wallet_and_proof()

        self.assertFalse(lock_state.change_passphrase("totally-wrong-passphrase", "brand-new-passphrase"))

        lock_state.lock()
        self.assertTrue(lock_state.unlock("original-passphrase"))

    def test_new_passphrase_too_short_is_rejected_and_changes_nothing(self):
        lock_state.setup_passphrase("original-passphrase")
        self._make_wallet_and_proof()

        with self.assertRaises(ValueError):
            lock_state.change_passphrase("original-passphrase", "short")

        lock_state.lock()
        self.assertTrue(lock_state.unlock("original-passphrase"))

    def test_requires_an_already_unlocked_session(self):
        lock_state.setup_passphrase("original-passphrase")
        lock_state.lock()

        with self.assertRaises(lock_state.WalletLockedError):
            lock_state.change_passphrase("original-passphrase", "brand-new-passphrase")

    def test_wrong_old_passphrase_is_throttled_like_unlock(self):
        lock_state.setup_passphrase("original-passphrase")
        for _ in range(3):
            lock_state.change_passphrase("wrong-passphrase", "brand-new-passphrase")
        with self.assertRaises(lock_state.WalletThrottledError):
            lock_state.change_passphrase("wrong-passphrase", "brand-new-passphrase")
