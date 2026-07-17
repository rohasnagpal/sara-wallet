import hashlib
import hmac
import os, pathlib, stat
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_ENV_FILE = pathlib.Path(__file__).parents[4] / ".env.local"  # sara-wallet/.env.local (gitignored; .env is the tracked template)
_PENDING_MIGRATION_FILE = _ENV_FILE.with_name(_ENV_FILE.name + ".migration-pending")
_HEX_CHARS = set("0123456789abcdefABCDEF")

# scrypt cost parameters — "interactive" tier per RFC 7914 (roughly
# 100-300ms on typical hardware). Orders of magnitude more expensive per
# guess than the single unsalted SHA-256 round this replaces, while still
# fast enough not to annoy someone unlocking the app by hand.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32


def normalize_master_key(value: str) -> str:
    """Accept either a 64-char hex key or any user passphrase.

    This is the OLD (unsalted, single-round SHA-256) derivation. It's kept
    only so a legacy install's existing passphrase can still be verified
    once — to drive the one-time migration in
    app.tools.wallet.lock._migrate_legacy_wallets — and for the raw-hex-key
    escape hatch, which is already as high-entropy as scrypt's output and
    isn't subject to dictionary attacks. New passphrase-based setups never
    call this; see setup_new/verify_new.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("SARA_MASTER_KEY cannot be blank")
    if len(raw) == 64 and all(ch in _HEX_CHARS for ch in raw):
        return raw.lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scrypt_key(candidate: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive((candidate or "").strip().encode("utf-8"))


def _verifier_for(key: bytes) -> str:
    # Domain-separated from the actual encryption key: this is a fast hash
    # of the *derived* key, not the key itself, so leaking it doesn't hand
    # over the key directly — checking a candidate passphrase still costs a
    # full scrypt run, same as deriving the real key does.
    return hashlib.sha256(key + b"sara-verify-v1").hexdigest()


def _read_env_lines() -> list[str]:
    if not _ENV_FILE.exists():
        return []
    return _ENV_FILE.read_text().splitlines()


def _read_env_value(name: str) -> str:
    prefix = f"{name}="
    for line in _read_env_lines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _read_env_key() -> str:
    """Legacy format: the persisted value *is* the AES key (or its unsalted
    SHA-256 derivation from a passphrase) — see normalize_master_key."""
    return _read_env_value("SARA_MASTER_KEY")


def _restrict_env_file_permissions() -> None:
    """This file holds the wallet-encryption key material — default file
    creation (umask-masked 0o666, typically 0o644) leaves it group/world-
    readable. Lock it to owner-only on every write, in case the umask ever
    allowed something looser."""
    try:
        os.chmod(_ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass


def _render_env_content(updates: dict) -> str:
    """Computes what .env.local's content would be after applying `updates`
    (a value of None deletes that line), without writing anything."""
    remaining = dict(updates)
    out = []
    for line in _read_env_lines():
        matched_key = next((k for k in remaining if line.startswith(f"{k}=")), None)
        if matched_key is None:
            out.append(line)
            continue
        value = remaining.pop(matched_key)
        if value is not None:
            out.append(f"{matched_key}={value}")
    for k, v in remaining.items():
        if v is not None:
            out.append(f"{k}={v}")
    return "\n".join(out) + "\n"


def _write_env_keys(updates: dict) -> None:
    """Rewrites the given keys in .env.local (a value of None deletes that
    line), preserving every other line untouched, via an atomic same-directory
    replacement."""
    _ENV_FILE.touch(exist_ok=True)
    staged = _ENV_FILE.with_name(_ENV_FILE.name + f".write-{os.getpid()}-{os.urandom(4).hex()}")
    staged.write_text(_render_env_content(updates))
    os.chmod(staged, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(staged, _ENV_FILE)
    _restrict_env_file_permissions()


def stage_env_update(updates: dict) -> pathlib.Path:
    """Writes the post-update .env.local content to a temp file in the same
    directory (so promote_staged_update's rename is same-filesystem, hence
    atomic) without touching the real file yet. Use this when the env
    update must only become visible after some other resource (e.g. a
    database commit) has already succeeded — staging first means that if
    disk is full or permissions are wrong, nothing has changed at all."""
    _ENV_FILE.touch(exist_ok=True)
    content = _render_env_content(updates)
    staged = _ENV_FILE.with_name(_ENV_FILE.name + f".staged-{os.getpid()}-{os.urandom(4).hex()}")
    staged.write_text(content)
    try:
        os.chmod(staged, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return staged


def promote_staged_update(staged: pathlib.Path) -> None:
    """Atomically swaps a staged file (from stage_env_update) into place —
    a single os.replace() syscall, the smallest failure window a plain-file
    update can have. Only call this after the paired resource has already
    committed successfully."""
    os.replace(staged, _ENV_FILE)
    _restrict_env_file_permissions()


def discard_staged_update(staged: pathlib.Path) -> None:
    try:
        staged.unlink(missing_ok=True)
    except OSError:
        pass


def _read_value_from_file(path: pathlib.Path, name: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{name}="
    for line in path.read_text().splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def stage_migration_update(updates: dict) -> pathlib.Path:
    """Create a deterministic, restart-discoverable migration file.

    If the database commit succeeds but promotion fails or the process dies,
    the next unlock can derive the new key from this salt and finish the
    promotion instead of trying the obsolete legacy key against migrated
    ciphertext.
    """
    _ENV_FILE.touch(exist_ok=True)
    temp = _PENDING_MIGRATION_FILE.with_name(
        _PENDING_MIGRATION_FILE.name + f".write-{os.getpid()}-{os.urandom(4).hex()}"
    )
    temp.write_text(_render_env_content(updates))
    os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temp, _PENDING_MIGRATION_FILE)
    return _PENDING_MIGRATION_FILE


def has_pending_migration() -> bool:
    return (
        bool(_read_value_from_file(_PENDING_MIGRATION_FILE, "SARA_MASTER_SALT"))
        and bool(_read_value_from_file(_PENDING_MIGRATION_FILE, "SARA_MASTER_VERIFIER"))
    )


def verify_pending_migration(candidate: str) -> bytes | None:
    salt_hex = _read_value_from_file(_PENDING_MIGRATION_FILE, "SARA_MASTER_SALT")
    verifier = _read_value_from_file(_PENDING_MIGRATION_FILE, "SARA_MASTER_VERIFIER")
    if not salt_hex or not verifier:
        return None
    key = _scrypt_key(candidate, bytes.fromhex(salt_hex))
    return key if hmac.compare_digest(_verifier_for(key), verifier) else None


def promote_pending_migration() -> None:
    os.replace(_PENDING_MIGRATION_FILE, _ENV_FILE)
    _restrict_env_file_permissions()


def discard_pending_migration() -> None:
    discard_staged_update(_PENDING_MIGRATION_FILE)


def has_new_format() -> bool:
    return bool(_read_env_value("SARA_MASTER_SALT")) and bool(_read_env_value("SARA_MASTER_VERIFIER"))


def has_legacy_format() -> bool:
    return bool(_read_env_key())


def is_configured() -> bool:
    return has_new_format() or has_legacy_format()


_MIN_PASSPHRASE_LEN = 8


def _validate_new_passphrase(candidate: str) -> None:
    """setup_new derives a key from whatever it's given — a blank or
    whitespace-only string still produces a valid (trivially guessable)
    scrypt key with no error, since _scrypt_key strips before encoding.
    This is the only place that needs the check: verify_new/verify_legacy
    just fail to match an already-validated verifier, they don't need to
    re-validate the candidate's shape."""
    stripped = (candidate or "").strip()
    if len(stripped) < _MIN_PASSPHRASE_LEN:
        raise ValueError(
            f"Passphrase must be at least {_MIN_PASSPHRASE_LEN} characters (not counting "
            f"leading/trailing whitespace)."
        )


