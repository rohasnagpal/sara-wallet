import time

SESSION_TIMEOUT_SECONDS = 900  # 15 minutes of inactivity

_session_key: bytes | None = None
_last_activity: float = 0.0


class WalletLockedError(Exception):
    pass


def is_configured() -> bool:
    from app.tools.wallet.encrypt import _read_env_key
    return bool(_read_env_key())


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


def setup_passphrase(passphrase: str) -> None:
    global _session_key, _last_activity
    from app.tools.wallet.encrypt import save_master_key, normalize_master_key
    if is_configured():
        raise WalletLockedError("A passphrase is already set. Use unlock instead.")
    key_hex = save_master_key(passphrase)
    _session_key = bytes.fromhex(key_hex)
    _last_activity = time.time()


def unlock(passphrase: str) -> bool:
    global _session_key, _last_activity
    from app.tools.wallet.encrypt import normalize_master_key, _read_env_key
    persisted = _read_env_key()
    if not persisted:
        raise WalletLockedError("No passphrase has been set up yet.")
    candidate = normalize_master_key(passphrase)
    if candidate != persisted.lower():
        return False
    _session_key = bytes.fromhex(candidate)
    _last_activity = time.time()
    return True


def lock() -> None:
    global _session_key
    _session_key = None
