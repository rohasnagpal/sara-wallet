import os, json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _master_key() -> bytes:
    raw = os.getenv("SARA_MASTER_KEY", "")
    if not raw:
        # derive a stable key from a fixed default so dev works without config
        # In production set SARA_MASTER_KEY to 32 random bytes hex-encoded
        raw = "sara-dev-key-please-set-in-env-00"
    key = raw.encode()[:32].ljust(32, b"0")
    return key

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
