// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — main tab UI
// This file never touches key material directly — every wallet action
// goes through browser.runtime.sendMessage to the background script,
// which is the only place private keys are ever decrypted.
// ══════════════════════════════════════════════════════════════

async function call(type, payload = {}) {
  const res = await browser.runtime.sendMessage({ type, ...payload });
  if (res && res.error) {
    const err = new Error(res.error);
    err.locked = !!res.locked;
    throw err;
  }
  return res;
}

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

let meta = null;
let wallets = [];
let addressBook = [];
let expandedWalletId = null;
let lockMode = "loading";
let exportWalletId = null;
let sendCtx = null; // { walletId, network, token, preview }

// ── Lock / Unlock ──────────────────────────────────────────────

async function refreshLockStatus() {
  const status = await call("LOCK_STATUS");
  const lockScreen = document.getElementById("lockScreen");
  const mainApp = document.getElementById("mainApp");

  if (status.unlocked) {
    lockScreen.style.display = "none";
    mainApp.style.display = "flex";
    if (!meta) meta = await call("META");
    await loadWallets();
    await loadAddressBook();
    return;
  }

  mainApp.style.display = "none";
  lockScreen.style.display = "flex";
  const passInput = document.getElementById("lockPassInput");
  const confirmInput = document.getElementById("lockConfirmInput");
  const submitBtn = document.getElementById("lockSubmitBtn");
  const title = document.getElementById("lockTitle");
  const sub = document.getElementById("lockSub");
  passInput.value = "";
  confirmInput.value = "";
  document.getElementById("lockStatus").textContent = "";
  passInput.style.display = "";
  submitBtn.style.display = "";

  if (!status.configured) {
    lockMode = "setup";
    title.textContent = "Create a Passphrase";
    sub.textContent = "This encrypts your wallets. There is no recovery if you lose it — write it down somewhere safe.";
    confirmInput.style.display = "";
    submitBtn.textContent = "Create Passphrase";
  } else {
    lockMode = "unlock";
    title.textContent = "Unlock Sara";
    sub.textContent = "Enter your passphrase to unlock your wallets.";
    confirmInput.style.display = "none";
    submitBtn.textContent = "Unlock";
  }
  passInput.focus();
}

async function submitLockForm() {
  const passInput = document.getElementById("lockPassInput");
  const confirmInput = document.getElementById("lockConfirmInput");
  const statusEl = document.getElementById("lockStatus");
  const pass = passInput.value;
  statusEl.textContent = "";

  if (lockMode === "setup") {
    if (pass.length < 8) { statusEl.textContent = "Passphrase must be at least 8 characters."; return; }
    if (pass !== confirmInput.value) { statusEl.textContent = "Passphrases do not match."; return; }
    try {
      await call("LOCK_SETUP", { passphrase: pass });
      await refreshLockStatus();
    } catch (e) { statusEl.textContent = e.message; }
  } else {
    try {
      const res = await call("LOCK_UNLOCK", { passphrase: pass });
      if (!res.ok) { statusEl.textContent = "Incorrect passphrase."; return; }
      await refreshLockStatus();
    } catch (e) { statusEl.textContent = e.message; }
  }
}

document.getElementById("lockSubmitBtn").addEventListener("click", submitLockForm);
document.getElementById("lockPassInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitLockForm();
});
document.getElementById("lockConfirmInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitLockForm();
});
document.getElementById("lockNowBtn").addEventListener("click", async () => {
  await call("LOCK_LOCK");
  await refreshLockStatus();
});

// ── Wallet list ─────────────────────────────────────────────────

async function loadWallets() {
  try {
    wallets = await call("WALLET_LIST");
  } catch (e) {
    if (e.locked) return refreshLockStatus();
    wallets = [];
  }
  renderWalletList();
}

function networksForWallet(w) {
  return w.chain === "evm" ? meta.evmNetworks : ["solana"];
}

function nativeSymbolFor(w, network) {
  return w.chain === "evm" ? meta.evmNativeSymbol[network] : "SOL";
}

function tokensFor(w, network) {
  if (w.chain === "evm") return Object.keys(meta.evmTokens[network] || {});
  return Object.keys(meta.splTokens);
}

