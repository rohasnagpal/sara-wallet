# Sara AI Wallet — Firefox Extension

A self-custodial crypto wallet as a Firefox extension, opened as a full
browser tab (via the toolbar icon). Unlike the main
`sara-wallet` app (a local Python backend + `index.html`), this extension has
**no backend at all** — wallet generation, encryption, and transaction signing
all happen inside the extension itself, using the browser's native WebCrypto
API and vendored signing libraries. Private keys never leave your browser.

## Scope of this v1

This is a **core wallet only** build — a separate, deliberately smaller first
milestone from the full Sara Wallet app. It does **not** include: AI chat,
voice mode, swaps, Hyperliquid perps, DeFi stats, prediction markets, or
bNames. Those live in the main app. This extension covers:

- Create or import wallets — EVM (one address, works across 7 chains) and Solana
- View native + token balances
- Send native assets and a curated list of tokens
- Passphrase-based lock/unlock, matching the main app's session model (1-hour inactivity timeout)
- Reveal/export a wallet's private key (passphrase-gated)
- Address book with nicknames

### Supported chains & tokens

| Chain | Native send | Token send |
|---|---|---|
| Ethereum | ✅ | USDC, USDT, WETH, DAI, WBTC, LINK |
| Polygon | ✅ | USDC, USDT, WETH, DAI, WBTC, LINK |
| Arbitrum | ✅ | USDC, USDT, WETH, DAI, WBTC, LINK |
| Base | ✅ | USDC, WETH, DAI |
| Optimism | ✅ | USDC, USDT, WETH, DAI, WBTC, LINK |
| BNB Smart Chain | ✅ | — (native only; no independently-verified token addresses in this repo) |
| Avalanche C-Chain | ✅ | — (native only, same reason) |
| Solana | ✅ | USDC, USDT, BONK, JUP, RAY, WIF |

## Architecture

```
extension-firefox/
├── manifest.json         # MV3, toolbar action + classic persistent background page
├── icons/                 # generated placeholder icons (lime target motif)
├── vendor/                 # prebuilt browser bundles — no CDN, no build step
│   ├── ethers.umd.min.js         # EVM signing
│   └── solana-web3.iife.min.js   # Solana signing
└── src/
    ├── crypto.js    # PBKDF2 passphrase derivation + AES-256-GCM (WebCrypto)
    ├── storage.js   # browser.storage.local schema (wallets, address book, config)
    ├── lock.js      # session lifecycle — mirrors the main app's lock.py
    ├── evm.js       # EVM wallet gen / balances / send, via ethers.js
    ├── solana.js    # Solana wallet gen / balances / send
    ├── background.js  # message router the UI talks to (only place keys are decrypted;
    │                   also opens/focuses the main.html tab on toolbar click or install)
    ├── main.html/css/js  # the UI, opened as a full tab — never touches key material directly
```

Clicking the toolbar icon opens (or focuses, if already open) `main.html` as
a normal browser tab — this is a full page, not a popup or sidebar, so it
gets the same amount of room as `index.html` in the main app.

Every wallet action from that tab goes through
`browser.runtime.sendMessage()` to `background.js`. The UI never decrypts a
key itself — that only ever happens inside the background script, in
response to an explicit message (create/import/send/export), and the
decrypted key is used and discarded immediately, never returned to the UI
except for the one-time, passphrase-gated "reveal private key" export flow.

### Why some Solana logic is hand-written

`@solana/web3.js` has an official prebuilt browser bundle, which is vendored
directly. `@solana/spl-token` (used for SPL token transfers) does **not** ship
a browser bundle, so `solana.js` reimplements the two pieces needed —
deriving an Associated Token Account address and building the SPL Token
`Transfer` instruction. Every constant and instruction layout there (program
IDs, instruction opcode, account ordering) was verified directly against the
real `@solana/spl-token` v0.4.9 source on npm, not written from memory, given
the cost of a wrong byte here is a misdirected real transfer.

## Loading it in Firefox for testing

This isn't signed or published — load it as a temporary add-on:

1. Open `about:debugging#/runtime/this-firefox` in Firefox
2. Click **Load Temporary Add-on…**
3. Select `manifest.json` inside this `extension-firefox/` folder
4. Firefox will auto-open the wallet in a new tab once on install. After
   that, open it anytime by clicking the extension's icon in the toolbar
   (clicking it again while a tab is already open just switches to that tab
   instead of opening a duplicate).

Temporary add-ons are removed when Firefox restarts — you'll need to reload
it each session during development. When you make code changes, use the
**Reload** button next to the extension's entry in `about:debugging` rather
than re-selecting `manifest.json` from scratch.

## What I could not verify myself

I don't have a way to drive an actual Firefox instance from here, so none of
this has been click-tested end-to-end. Before trusting it with real funds:

1. **Test with new, empty wallets first.** Create a wallet, note the address,
   verify it independently (e.g. check the address format looks right, or
   send a trivial test amount from another wallet you control and confirm the
   balance shows up).
2. **Test a send with a small amount first**, ideally on a cheap network
   (Polygon, or Solana) before larger amounts or expensive chains.
3. **SPL token sends are the highest-risk path to verify** — the transfer
   instruction is hand-built (see above). Test with a small amount of a
   cheap SPL token before trusting it with anything meaningful.
4. The passphrase has no recovery — same as the main app, losing it makes
   existing wallets permanently undecryptable. There is currently no seed
   phrase / mnemonic backup flow, only the one-time private-key export per
   wallet (back that up somewhere safe after creating each wallet).

## Security notes

- PBKDF2-SHA256 with 600,000 iterations (OWASP 2023 guidance) derives the
  encryption key from your passphrase; the derived key is marked
  non-extractable in WebCrypto, so even buggy code in this extension can't
  read the raw key bytes back out.
- The unlock session (derived key) lives only in the background page's
  memory, never written to disk, and expires after 1 hour of inactivity —
  same model as the main app.
- No network calls happen except read-only RPC calls (balance checks,
  broadcasting a transaction you've already signed) and only to the specific
  RPC endpoints declared in `manifest.json`'s `host_permissions`. Nothing is
  sent to any Sara-operated server, because there isn't one here.
