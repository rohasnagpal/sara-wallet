# Codex Review Prompt

Copy everything below into Codex.

---

## Context

You are reviewing **Sara Wallet**, an open-source, local-first, AI-native crypto wallet. It runs entirely on the user's own machine: a single-file `index.html` frontend (no build step, vanilla JS) talking to a Python FastAPI backend (`backend/`) on `127.0.0.1:8888`, backed by SQLite. Private keys are encrypted (AES-256-GCM) and never leave the machine. Sara specializes in **stablecoin payments and cross-border remittances** — send/receive/bridge USDC/USDT across Ethereum, Arbitrum, Base, Polygon, Optimism, BNB Smart Chain, Avalanche, Solana, and Tron, controlled via a natural-language chat interface backed by a deterministic regex/intent-detection layer (an LLM is only used for free-form chat that layer doesn't handle).

This is a real wallet that signs and broadcasts real transactions with real funds. Treat every code path that touches private keys, transaction construction, or fund transfer as high-stakes.

Do a **thorough, structured review** covering functional correctness, security, and code quality. Prioritize depth over breadth — it's fine to skip low-risk cosmetic code to spend more time on money-moving paths.

## Architecture map

```
sara-wallet/
├── index.html                          # entire frontend: UI + chat + all JS
└── backend/
    ├── main.py                         # FastAPI app, lifespan, CORS, router registration, one-time SQLite migrations
    └── app/
        ├── routers/                    # chat.py (largest file — intent regex + tool dispatch), wallets.py, payments.py, portfolio.py, tokens.py, settings.py, address_book.py, market.py, intelligence.py, lock.py
        ├── chains/                     # evm.py, solana.py, tron.py — chain-specific key derivation, balance, raw tx building/signing/broadcasting
        ├── tools/
        │   ├── wallet/                 # encrypt.py (AES-GCM), lock.py (passphrase/session), send.py, balance.py, keygen.py, token_trust.py (typo-correction), tokens.py (Alchemy ERC-20 balances)
        │   ├── market/                 # paraswap.py (EVM swaps + trusted token list), jupiter.py (Solana swaps + trusted token list), coingecko.py, yfinance_tool.py, gas_tracker.py, cryptopanic.py, sentiment.py
        │   ├── trading/                # lifi.py (cross-chain bridging)
        │   ├── payments/               # links.py (payment-request payload encode/decode + creation), reconcile.py (on-chain auto-reconciliation)
        │   └── names/                  # ENS/SNS/bName resolution
        ├── db/models.py                # SQLAlchemy models: Wallet, AddressBook, Transaction, PaymentRequest, ChatMessage, Config
        └── llm/                        # prompts.py (system prompt), litellm_client.py (OpenRouter)
```

## Specific areas to scrutinize

### 1. Key management & encryption (`app/tools/wallet/encrypt.py`, `lock.py`)
- Is the AES-256-GCM implementation correct (nonce uniqueness/reuse, authenticated-encryption tag verification, key derivation from passphrase)?
- `lock.py` implements a MetaMask-style unlock session (in-memory key, auto-expiring after inactivity). Check for timing attacks on passphrase comparison, session-fixation issues, and whether the "locked" state is enforced consistently everywhere it should be (every endpoint that calls `decrypt_key`/`encrypt_key`).
- `.env`/`.env.local` handling — is the master key or any derived secret ever logged, returned in an API response, or written somewhere it shouldn't be?

### 2. Chain integrations — transaction construction & signing (`app/chains/evm.py`, `solana.py`, `tron.py`)
- `evm.py` hand-encodes ERC-20 `transfer(address,uint256)` calldata manually (selector + padded params) rather than using a full ABI encoder — verify the hex encoding/padding is correct for edge cases (zero amounts, max uint256, addresses with leading zero bytes).
- `tron.py` is a newer, more unusual integration: it avoids `tronpy`'s `get_contract()` (which needs an authenticated TronGrid endpoint) and instead hand-builds `TriggerSmartContract` transactions with raw hex parameters, using the same manual-encoding style as `evm.py`. Check this construction carefully — a subtly wrong parameter encoding could cause a transaction to send to the wrong address or wrong amount rather than just failing.
- Check that native-asset vs. token-transfer code paths can't be confused (e.g. a bug that sends native TRX when the user asked for TRC20 USDT, or vice versa).
- Check gas/fee estimation logic (`get_native_transfer_preview`, `get_erc20_transfer_preview`, `get_trc20_transfer_preview`) for off-by-one or insufficient-buffer bugs that could cause a broadcast transaction to fail after the user already confirmed based on a preview that said it would succeed.

