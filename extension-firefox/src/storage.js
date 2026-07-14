// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — browser.storage.local schema and helpers
// (the extension's equivalent of Sara's local SQLite database)
// ══════════════════════════════════════════════════════════════

const KEYS = {
  CONFIG: "sara_config", // { salt, iterations, verifier } — never the raw key
  WALLETS: "sara_wallets", // [{ id, name, chain, address, encrypted_key }]
  ADDRESS_BOOK: "sara_address_book", // [{ nickname, address, chain }]
  NEXT_WALLET_ID: "sara_next_wallet_id",
};

async function getConfig() {
  const r = await browser.storage.local.get(KEYS.CONFIG);
  return r[KEYS.CONFIG] || null;
}

async function setConfig(config) {
  await browser.storage.local.set({ [KEYS.CONFIG]: config });
}

async function isConfigured() {
  return (await getConfig()) !== null;
}

async function getWallets() {
  const r = await browser.storage.local.get(KEYS.WALLETS);
  return r[KEYS.WALLETS] || [];
}

async function saveWallets(wallets) {
  await browser.storage.local.set({ [KEYS.WALLETS]: wallets });
}

async function nextWalletId() {
  const r = await browser.storage.local.get(KEYS.NEXT_WALLET_ID);
  const id = (r[KEYS.NEXT_WALLET_ID] || 0) + 1;
  await browser.storage.local.set({ [KEYS.NEXT_WALLET_ID]: id });
  return id;
}

async function addWallet({ name, chain, address, encrypted_key }) {
  const wallets = await getWallets();
  if (wallets.some((w) => w.name === name)) {
    throw new Error("Wallet name already exists");
  }
  const id = await nextWalletId();
  const wallet = { id, name, chain, address, encrypted_key };
  wallets.push(wallet);
  await saveWallets(wallets);
  return wallet;
}

async function deleteWallet(id) {
  const wallets = await getWallets();
  const next = wallets.filter((w) => w.id !== id);
  if (next.length === wallets.length) throw new Error("Wallet not found");
  await saveWallets(next);
}

async function renameWallet(id, newName) {
  const wallets = await getWallets();
  const w = wallets.find((x) => x.id === id);
  if (!w) throw new Error("Wallet not found");
  const trimmed = newName.trim();
  if (!trimmed) throw new Error("Wallet name cannot be blank");
  if (trimmed !== w.name && wallets.some((x) => x.name === trimmed)) {
    throw new Error("Wallet name already exists");
  }
  w.name = trimmed;
  await saveWallets(wallets);
  return w;
}

async function getWalletById(id) {
  const wallets = await getWallets();
  const w = wallets.find((x) => x.id === id);
  if (!w) throw new Error("Wallet not found");
  return w;
}

async function getAddressBook() {
  const r = await browser.storage.local.get(KEYS.ADDRESS_BOOK);
  return r[KEYS.ADDRESS_BOOK] || [];
}

async function saveAddressBook(entries) {
  await browser.storage.local.set({ [KEYS.ADDRESS_BOOK]: entries });
}

async function addAddressBookEntry({ nickname, address, chain }) {
  const entries = await getAddressBook();
  if (entries.some((e) => e.nickname === nickname)) {
    throw new Error("Nickname already exists");
  }
  entries.push({ nickname, address, chain });
  await saveAddressBook(entries);
}

async function deleteAddressBookEntry(nickname) {
  const entries = await getAddressBook();
  const next = entries.filter((e) => e.nickname !== nickname);
  await saveAddressBook(next);
}

self.SaraStorage = {
  getConfig,
  setConfig,
  isConfigured,
  getWallets,
  saveWallets,
  addWallet,
  deleteWallet,
  renameWallet,
  getWalletById,
  getAddressBook,
  addAddressBookEntry,
  deleteAddressBookEntry,
};
