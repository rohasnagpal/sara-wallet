import os
import threading
import time

SESSION_TIMEOUT_SECONDS = 3600  # 1 hour of inactivity

_session_key: bytes | None = None
_last_activity: float = 0.0

# Serializes _stage_and_reencrypt (legacy-format upgrade and explicit
# change_passphrase both call it): it stages a new salt/verifier to one
# deterministic, shared file, then re-encrypts every wallet, then promotes
# that file — two of those overlapping (e.g. a double-submitted passphrase
# change) previously let the second call's promote() replace .env.local
# with a salt/verifier that didn't correspond to whichever new key the
# database actually ended up encrypted with, permanently bricking every
# wallet: unlock() would succeed (the verifier matches), but decrypting any
# wallet's key would fail forever, with no recovery. With this lock, the
# second call instead simply runs after the first one is fully done, reads
# the first call's already-re-encrypted ciphertext, and cleanly fails (its
# now-stale old_key no longer decrypts it) with an ordinary, safe error —
# never a silent mismatch between the persisted salt and the actual key.
_reencrypt_lock = threading.Lock()

# Unlock throttling: slows online passphrase-guessing against
# /api/lock/unlock. A single in-memory counter is enough here — this is a
# single-user local app with one lock state, not a multi-tenant service.
_failed_attempts = 0
_locked_until = 0.0
_BASE_BACKOFF_SECONDS = 2
_MAX_BACKOFF_SECONDS = 300


class WalletLockedError(Exception):
    pass


class WalletThrottledError(Exception):
    pass


def is_configured() -> bool:
    from app.tools.wallet.encrypt import is_configured as _is_configured
    return _is_configured()


def is_unlocked() -> bool:
    return _session_key is not None and (time.time() - _last_activity) < SESSION_TIMEOUT_SECONDS


def touch() -> None:
    """Extend an already-unlocked session on any request activity. Does
    nothing if already locked/expired — a request after expiry must not
    silently re-arm the session."""
    global _last_activity
    if is_unlocked():
        _last_activity = time.time()


def get_active_key() -> bytes:
    global _last_activity
    if not is_unlocked():
        raise WalletLockedError("Wallet is locked. Unlock Sara with your passphrase first.")
    _last_activity = time.time()
    return _session_key


def _record_failure() -> None:
    global _failed_attempts, _locked_until
    _failed_attempts += 1
    # Exponential backoff starting after the 3rd failure — a couple of
    # mistyped passphrases shouldn't lock out a legitimate user, but
    # repeated failures should cost real wall-clock time for anyone
    # actually guessing.
    if _failed_attempts >= 3:
        backoff = min(_BASE_BACKOFF_SECONDS * (2 ** (_failed_attempts - 3)), _MAX_BACKOFF_SECONDS)
        _locked_until = time.time() + backoff


def _reset_failures() -> None:
    global _failed_attempts, _locked_until
    _failed_attempts = 0
    _locked_until = 0.0


def _check_not_throttled() -> None:
    remaining = _locked_until - time.time()
    if remaining > 0:
        raise WalletThrottledError(
            f"Too many incorrect attempts. Try again in {int(remaining) + 1}s."
        )


def setup_passphrase(passphrase: str) -> None:
    global _session_key, _last_activity
    from app.tools.wallet import encrypt
    if is_configured():
        raise WalletLockedError("A passphrase is already set. Use unlock instead.")
    _session_key = encrypt.setup_new(passphrase)
    _last_activity = time.time()
    _reset_failures()


def unlock(passphrase: str) -> bool:
    global _session_key, _last_activity
    from app.tools.wallet import encrypt
    _check_not_throttled()

    recovered_key = _recover_pending_migration(passphrase)
    if recovered_key is not None:
        _session_key = recovered_key
    elif encrypt.has_new_format():
        key = encrypt.verify_new(passphrase)
        if key is None:
            _record_failure()
            return False
        _session_key = key
    elif encrypt.has_legacy_format():
        old_key = encrypt.verify_legacy(passphrase)
        if old_key is None:
            _record_failure()
            return False
        # Correct legacy passphrase — transparently upgrade off the old
        # unsalted-SHA-256 scheme before continuing, so this same check
        # never runs again for this install.
        _session_key = _migrate_legacy_wallets(passphrase, old_key)
    else:
        raise WalletLockedError("No passphrase has been set up yet.")

    _last_activity = time.time()
    _reset_failures()
    return True