### 3. Trusted-token allowlisting (`app/tools/market/paraswap.py`, `jupiter.py`, `app/chains/tron.py`, `app/tools/wallet/token_trust.py`)
- Sara deliberately resolves token symbols only against small, hardcoded, developer-curated allowlists (just each chain's native asset + USDC/USDT) specifically so a scam token sharing a real symbol (e.g. a fake "USDT" contract) can never be substituted in. Verify every code path that resolves a user-supplied token symbol to a contract address actually goes through this allowlist — look for any path that accepts an arbitrary contract address from user input, or that could be tricked into resolving to an attacker-controlled address.
- `token_trust.py` does fuzzy typo-correction (via `difflib`) against the trusted symbol list — verify the similarity threshold can't be abused to "correct" a legitimately different token into the wrong one (this must only fix spelling of the *same* asset, never substitute a different asset).
- Cross-check `app/routers/tokens.py` (the `/api/tokens/trusted` endpoint that powers the UI's trust display) actually reflects the same live data the transaction-signing code paths use — a stale or hand-duplicated list would be a real vulnerability (UI shows one thing, code trusts another).

### 4. Payment links & reconciliation (`app/tools/payments/links.py`, `reconcile.py`, `app/routers/payments.py`)
- Payment-request "links" are a base64url-encoded, unsigned, unauthenticated JSON payload (`{ref, to, chain, network, token, amount, note}`) embedded in a URL query param — there is no signature or integrity check. Assess whether this is exploitable: e.g. can a malicious link be crafted to trick a user's own Sara instance into doing something unintended when opened? Does the frontend correctly treat every field from a scanned/opened payment link as untrusted input?
- `reconcile.py` calls external, unauthenticated (or optionally API-keyed) blockchain-explorer APIs (Alchemy `alchemy_getAssetTransfers`, TronGrid REST, Solana RPC `get_signatures_for_address`/`get_transaction`) to detect incoming transfers matching a payment request, matching by contract/mint address (not display name) and by amount + timestamp threshold (`amount * 0.999`, transfers after `created_at`). Check for: race conditions or replay (could an old, already-matched transfer be matched twice, or against two different pending requests for the same amount?), whether the amount-matching tolerance could allow a smaller unrelated payment to incorrectly mark a request "paid", and whether errors from these external APIs are handled safely (fail-closed, not fail-open).
- `GET /api/payments/qr` takes an arbitrary `data` query param (max 2000 chars) and returns whatever QR the caller asks for — confirm there's no SSRF/injection angle and that this can't be abused to encode something malicious that gets scanned by another instance of Sara.

### 5. Chat intent-detection layer (`app/routers/chat.py` — this is the largest and most complex file)
- This file uses regex-based parsing to detect "send X to Y", "swap A for B", "bridge...", "payment link for...", etc., matched against `msg.lower()`. Look for:
  - **Regex/substring collision bugs**: earlier in this codebase's history, phrases like "payment link" (contains "link", a token symbol) or "polymarket" (contains "pol", a chain-alias) caused an intent to be silently misrouted to the wrong handler. Search for other places a legitimate phrase could substring-match an unrelated keyword and hijack the intent.
  - **Recipient-address case handling**: the send-parsing regex was fixed once already to stop lowercasing case-sensitive base58 addresses (Solana/Tron) by matching against the original-case message instead of the lowercased one — verify this fix is complete and no other regex in the file still operates on the lowercased string in a way that could corrupt a case-sensitive value (addresses, tx hashes, etc.).
  - **Wallet/chain confusion**: multiple places filter "compatible wallets" for a given network/chain — confirm the 3-way EVM/Solana/Tron branching is applied *everywhere* a wallet is selected for a chain-specific action (a prior bug here silently matched Tron intents against EVM-only wallet lists).
  - The `_pending` dict pattern requires the user to type `CONFIRM` before any money-moving action executes — verify every code path that ends in an actual broadcast is gated behind this, with no way to skip straight from intent-detection to execution.
  - A `reference` field (linking an outgoing send to a payment request) is threaded through several dict-rebuilding code paths (initial detection → pending → wallet-choice follow-up → final execution). Verify it survives every rebuild and never leaks onto an unrelated transaction.

### 6. General backend hygiene
- CORS is restricted to `localhost`/`127.0.0.1` origins in `main.py` — confirm this can't be bypassed and that no endpoint is reachable in a way that defeats the purpose (e.g. a misconfigured wildcard elsewhere).
- Look for injection risks in any endpoint that builds external API URLs/queries from user input (Alchemy, TronGrid, CoinGecko, Paraswap, Jupiter, LI.FI calls).
- Check the one-time SQLite migrations in `main.py` (`_add_column_if_missing`) for injection risk (table/column names are interpolated into raw SQL — confirm they're always hardcoded, never user-influenced) and for correctness on a fresh vs. pre-existing database.
- Check `app/tools/wallet/tokens.py` and any other Alchemy-calling code for API-key handling — is the key ever exposed to the frontend or logged?

### 7. Frontend (`index.html`)
- All chat-bot messages are inserted via `innerHTML` (not text-escaped) to allow rich formatting (clickable addresses, embedded QR images, inline forms). Confirm no user-controllable data (recipient addresses, notes on payment requests, wallet names) can end up in that HTML unescaped in a way that enables XSS — a malicious payment-request `note` field, in particular, flows from an external, unauthenticated payload straight into a chat bubble's `innerHTML`.
- Confirm the payment-request "Review in Chat" flow and the inline payment-link creation form validate input the same way the backend does, and that the backend re-validates everything (never trust client-side checks alone).

## Deliverable

Produce a findings report ranked by severity (Critical / High / Medium / Low), each with: file:line reference, a concrete failure scenario (what input/state triggers it, what goes wrong), and a suggested fix. Call out anything in section 4 (payment links/reconciliation) and section 5 (chat regex layer) as explicitly higher-attention areas given they're the newest, least battle-tested code in the repo. If you find nothing wrong in an area, say so explicitly rather than omitting it — a review that's silent on a section reads as "not checked," not "checked and clean."