function renderWalletList() {
  const list = document.getElementById("walletList");
  if (!wallets.length) {
    list.innerHTML = '<div class="empty-msg">No wallets yet. Click + Add to create or import one.</div>';
    return;
  }
  list.innerHTML = wallets.map((w) => `
    <div class="wallet-card" data-wallet-id="${w.id}">
      <div class="wallet-row">
        <div class="wallet-name" data-role="name" data-action="toggle">${esc(w.name)}</div>
        <div class="wallet-addr" title="${w.address}" data-action="toggle">${w.address}</div>
        <div class="wallet-chain" data-action="toggle">${w.chain}</div>
        <div class="wallet-actions">
          <button class="wallet-icon-btn" data-action="rename" title="Rename">✎</button>
          <button class="wallet-icon-btn" data-action="export" title="Reveal private key">🔑</button>
          <button class="wallet-icon-btn" data-action="delete" title="Delete">×</button>
        </div>
      </div>
      <div class="wallet-detail ${expandedWalletId === w.id ? "open" : ""}" data-role="detail"></div>
    </div>`).join("");

  if (expandedWalletId !== null && wallets.some((w) => w.id === expandedWalletId)) {
    renderWalletDetail(expandedWalletId);
  }
}

function renderWalletDetail(id) {
  const w = wallets.find((x) => x.id === id);
  const card = document.querySelector(`.wallet-card[data-wallet-id="${id}"] [data-role="detail"]`);
  if (!w || !card) return;
  const networks = networksForWallet(w);
  card.innerHTML = `
    <div class="balance-row">
      <span class="balance-amount" id="detailBalance-${id}">…</span>
      <span class="balance-sym" id="detailSym-${id}"></span>
    </div>
    ${w.chain === "evm" ? `
    <label class="field-label">Network</label>
    <select class="input" id="detailNetwork-${id}">
      ${networks.map((n) => `<option value="${n}">${n[0].toUpperCase() + n.slice(1)}</option>`).join("")}
    </select>` : ""}
    <label class="field-label">Asset</label>
    <select class="input" id="detailAsset-${id}"></select>
    <button class="btn-primary" style="width:100%;margin-top:10px" data-action="open-send">Send</button>
  `;
  const networkSelect = document.getElementById(`detailNetwork-${id}`);
  const assetSelect = document.getElementById(`detailAsset-${id}`);

  function populateAssets() {
    const network = w.chain === "evm" ? networkSelect.value : "solana";
    const nativeSym = nativeSymbolFor(w, network);
    const tokens = tokensFor(w, network);
    assetSelect.innerHTML =
      `<option value="">${nativeSym} (native)</option>` +
      tokens.map((t) => `<option value="${t}">${t}</option>`).join("");
    refreshDetailBalance(id, network, "");
  }

  if (networkSelect) networkSelect.addEventListener("change", populateAssets);
  assetSelect.addEventListener("change", () => {
    const network = w.chain === "evm" ? networkSelect.value : "solana";
    refreshDetailBalance(id, network, assetSelect.value);
  });
  populateAssets();
}

async function refreshDetailBalance(id, network, token) {
  const balEl = document.getElementById(`detailBalance-${id}`);
  const symEl = document.getElementById(`detailSym-${id}`);
  if (!balEl) return;
  balEl.textContent = "…";
  try {
    const res = await call("WALLET_BALANCE", { id, network, token: token || undefined });
    balEl.textContent = res.balance === null ? "0" : String(res.balance);
    symEl.textContent = res.symbol;
  } catch (e) {
    if (e.locked) return refreshLockStatus();
    balEl.textContent = "err";
    symEl.textContent = "";
  }
}

document.getElementById("walletList").addEventListener("click", async (e) => {
  const card = e.target.closest(".wallet-card");
  if (!card) return;
  const id = parseInt(card.dataset.walletId, 10);
  const actionBtn = e.target.closest("[data-action]");
  const action = actionBtn ? actionBtn.dataset.action : null;

  if (action === "toggle" || !action) {
    expandedWalletId = expandedWalletId === id ? null : id;
    renderWalletList();
    return;
  }
  if (action === "rename") return startRenameWallet(card, id);
  if (action === "export") return openExportModal(id);
  if (action === "delete") return handleDeleteWalletClick(actionBtn, id);
  if (action === "open-send") return openSendForm(id);
});