def _recover_pending_migration(passphrase: str) -> bytes | None:
    """Finish a legacy migration whose DB commit succeeded but env-file
    promotion did not. The deterministic pending file contains only a salt
    and verifier; the passphrase is still required to derive the key."""
    import logging
    from app.tools.wallet import encrypt
    if not encrypt.has_pending_migration():
        return None
    key = encrypt.verify_pending_migration(passphrase)
    if key is None:
        return None

    from app.db.session import SessionLocal
    from app.db.models import Wallet, ProofRecord
    db = SessionLocal()
    try:
        try:
            for wallet in db.query(Wallet).all():
                encrypt.decrypt_with_key(wallet.encrypted_key, key)
            for proof in db.query(ProofRecord).all():
                encrypt.decrypt_with_key(proof.encrypted_details, key)
                if proof.encrypted_proof:
                    encrypt.decrypt_with_key(proof.encrypted_proof, key)
                if proof.encrypted_evidence:
                    encrypt.decrypt_bytes_with_key(proof.encrypted_evidence, key)
        except Exception:
            # The DB never committed; current .env.local remains authoritative.
            encrypt.discard_pending_migration()
            return None
    finally:
        db.close()
    try:
        encrypt.promote_pending_migration()
    except Exception:
        logging.getLogger("sara.migrations").exception(
            "Recovered the pending wallet key but still could not promote .env.local; "
            "the restart-recovery file remains in place."
        )
    return key


def confirm_passphrase(passphrase: str) -> bool:
    """Re-authenticate a money-moving confirmation without replacing the
    active session key. A per-launch HTTP token is CSRF protection, not proof
    of a human: another local process can fetch it too. Requiring the wallet
    passphrase immediately before broadcast supplies that missing proof."""
    if not is_unlocked():
        raise WalletLockedError("Wallet is locked. Unlock Sara first.")
    _check_not_throttled()
    from app.tools.wallet import encrypt
    if encrypt.has_new_format():
        ok = encrypt.verify_new(passphrase) is not None
    elif encrypt.has_legacy_format():
        ok = encrypt.verify_legacy(passphrase) is not None
    else:
        raise WalletLockedError("No passphrase has been set up yet.")
    if not ok:
        _record_failure()
        return False
    _reset_failures()
    touch()
    return True