def setup_new(candidate: str) -> bytes:
    """Fresh setup (no prior passphrase at all): derives the key via scrypt
    with a new random salt and persists only the salt + a verifier — the
    key itself is returned for the caller to hold in memory only, never
    written to disk. This is what closes the finding that a stolen
    .env.local + DB used to be enough to decrypt every wallet with no
    passphrase needed at all."""
    _validate_new_passphrase(candidate)
    salt = os.urandom(16)
    key = _scrypt_key(candidate, salt)
    _write_env_keys({
        "SARA_MASTER_SALT": salt.hex(),
        "SARA_MASTER_VERIFIER": _verifier_for(key),
    })
    return key


def verify_new(candidate: str) -> bytes | None:
    """Returns the derived key if candidate matches the persisted
    (new-format) verifier, else None."""
    salt_hex = _read_env_value("SARA_MASTER_SALT")
    verifier = _read_env_value("SARA_MASTER_VERIFIER")
    if not salt_hex or not verifier:
        return None
    key = _scrypt_key(candidate, bytes.fromhex(salt_hex))
    if not hmac.compare_digest(_verifier_for(key), verifier):
        return None
    return key


def verify_legacy(candidate: str) -> bytes | None:
    """Checks candidate against the legacy unsalted-SHA-256 format. Returns
    the OLD key bytes (the same bytes every existing wallet is currently
    encrypted with) so the caller can re-encrypt off of it, since it
    becomes unrecoverable from disk once migration completes."""
    persisted = _read_env_key()
    if not persisted:
        return None
    candidate_hex = normalize_master_key(candidate)
    if not hmac.compare_digest(candidate_hex, persisted.lower()):
        return None
    return bytes.fromhex(candidate_hex)


def _master_key() -> bytes:
    from app.tools.wallet import lock
    return lock.get_active_key()


def encrypt_with_key(plaintext: str, key: bytes) -> str:
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return (nonce + ct).hex()


def decrypt_with_key(blob_hex: str, key: bytes) -> str:
    data = bytes.fromhex(blob_hex)
    nonce, ct = data[:12], data[12:]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ct, None).decode()
    except InvalidTag as exc:
        raise ValueError(
            "wallet private key cannot be decrypted with the current SARA_MASTER_KEY. "
            "Restore the original key or re-import this wallet."
        ) from exc


def encrypt_key(plaintext: str) -> str:
    return encrypt_with_key(plaintext, _master_key())


def decrypt_key(blob_hex: str) -> str:
    return decrypt_with_key(blob_hex, _master_key())