function startRenameWallet(card, id) {
  const nameEl = card.querySelector('[data-role="name"]');
  if (nameEl.querySelector("input")) return;
  const w = wallets.find((x) => x.id === id);
  nameEl.innerHTML = `<input class="input" style="padding:3px 6px;font-size:12px" maxlength="32">`;
  const input = nameEl.querySelector("input");
  input.value = w.name;
  input.focus();
  input.select();
  let handled = false;
  input.addEventListener("click", (e) => e.stopPropagation());
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") { handled = true; saveRename(id, input.value.trim()); }
    if (e.key === "Escape") { handled = true; loadWallets(); }
  });
  input.addEventListener("blur", () => {
    if (!handled) { handled = true; saveRename(id, input.value.trim()); }
  });
}

async function saveRename(id, newName) {
  if (!newName) { await loadWallets(); return; }
  try {
    await call("WALLET_RENAME", { id, name: newName });
  } catch (e) {}
  await loadWallets();
}

function handleDeleteWalletClick(btn, id) {
  if (btn.dataset.confirming) {
    deleteWallet(id);
    return;
  }
  btn.dataset.confirming = "1";
  btn.textContent = "✓";
  btn.style.color = "var(--red)";
  btn._revert = setTimeout(() => {
    delete btn.dataset.confirming;
    btn.textContent = "×";
    btn.style.color = "";
  }, 3000);
}

async function deleteWallet(id) {
  try {
    await call("WALLET_DELETE", { id });
  } catch (e) {}
  if (expandedWalletId === id) expandedWalletId = null;
  await loadWallets();
}

// ── Add wallet ──────────────────────────────────────────────────

let addWalletTab = "create";

document.getElementById("addWalletBtn").addEventListener("click", () => {
  document.getElementById("sendForm").style.display = "none";
  sendCtx = null;
  document.getElementById("addWalletForm").style.display = "block";
  document.getElementById("newWalletName").value = "";
  document.getElementById("newWalletKey").value = "";
  document.getElementById("addWalletStatus").textContent = "";
  setAddWalletTab("create");
});
document.getElementById("cancelAddWalletBtn").addEventListener("click", () => {
  document.getElementById("addWalletForm").style.display = "none";
});
document.getElementById("tabCreate").addEventListener("click", () => setAddWalletTab("create"));
document.getElementById("tabImport").addEventListener("click", () => setAddWalletTab("import"));

function setAddWalletTab(tab) {
  addWalletTab = tab;
  document.getElementById("tabCreate").classList.toggle("active", tab === "create");
  document.getElementById("tabImport").classList.toggle("active", tab === "import");
  document.getElementById("importKeyGroup").style.display = tab === "import" ? "block" : "none";
  document.getElementById("submitAddWalletBtn").textContent = tab === "create" ? "Create Wallet" : "Import Wallet";
}

document.getElementById("submitAddWalletBtn").addEventListener("click", async () => {
  const name = document.getElementById("newWalletName").value.trim();
  const chain = document.getElementById("newWalletChain").value;
  const statusEl = document.getElementById("addWalletStatus");
  const btn = document.getElementById("submitAddWalletBtn");
  if (!name) { statusEl.textContent = "Wallet name is required."; return; }
  btn.disabled = true; btn.textContent = "Working…";
  try {
    if (addWalletTab === "create") {
      await call("WALLET_CREATE", { name, chain });
    } else {
      const privateKey = document.getElementById("newWalletKey").value.trim();
      if (!privateKey) { statusEl.textContent = "Private key is required."; btn.disabled = false; setAddWalletTab(addWalletTab); return; }
      await call("WALLET_IMPORT", { name, chain, privateKey });
    }
    document.getElementById("addWalletForm").style.display = "none";
    await loadWallets();
  } catch (e) {
    if (e.locked) { await refreshLockStatus(); return; }
    statusEl.textContent = e.message;
  }
  btn.disabled = false;
  setAddWalletTab(addWalletTab);
});

// ── Export private key ─────────────────────────────────────────

function openExportModal(id) {
  exportWalletId = id;
  document.getElementById("exportPassInput").value = "";
  document.getElementById("exportStatus").textContent = "";
  document.getElementById("exportKeyOutput").value = "";
  document.getElementById("exportCopyStatus").textContent = "";
  document.getElementById("exportStepPass").style.display = "";
  document.getElementById("exportStepKey").style.display = "none";
  document.getElementById("exportModal").classList.add("open");
  document.getElementById("exportPassInput").focus();
}

