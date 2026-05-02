import os, secrets, pathlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENV_FILE = pathlib.Path(__file__).parents[4] / ".env"  # sara-wallet/.env


def _master_key() -> bytes:
    raw = os.getenv("SARA_MASTER_KEY", "").strip()
    if raw:
        key = bytes.fromhex(raw) if len(raw) == 64 else raw.encode()[:32].ljust(32, b"0")
        return key[:32]
    raise RuntimeError(
        "SARA_MASTER_KEY is not set. "
        "Run the server once via 'uvicorn main:app' — it will generate and save a key automatically."
    )


def _generate_and_save_key() -> str:
    """Generate a random master key and persist it to .env."""
    key_hex = secrets.token_hex(32)  # 256-bit random key
    _ENV_FILE.touch(exist_ok=True)
    content = _ENV_FILE.read_text()
    if "SARA_MASTER_KEY" not in content:
        with _ENV_FILE.open("a") as f:
            f.write(f"\nSARA_MASTER_KEY={key_hex}\n")
    os.environ["SARA_MASTER_KEY"] = key_hex
    return key_hex


def ensure_master_key() -> None:
    """Call at startup. Generates a key if one is not already configured."""
    if os.getenv("SARA_MASTER_KEY", "").strip():
        return
    # Try loading from .env file first
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            if line.startswith("SARA_MASTER_KEY="):
                val = line.split("=", 1)[1].strip()
                if val:
                    os.environ["SARA_MASTER_KEY"] = val
                    return
    # Generate a fresh key and save it
    _generate_and_save_key()
    print(
        "\n⚠️  SARA_MASTER_KEY was not set. A random encryption key has been generated\n"
        f"   and saved to: {_ENV_FILE}\n"
        "   Keep this file safe — it protects your stored private keys.\n"
    )


def encrypt_key(plaintext: str) -> str:
    aesgcm = AESGCM(_master_key())
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return (nonce + ct).hex()


def decrypt_key(blob_hex: str) -> str:
    data = bytes.fromhex(blob_hex)
    nonce, ct = data[:12], data[12:]
    aesgcm = AESGCM(_master_key())
    return aesgcm.decrypt(nonce, ct, None).decode()