def _stage_and_reencrypt(new_salt: bytes, new_key: bytes, old_key: bytes) -> None:
    """Core of both the legacy-format upgrade and an explicit user-
    initiated passphrase change: re-encrypt every piece of master-key-
    encrypted data from old_key to new_key, and persist the new salt/
    verifier — used by _migrate_legacy_wallets and change_passphrase so
    this security-critical sequence exists in exactly one place.

    This touches two separate resources (the DB and .env.local) that can't
    be updated in one real transaction, so the ordering here is deliberate:
      1. Stage the new env-format content to a deterministic, owner-only
         restart-recovery file — if this fails, nothing has changed at all.
      2. Re-encrypt every wallet and every ProofRecord's encrypted fields
         from the old key to the new one, in one DB transaction. If
         anything here raises, the staged file is discarded and nothing
         has changed.
      3. Only once the DB commit has actually succeeded, promote the staged
         file with a single atomic os.replace(). If promotion or the process
         fails after the DB commit, the deterministic pending file remains;
         the next unlock derives its key from the supplied passphrase,
         validates it against every wallet, and safely retries promotion.
    """
    import logging
    from app.tools.wallet import encrypt
    from app.db.session import SessionLocal
    from app.db.models import Wallet, ProofRecord

    encrypt.stage_migration_update({
        "SARA_MASTER_KEY": None,
        "SARA_MASTER_SALT": new_salt.hex(),
        "SARA_MASTER_VERIFIER": encrypt._verifier_for(new_key),
    })

    try:
        db = SessionLocal()
        try:
            for w in db.query(Wallet).all():
                plaintext = encrypt.decrypt_with_key(w.encrypted_key, old_key)
                w.encrypted_key = encrypt.encrypt_with_key(plaintext, new_key)
            for proof in db.query(ProofRecord).all():
                details = encrypt.decrypt_with_key(proof.encrypted_details, old_key)
                proof.encrypted_details = encrypt.encrypt_with_key(details, new_key)
                if proof.encrypted_proof:
                    proof_json = encrypt.decrypt_with_key(proof.encrypted_proof, old_key)
                    proof.encrypted_proof = encrypt.encrypt_with_key(proof_json, new_key)
                if proof.encrypted_evidence:
                    evidence = encrypt.decrypt_bytes_with_key(proof.encrypted_evidence, old_key)
                    proof.encrypted_evidence = encrypt.encrypt_bytes_with_key(evidence, new_key)
            db.commit()
        finally:
            db.close()
    except Exception:
        encrypt.discard_pending_migration()
        raise

    try:
        encrypt.promote_pending_migration()
    except Exception:
        logging.getLogger("sara.migrations").exception(
            "Wallets were re-encrypted with a new key, but Sara could not persist that key's "
            "salt/verifier to .env.local. A restart-recovery file was retained; the next unlock "
            "with this passphrase will validate it against the migrated wallets and retry the "
            "atomic promotion."
        )


def _migrate_legacy_wallets(passphrase: str, old_key: bytes) -> bytes:
    """One-time upgrade off the legacy scheme, where the persisted
    SARA_MASTER_KEY *was* the literal AES key (or an unsalted SHA-256 of the
    passphrase) — anyone with .env.local and the DB could decrypt every
    wallet with no passphrase needed at all. See _stage_and_reencrypt for
    the actual re-encryption sequence."""
    from app.tools.wallet import encrypt

    new_salt = os.urandom(16)
    new_key = encrypt._scrypt_key(passphrase, new_salt)
    with _reencrypt_lock:
        _stage_and_reencrypt(new_salt, new_key, old_key)
    return new_key


def change_passphrase(old_passphrase: str, new_passphrase: str) -> bool:
    """User-initiated passphrase change: verifies old_passphrase, then
    re-encrypts every wallet/proof-record from the current key to a
    freshly salted/scrypt-derived key from new_passphrase, via the same
    _stage_and_reencrypt sequence _migrate_legacy_wallets uses.

    Requires an already-unlocked session on top of the correct old
    passphrase — same reasoning as confirm_passphrase: a per-launch HTTP
    session token alone isn't proof of a human, so this is belt-and-
    suspenders on top of require_session, not redundant with it.

    Returns False (not an exception) for an incorrect old_passphrase, so
    the router can turn that into a 401 the same way unlock() does.
    """
    global _session_key, _last_activity
    from app.tools.wallet import encrypt

    if not is_unlocked():
        raise WalletLockedError("Wallet is locked. Unlock Sara first.")
    _check_not_throttled()

    if not encrypt.has_new_format():
        raise WalletLockedError(
            "Unlock Sara once with your current passphrase before changing it "
            "(this also finishes a one-time internal upgrade on older installs)."
        )

    old_key = encrypt.verify_new(old_passphrase)
    if old_key is None:
        _record_failure()
        return False

    encrypt._validate_new_passphrase(new_passphrase)

    new_salt = os.urandom(16)
    new_key = encrypt._scrypt_key(new_passphrase, new_salt)
    with _reencrypt_lock:
        _stage_and_reencrypt(new_salt, new_key, old_key)

    # Stays unlocked under the new key — the user just typed both
    # passphrases seconds ago; forcing an immediate re-unlock would be
    # pure friction, not a real security gain.
    _session_key = new_key
    _last_activity = time.time()
    _reset_failures()
    return True


def lock() -> None:
    global _session_key
    _session_key = None