function closeExportModal() {
  document.getElementById("exportModal").classList.remove("open");
  document.getElementById("exportPassInput").value = "";
  document.getElementById("exportKeyOutput").value = "";
  exportWalletId = null;
}

document.getElementById("cancelExportBtn").addEventListener("click", closeExportModal);
document.getElementById("doneExportBtn").addEventListener("click", closeExportModal);
document.getElementById("exportModal").addEventListener("click", (e) => {
  if (e.target.id === "exportModal") closeExportModal();
});

document.getElementById("submitExportBtn").addEventListener("click", async () => {
  const passphrase = document.getElementById("exportPassInput").value;
  const statusEl = document.getElementById("exportStatus");
  const btn = document.getElementById("submitExportBtn");
  if (!passphrase) { statusEl.textContent = "Enter your passphrase."; return; }
  btn.disabled = true; btn.textContent = "Verifying…";
  try {
    const res = await call("WALLET_EXPORT", { id: exportWalletId, passphrase });
    document.getElementById("exportKeyOutput").value = res.private_key;
    document.getElementById("exportStepPass").style.display = "none";
    document.getElementById("exportStepKey").style.display = "";
  } catch (e) {
    statusEl.textContent = e.message;
  }
  btn.disabled = false; btn.textContent = "Reveal";
});

document.getElementById("copyExportBtn").addEventListener("click", () => {
  const output = document.getElementById("exportKeyOutput");
  navigator.clipboard.writeText(output.value).then(() => {
    const s = document.getElementById("exportCopyStatus");
    s.textContent = "Copied to clipboard.";
    s.className = "status-msg ok";
    setTimeout(() => { s.textContent = ""; }, 2500);
  });
});

// ── Send flow ───────────────────────────────────────────────────

function openSendForm(id) {
  const w = wallets.find((x) => x.id === id);
  sendCtx = { walletId: id };
  document.getElementById("sendFromLabel").textContent = "from " + w.name;
  document.getElementById("sendToInput").value = "";
  document.getElementById("sendAmountInput").value = "";
  document.getElementById("sendStatus").textContent = "";
  document.getElementById("sendConfirmStatus").textContent = "";
  document.getElementById("sendPreviewStep").style.display = "";
  document.getElementById("sendConfirmStep").style.display = "none";

  const networkSelect = document.getElementById("sendNetworkSelect");
  const assetSelect = document.getElementById("sendAssetSelect");
  const networks = networksForWallet(w);
  networkSelect.style.display = w.chain === "evm" ? "" : "none";
  networkSelect.innerHTML = networks.map((n) => `<option value="${n}">${n[0].toUpperCase() + n.slice(1)}</option>`).join("");

  function populateAssets() {
    const network = w.chain === "evm" ? networkSelect.value : "solana";
    const nativeSym = nativeSymbolFor(w, network);
    const tokens = tokensFor(w, network);
    assetSelect.innerHTML =
      `<option value="">${nativeSym} (native)</option>` +
      tokens.map((t) => `<option value="${t}">${t}</option>`).join("");
  }
  networkSelect.onchange = populateAssets;
  populateAssets();

  document.getElementById("sendForm").style.display = "block";
  document.getElementById("addWalletForm").style.display = "none";
}

document.getElementById("cancelSendBtn").addEventListener("click", () => {
  document.getElementById("sendForm").style.display = "none";
  sendCtx = null;
});

document.getElementById("previewSendBtn").addEventListener("click", async () => {
  const w = wallets.find((x) => x.id === sendCtx.walletId);
  const network = w.chain === "evm" ? document.getElementById("sendNetworkSelect").value : undefined;
  const token = document.getElementById("sendAssetSelect").value || undefined;
  let to = document.getElementById("sendToInput").value.trim();
  const amount = parseFloat(document.getElementById("sendAmountInput").value);
  const statusEl = document.getElementById("sendStatus");
  statusEl.textContent = "";

  if (!to) { statusEl.textContent = "Recipient is required."; return; }
  const nick = addressBook.find((e) => e.nickname.toLowerCase() === to.toLowerCase());
  if (nick) to = nick.address;
  if (!amount || amount <= 0) { statusEl.textContent = "Enter a valid amount."; return; }

  const btn = document.getElementById("previewSendBtn");
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    const preview = await call("SEND_PREVIEW", { id: sendCtx.walletId, network, token, to, amount });
    sendCtx = { ...sendCtx, network, token, to, amount, preview };
    const box = document.getElementById("sendPreviewBox");
    box.innerHTML =
      `Send <b>${amount} ${preview.symbol}</b><br>` +
      `To: <b>${to.slice(0, 6)}…${to.slice(-4)}</b><br>` +
      (network ? `Network: <b>${network}</b><br>` : "") +
      (preview.estGasNative ? `Est. gas: <b>${preview.estGasNative.toFixed(6)}</b><br>` : "");
    document.getElementById("sendPreviewStep").style.display = "none";
    document.getElementById("sendConfirmStep").style.display = "";
  } catch (e) {
    if (e.locked) { await refreshLockStatus(); return; }
    statusEl.textContent = e.message;
  }
  btn.disabled = false; btn.textContent = "Preview";
});

