# Security Model

Sara is built on a simple principle:

> **Your keys never leave your machine.**

This document answers the questions people actually ask before trusting a
wallet with money, then lays out the fuller security philosophy and threat
model. For how to report a vulnerability, see [SECURITY.md](../SECURITY.md).

## Trust questions, answered

### Does the AI see my private key?

No. Private keys are encrypted at rest (AES-256-GCM) and are only ever
decrypted inside local Python signing code
(`backend/app/tools/wallet/encrypt.py` and the chain-layer signing calls in
`backend/app/chains/evm.py`). That decrypted key is used immediately to
sign a transaction and is never included in any message sent to the AI
model. The AI layer (`backend/app/llm/`) has no code path that touches key
material at all — you can grep it yourself, there's nothing there.

### Who signs transactions?

Local Python code, not the AI. The model's job is limited to interpreting
your natural-language request and deciding which deterministic tool to
call with which parameters (e.g., "send 100 USDC to this address"). The
actual signing — decrypting the key, building the transaction, calling
`w3.eth.account.sign_transaction` — happens entirely in backend tool code
on your machine, after you've confirmed the action.

### What runs locally?

Everything except AI model inference (unless you point Sara at a local
Ollama model, in which case that runs locally too):

- The frontend (`index.html`, served from your own machine)
- The backend API server (FastAPI, `backend/main.py`)
- Your wallet database (SQLite at `backend/sara.db`)
- Private key storage and decryption
- Transaction building and signing
- The wallet lock/passphrase and session timeout

### What leaves my machine?

Only what a feature needs, and never key material. The full, honest
breakdown of who can see what is in [privacy.md](privacy.md). In short:

- **Your chat messages and tool results** go to whichever AI provider you
  configure (OpenRouter, OpenAI, Anthropic, Groq, xAI, Gemini, Cloudflare,
  or a local Ollama model you run yourself, in which case nothing leaves
  your machine for inference). This can include amounts, recipient names or
  addresses and balances, because the model needs them to hold a
  conversation about them. It never includes your private key, seed phrase
  or passphrase.
- **Signed transactions** are broadcast to the public blockchain network
  you're using, same as any other wallet.
- **Public blockchain nodes** see the addresses Sara looks up (balances,
  allowances, address screening) and your IP address. You can point Sara at
  your own node.
- **Swap and bridge services** (LI.FI, ParaSwap) see your wallet address, and
  Alchemy sees the transaction you are about to sign, because Sara verifies
  every swap and bridge before signing and refuses without an Alchemy key.
- **Optional services** you configure, such as CoinGecko prices or Telegram
  alerts, receive only what their feature needs.
- No telemetry, no analytics, no cloud sync, no account, no external key
  custody, and the app page itself loads nothing from third parties.

See [third-party-services.md](third-party-services.md) for the complete,
code-verified list of every external service Sara connects to, and
Sara's own no-markup policy on swaps/bridges/sends/x402 payments.

### Can the AI send money itself?

Not without your say-so. Every send requires you to explicitly confirm
(type `CONFIRM` and your passphrase) before anything is signed — the model
proposes an action, it doesn't execute one unilaterally.

The one deliberate exception is **x402 policy-gated auto-pay**: you can
configure a spending policy (scoped to a wallet/network, with a cap) that
lets Sara pay for machine-priced HTTP resources without a passphrase
prompt each time, so an unattended agent flow can work. This only applies
within the policy you explicitly configured in advance — anything outside
it falls back to a normal passphrase-confirmed send. See
[x402.md](x402.md).

## Security Philosophy

- Private keys are encrypted and stored locally
- Sara locks like a normal wallet — passphrase required to unlock,
  auto-locks after 1 hour of inactivity
- Swaps and bridges are verified before signing: Sara simulates the
  transaction (or checks the aggregator's own quote/result) and refuses to
  sign if it would move more than the confirmed input amount — it doesn't
  trust calldata blindly
- Batch and token amounts are signed from exact integer base units; crash
  recovery reuses persisted signed transaction bytes instead of creating a
  second payment
- Spending limits are enforced at preview and again immediately before chat,
  token, batch and x402 sends; time windows use the policy's configured IANA
  timezone
- Payment batches are validated and shown to you in full before anything is
  signed, and sending one asks for your passphrase
- Risk screening can be configured to fail closed, and provider evidence is
  stored as bounded identifiers rather than allegation text
- Sara Names records use EIP-712 signatures, content hashes,
  sequence/epoch replay protection and live on-chain ownership checks
- Token symbols only ever resolve to a hardcoded, developer-verified
  contract address list — never an arbitrary on-chain lookup
- No telemetry, no cloud sync, no external key custody (see [privacy.md](privacy.md))
- Open source — read every line, audit everything
- You own your wallet code

## Audit Status & Reporting

Sara Wallet has **not** undergone a third-party professional security
audit — see [SECURITY.md](../SECURITY.md) for the full audit-status
disclosure, supported versions, and how to privately report a
vulnerability.
