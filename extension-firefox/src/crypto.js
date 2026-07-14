// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — crypto primitives (PBKDF2 + AES-256-GCM)
// Runs entirely on WebCrypto (crypto.subtle). No key material ever
// leaves this extension's own memory/storage.
// ══════════════════════════════════════════════════════════════

const PBKDF2_ITERATIONS = 600000; // OWASP 2023 guidance for PBKDF2-HMAC-SHA256
const VERIFIER_PLAINTEXT = "sara-wallet-v1-unlock-check";

function bytesToHex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return out;
}

function randomBytes(len) {
  return crypto.getRandomValues(new Uint8Array(len));
}

async function deriveKey(passphrase, saltBytes, iterations) {
  const enc = new TextEncoder();
  const baseKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(passphrase),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: saltBytes, iterations, hash: "SHA-256" },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false, // not extractable — raw bytes can never be read back out
    ["encrypt", "decrypt"]
  );
}

async function encryptString(key, plaintext) {
  const nonce = randomBytes(12);
  const enc = new TextEncoder();
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv: nonce },
    key,
    enc.encode(plaintext)
  );
  return bytesToHex(nonce) + bytesToHex(new Uint8Array(ciphertext));
}

async function decryptString(key, blobHex) {
  const bytes = hexToBytes(blobHex);
  const nonce = bytes.slice(0, 12);
  const ciphertext = bytes.slice(12);
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: nonce },
    key,
    ciphertext
  );
  return new TextDecoder().decode(plaintext);
}

// Derives a fresh key from a candidate passphrase and a stored salt, then
// tries to decrypt the stored verifier blob. AES-GCM's auth tag makes this a
// safe correctness check — a wrong passphrase produces a wrong key, which
// fails the tag check inside decryptString rather than silently succeeding.
async function verifyPassphrase(passphrase, saltHex, iterations, verifierBlob) {
  try {
    const key = await deriveKey(passphrase, hexToBytes(saltHex), iterations);
    const plaintext = await decryptString(key, verifierBlob);
    if (plaintext !== VERIFIER_PLAINTEXT) return null;
    return key;
  } catch (e) {
    return null;
  }
}

async function makeVerifier(key) {
  return encryptString(key, VERIFIER_PLAINTEXT);
}

self.SaraCrypto = {
  PBKDF2_ITERATIONS,
  bytesToHex,
  hexToBytes,
  randomBytes,
  deriveKey,
  encryptString,
  decryptString,
  verifyPassphrase,
  makeVerifier,
};