document.getElementById("backSendBtn").addEventListener("click", () => {
  document.getElementById("sendPreviewStep").style.display = "";
  document.getElementById("sendConfirmStep").style.display = "none";
});

document.getElementById("confirmSendBtn").addEventListener("click", async () => {
  const statusEl = document.getElementById("sendConfirmStatus");
  const btn = document.getElementById("confirmSendBtn");
  statusEl.textContent = "";
  btn.disabled = true; btn.textContent = "Sending…";
  try {
    const res = await call("SEND_EXECUTE", {
      id: sendCtx.walletId, network: sendCtx.network, token: sendCtx.token,
      to: sendCtx.to, amount: sendCtx.amount,
    });
    statusEl.textContent = "Sent! Tx: " + res.tx_hash.slice(0, 12) + "…";
    statusEl.className = "status-msg ok";
    setTimeout(() => {
      document.getElementById("sendForm").style.display = "none";
      sendCtx = null;
      if (expandedWalletId) renderWalletDetail(expandedWalletId);
    }, 2000);
  } catch (e) {
    if (e.locked) { await refreshLockStatus(); return; }
    statusEl.textContent = e.message;
    statusEl.className = "status-msg";
  }
  btn.disabled = false; btn.textContent = "Confirm & Send";
});

// ── Address book ────────────────────────────────────────────────

async function loadAddressBook() {
  try {
    addressBook = await call("ADDRESS_BOOK_LIST");
  } catch (e) {
    addressBook = [];
  }
  renderAddressBook();
}

function renderAddressBook() {
  const list = document.getElementById("dirList");
  if (!addressBook.length) {
    list.innerHTML = '<div class="empty-msg">No entries yet.</div>';
    return;
  }
  list.innerHTML = addressBook.map((e) => `
    <div class="dir-entry" data-nickname="${esc(e.nickname)}">
      <div class="dir-nick">${esc(e.nickname)}</div>
      <div class="dir-addr" title="${e.address}">${e.address}</div>
      <div class="dir-chain">${e.chain}</div>
      <button class="wallet-icon-btn" data-action="delete-dir" title="Remove">×</button>
    </div>`).join("");
}

document.getElementById("addDirBtn").addEventListener("click", async () => {
  const nickname = document.getElementById("dirNick").value.trim();
  const address = document.getElementById("dirAddr").value.trim();
  const chain = document.getElementById("dirChain").value;
  const statusEl = document.getElementById("dirStatus");
  if (!nickname || !address) { statusEl.textContent = "Nickname and address are required."; return; }
  try {
    await call("ADDRESS_BOOK_ADD", { nickname, address, chain });
    document.getElementById("dirNick").value = "";
    document.getElementById("dirAddr").value = "";
    statusEl.textContent = "Saved!";
    statusEl.className = "status-msg ok";
    setTimeout(() => { statusEl.textContent = ""; }, 2000);
    await loadAddressBook();
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-msg";
  }
});

document.getElementById("dirList").addEventListener("click", async (e) => {
  const btn = e.target.closest('[data-action="delete-dir"]');
  if (!btn) return;
  const entry = e.target.closest(".dir-entry");
  const nickname = entry.dataset.nickname;
  if (btn.dataset.confirming) {
    await call("ADDRESS_BOOK_DELETE", { nickname });
    await loadAddressBook();
    return;
  }
  btn.dataset.confirming = "1";
  btn.textContent = "✓";
  btn.style.color = "var(--red)";
  setTimeout(() => {
    delete btn.dataset.confirming;
    btn.textContent = "×";
    btn.style.color = "";
  }, 3000);
});

// ── Init ────────────────────────────────────────────────────────

refreshLockStatus();
