// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — unlock session lifecycle
// Mirrors the local app's lock model: locked by default, a passphrase
// derives an in-memory-only AES key, and the session auto-expires after
// 1 hour of inactivity. Nothing here is ever written to disk.
// ══════════════════════════════════════════════════════════════

const SESSION_TIMEOUT_MS = 60 * 60 * 1000;

let _sessionKey = null;
let _lastActivity = 0;

class WalletLockedError extends Error {}

async function isConfigured() {
  return SaraStorage.isConfigured();
}

function isUnlocked() {
  return _sessionKey !== null && Date.now() - _lastActivity < SESSION_TIMEOUT_MS;
}

function touch() {
  if (isUnlocked()) _lastActivity = Date.now();
}

function getActiveKey() {
  if (!isUnlocked()) {
    throw new WalletLockedError("Wallet is locked. Unlock Sara with your passphrase first.");
  }
  _lastActivity = Date.now();
  return _sessionKey;
}

async function setupPassphrase(passphrase) {
  if (await isConfigured()) {
    throw new WalletLockedError("A passphrase is already set. Use unlock instead.");
  }
  if (!passphrase || passphrase.length < 8) {
    throw new Error("Passphrase must be at least 8 characters.");
  }
  const salt = SaraCrypto.randomBytes(16);
  const iterations = SaraCrypto.PBKDF2_ITERATIONS;
  const key = await SaraCrypto.deriveKey(passphrase, salt, iterations);
  const verifier = await SaraCrypto.makeVerifier(key);
  await SaraStorage.setConfig({
    salt: SaraCrypto.bytesToHex(salt),
    iterations,
    verifier,
  });
  _sessionKey = key;
  _lastActivity = Date.now();
}

async function unlock(passphrase) {
  const config = await SaraStorage.getConfig();
  if (!config) throw new WalletLockedError("No passphrase has been set up yet.");
  const key = await SaraCrypto.verifyPassphrase(
    passphrase,
    config.salt,
    config.iterations,
    config.verifier
  );
  if (!key) return false;
  _sessionKey = key;
  _lastActivity = Date.now();
  return true;
}

function lock() {
  _sessionKey = null;
}

self.SaraLock = {
  WalletLockedError,
  isConfigured,
  isUnlocked,
  touch,
  getActiveKey,
  setupPassphrase,
  unlock,
  lock,
};
