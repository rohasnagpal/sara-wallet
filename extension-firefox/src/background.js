// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — background message router
// (the extension's equivalent of Sara's FastAPI routers: the UI tab
// sends a typed message, this dispatches to the right handler and
// returns a plain JSON-serializable result or {error} on failure.)
// ══════════════════════════════════════════════════════════════

const MAIN_URL = browser.runtime.getURL("src/main.html");

async function openOrFocusMainTab() {
  const existing = await browser.tabs.query({ url: MAIN_URL });
  if (existing.length > 0) {
    await browser.tabs.update(existing[0].id, { active: true });
  } else {
    await browser.tabs.create({ url: MAIN_URL });
  }
}

browser.action.onClicked.addListener(() => {
  openOrFocusMainTab();
});

browser.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    openOrFocusMainTab();
  }
});

async function handleMessage(msg) {
  switch (msg.type) {
    case "META": {
      return {
        evmNetworks: Object.keys(SaraEvm.EVM_RPC),
        evmNativeSymbol: SaraEvm.EVM_NATIVE_SYMBOL,
        evmTokens: SaraEvm.EVM_TOKENS,
        splTokens: SaraSolana.SPL_TOKENS,
      };
    }
    case "LOCK_STATUS": {
      const configured = await SaraLock.isConfigured();
      return { configured, unlocked: SaraLock.isUnlocked() };
    }
    case "LOCK_SETUP": {
      await SaraLock.setupPassphrase(msg.passphrase);
      return { ok: true };
    }
    case "LOCK_UNLOCK": {
      const ok = await SaraLock.unlock(msg.passphrase);
      return { ok };
    }
    case "LOCK_LOCK": {
      SaraLock.lock();
      return { ok: true };
    }

    case "WALLET_LIST": {
      const wallets = await SaraStorage.getWallets();
      return wallets.map(({ id, name, chain, address }) => ({ id, name, chain, address }));
    }

    case "WALLET_CREATE": {
      const key = SaraLock.getActiveKey(); // throws WalletLockedError if locked
      const chain = msg.chain.toLowerCase();
      let generated;
      if (chain === "evm") generated = SaraEvm.createEvmWallet();
      else if (chain === "solana") generated = SaraSolana.createSolanaWallet();
      else throw new Error("chain must be 'evm' or 'solana'");
      const encrypted_key = await SaraCrypto.encryptString(key, generated.privateKey);
      const wallet = await SaraStorage.addWallet({
        name: msg.name, chain, address: generated.address, encrypted_key,
      });
      return { id: wallet.id, name: wallet.name, chain: wallet.chain, address: wallet.address };
    }

    case "WALLET_IMPORT": {
      const key = SaraLock.getActiveKey();
      const chain = msg.chain.toLowerCase();
      let imported;
      try {
        if (chain === "evm") imported = SaraEvm.importEvmWallet(msg.privateKey);
        else if (chain === "solana") imported = SaraSolana.importSolanaWallet(msg.privateKey);
        else throw new Error("chain must be 'evm' or 'solana'");
      } catch (e) {
        throw new Error("Invalid private key for " + chain);
      }
      const encrypted_key = await SaraCrypto.encryptString(key, imported.privateKey);
      const wallet = await SaraStorage.addWallet({
        name: msg.name, chain, address: imported.address, encrypted_key,
      });
      return { id: wallet.id, name: wallet.name, chain: wallet.chain, address: wallet.address };
    }

    case "WALLET_RENAME": {
      const w = await SaraStorage.renameWallet(msg.id, msg.name);
      return { id: w.id, name: w.name, chain: w.chain, address: w.address };
    }

    case "WALLET_DELETE": {
      await SaraStorage.deleteWallet(msg.id);
      return { deleted: msg.id };
    }

    case "WALLET_EXPORT": {
      if (!msg.passphrase || !msg.passphrase.trim()) {
        throw new Error("Passphrase cannot be blank");
      }
      const wallet = await SaraStorage.getWalletById(msg.id);
      const ok = await SaraLock.unlock(msg.passphrase);
      if (!ok) throw new Error("Incorrect passphrase");
      const key = SaraLock.getActiveKey();
      const privateKey = await SaraCrypto.decryptString(key, wallet.encrypted_key);
      return { id: wallet.id, name: wallet.name, chain: wallet.chain, address: wallet.address, private_key: privateKey };
    }

    case "WALLET_BALANCE": {
      const wallet = await SaraStorage.getWalletById(msg.id);
      if (wallet.chain === "evm") {
        const network = msg.network || "ethereum";
        if (msg.token) {
          const bal = await SaraEvm.getErc20Balance(network, msg.token, wallet.address);
          return { balance: bal, symbol: msg.token, network };
        }
        const bal = await SaraEvm.getNativeBalance(network, wallet.address);
        return { balance: bal, symbol: SaraEvm.EVM_NATIVE_SYMBOL[network], network };
      } else if (wallet.chain === "solana") {
        if (msg.token) {
          const bal = await SaraSolana.getSplBalance(wallet.address, msg.token);
          return { balance: bal, symbol: msg.token };
        }
        const bal = await SaraSolana.getNativeBalance(wallet.address);
        return { balance: bal, symbol: "SOL" };
      }
      throw new Error("Unknown wallet chain");
    }

    case "SEND_PREVIEW": {
      const wallet = await SaraStorage.getWalletById(msg.id);
      if (wallet.chain === "evm") {
        const network = msg.network || "ethereum";
        if (msg.token) {
          return await SaraEvm.previewTokenSend(network, wallet.address, msg.token, msg.to, msg.amount);
        }
        return await SaraEvm.previewNativeSend(network, wallet.address, msg.to, msg.amount);
      } else if (wallet.chain === "solana") {
        if (msg.token) {
          return await SaraSolana.previewSplSend(wallet.address, msg.token, msg.to, msg.amount);
        }
        return await SaraSolana.previewNativeSend(wallet.address, msg.to, msg.amount);
      }
      throw new Error("Unknown wallet chain");
    }

    case "SEND_EXECUTE": {
      const key = SaraLock.getActiveKey();
      const wallet = await SaraStorage.getWalletById(msg.id);
      const privateKey = await SaraCrypto.decryptString(key, wallet.encrypted_key);
      let txHash;
      if (wallet.chain === "evm") {
        const network = msg.network || "ethereum";
        txHash = msg.token
          ? await SaraEvm.sendErc20(network, privateKey, msg.token, msg.to, msg.amount)
          : await SaraEvm.sendNative(network, privateKey, msg.to, msg.amount);
      } else if (wallet.chain === "solana") {
        txHash = msg.token
          ? await SaraSolana.sendSpl(privateKey, msg.token, msg.to, msg.amount)
          : await SaraSolana.sendNative(privateKey, msg.to, msg.amount);
      } else {
        throw new Error("Unknown wallet chain");
      }
      return { tx_hash: txHash };
    }

    case "ADDRESS_BOOK_LIST": {
      return SaraStorage.getAddressBook();
    }
    case "ADDRESS_BOOK_ADD": {
      await SaraStorage.addAddressBookEntry({ nickname: msg.nickname, address: msg.address, chain: msg.chain });
      return { ok: true };
    }
    case "ADDRESS_BOOK_DELETE": {
      await SaraStorage.deleteAddressBookEntry(msg.nickname);
      return { ok: true };
    }

    default:
      throw new Error("Unknown message type: " + msg.type);
  }
}

browser.runtime.onMessage.addListener((msg) => {
  return handleMessage(msg).catch((err) => ({
    error: err.message || String(err),
    locked: err instanceof SaraLock.WalletLockedError,
  }));
});
