"""Critical regression test: two concurrent change_passphrase calls (e.g. a
double-submitted form, or two tabs) with the same old/new passphrase used
to be able to permanently brick every wallet.

_stage_and_reencrypt stages a new salt/verifier to one deterministic,
shared file, re-encrypts every wallet, then promotes that file to
.env.local. Two overlapping calls could interleave so that whichever call's
salt/verifier ended up promoted didn't correspond to whichever new key the
database actually ended up encrypted with: unlock() would then succeed (the
verifier matches what's on disk) but decrypting any wallet's key would fail
forever, with no recovery path. A threading.Lock now serializes the whole
stage -> re-encrypt -> commit -> promote sequence, so the second call
instead runs strictly after the first is completely done, and cleanly fails
(its now-stale old_key can no longer decrypt the first call's
already-re-encrypted ciphertext) rather than corrupting the persisted salt.

This test uses real threads against a real (temp-directory) env file and a
real SQLite database — the actual mechanism that was broken — not just the
lock object in isolation.
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Wallet
from app.tools.wallet import encrypt, lock

OLD_PASSPHRASE = "correct horse battery staple"
NEW_PASSPHRASE = "the same new passphrase both submits used"
PLAINTEXT_KEY = "0x" + "ab" * 32


class ConcurrentPassphraseChangeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_dir = Path(self._tmp.name)
        env_file = tmp_dir / ".env.local"
        pending_file = tmp_dir / ".env.local.migration-pending"

        # Point encrypt.py's module-level file paths at a scratch directory
        # for this test only — never the real .env.local.
        self._env_patch = patch.object(encrypt, "_ENV_FILE", env_file)
        self._pending_patch = patch.object(encrypt, "_PENDING_MIGRATION_FILE", pending_file)
        self._env_patch.start(); self.addCleanup(self._env_patch.stop)
        self._pending_patch.start(); self.addCleanup(self._pending_patch.stop)

        engine = create_engine(f"sqlite:///{tmp_dir}/wallets.db")
        Base.metadata.create_all(engine)
        # _stage_and_reencrypt (and _recover_pending_migration) both do a
        # local `from app.db.session import SessionLocal` at call time, so
        # patching the module attribute here is enough to redirect them.
        self._db_session_patch = patch("app.db.session.SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
        self._db_session_patch.start(); self.addCleanup(self._db_session_patch.stop)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        self.db = Session()

        session_key = encrypt.setup_new(OLD_PASSPHRASE)
        encrypted = encrypt.encrypt_with_key(PLAINTEXT_KEY, session_key)
        self.db.add(Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key=encrypted))
        self.db.commit()

        lock._session_key = session_key
        lock._last_activity = time.time()
        lock._failed_attempts = 0
        lock._locked_until = 0.0
        self.addCleanup(setattr, lock, "_session_key", None)

    def tearDown(self):
        self.db.close()

    def _change(self, results, key, old=OLD_PASSPHRASE, new=NEW_PASSPHRASE):
        try:
            results[key] = lock.change_passphrase(old, new)
        except Exception as exc:
            results[key] = exc

    def test_two_concurrent_identical_changes_never_brick_the_wallet(self):
        results = {}
        threads = [threading.Thread(target=self._change, args=(results, name)) for name in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Exactly one of the two succeeds; the other must fail with an
        # ordinary, safe exception (never hang, never silently "succeed"
        # while corrupting the persisted salt).
        outcomes = list(results.values())
        self.assertEqual(len(outcomes), 2)
        successes = [o for o in outcomes if o is True]
        failures = [o for o in outcomes if isinstance(o, Exception)]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(failures), 1, results)
        self.assertIsInstance(failures[0], ValueError)
        self.assertIn("cannot be decrypted", str(failures[0]))

        # The critical assertion: after this race, the new passphrase must
        # both unlock *and* actually decrypt the wallet back to the exact
        # original private key. Before the fix, unlock() could return True
        # while every wallet was permanently undecryptable.
        lock._session_key = None
        self.assertTrue(lock.unlock(NEW_PASSPHRASE))
        self.db.expire_all()
        wallet = self.db.query(Wallet).filter_by(name="Main").first()
        recovered = encrypt.decrypt_with_key(wallet.encrypted_key, lock._session_key)
        self.assertEqual(recovered, PLAINTEXT_KEY)

    def test_a_solo_change_still_works_normally(self):
        self.assertTrue(lock.change_passphrase(OLD_PASSPHRASE, NEW_PASSPHRASE))
        lock._session_key = None
        self.assertTrue(lock.unlock(NEW_PASSPHRASE))
        wallet = self.db.query(Wallet).filter_by(name="Main").first()
        recovered = encrypt.decrypt_with_key(wallet.encrypted_key, lock._session_key)
        self.assertEqual(recovered, PLAINTEXT_KEY)


if __name__ == "__main__":
    unittest.main()
