<div align="center">

**Sara AI Wallet** is an open-source, local-first AI wallet for stablecoin payments, x402 agentic payments, business tools and Web3.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-f4a261?style=flat-square)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open-Source-e76f51?style=flat-square&logo=github)](https://github.com)
[![AI Powered](https://img.shields.io/badge/AI-Powered-264653?style=flat-square&logoColor=white)](https://github.com)
[![Runs Locally](https://img.shields.io/badge/Runs-Locally-2a9d8f?style=flat-square)](https://github.com)
[![Security checks](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml/badge.svg)](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml)

<br />
<img width="2836" height="1536" alt="image" src="https://github.com/user-attachments/assets/2f704c7e-e1c0-4187-a697-912ea5663f59" />
</div>

---

## ✦ What is Sara Wallet?

**Sara AI Wallet** is an open source, AI-powered crypto wallet that makes sending USDC as easy as sending a text message: Send 50 USDC to Maria.

Sara now combines five product layers:

**Wallet and payments**
1. **Natural-language USDC/native sends** - e.g. `send 100 USDC to rohas.sara`; Sara resolves the recipient, shows the exact amount and fee, and asks you to confirm before signing.
2. **Payment links and QR codes** - generate a shareable link or QR pre-filled with the amount, token and network, so anyone can pay you without typing an address.
3. **Invoices with automatic on-chain reconciliation** - create an invoice for a customer and Sara marks it paid itself the moment a matching transfer lands, no manual "mark as paid."
4. **Proof-of-payment receipts** - every confirmed payment gets a receipt with the amount, fiat value, fee, tx hash and status, ready to save or send.
5. **Swaps** - trade one token for another on the same network, e.g. USDC to POL, with the quoted rate shown before you sign.
6. **Bridges** - move USDC from one supported network to another, e.g. Polygon to Base, in one flow instead of a separate bridge dApp.
7. **Batch payments** - pay or airdrop many recipients in one go from a list, each sent as its own on-chain transaction.
8. **Recurring payments** - set a schedule (e.g. "every 1st of the month") that materializes a reviewable payment batch each time it's due.
9. **Crypto payroll** - run payroll for a list of employees/contractors in one action, built on top of counterparties and batches.

**Agentic payments (x402)**
1. **Pay-per-call HTTP fetches** - give Sara a URL; if it answers `402 Payment Required`, Sara pays the exact USDC price and returns the resource - no account or API key on either side.
2. **Policy-gated autonomous payment** - a spending policy scoped to a wallet/network (the same engine behind "Scoped spending controls" below) lets payments under your cap go through with no passphrase prompt, so an agent can pay for calls unattended; anything outside the policy falls back to your passphrase, same as any other send.
3. **Trusted-asset only** - Sara only ever authorizes its own developer-verified USDC contract per network, never whatever asset address a 402 response itself claims.
4. **Base, Polygon, Ethereum, Arbitrum** for real payments, plus **Base Sepolia** for free testnet trials (no real money, not recorded in your ledger) - each network's USDC/EIP-3009 support independently confirmed against x402's own asset registry, not assumed.
5. **Demo seller + LLM agent included** - `examples/x402/` has a runnable multi-resource paid site (weather/trivia/stock/recipe, each a different price) with a free catalog, and a tool-calling agent that browses the catalog, picks the resource that matches its task, and pays for only that one - see `examples/x402/README.md`.

**Business and accounting**
1. **Exact-base-unit transaction ledger** - every send/receive is recorded in the token's exact base units (no floating-point rounding), searchable by tag or note.
2. **Fiat valuation** - each transaction is snapshotted with its USD/INR value at the time it happened, for accurate reporting later.
3. **Tags and notes** - label transactions, e.g. "rent" or "invoice #42", so you can filter and categorize your ledger.
4. **Counterparties** - save vendors, employees and contractors separately from your personal address book, for use in batches and payroll.
5. **Income/expense reporting** - totals every ledger entry by category over a date range - a quick P&L across all tokens and networks.
6. **FIFO cost basis and P&L** - tracks acquisition cost lots per token and computes realized gains/losses (not tax advice) when you dispose of them.
7. **CSV/XLSX exports** - download your full ledger with fiat values, fees and classifications for your accountant or tax software.
8. **Approval workflows** - require a second, independently-generated checker credential before a sensitive payment batch executes.
9. **Scoped spending controls** - cap how much a wallet can send per transaction or per day/week/month, enforced right before signing.

**Token and safety tools**
1. **Fixed-supply or capped mintable/burnable ERC-20 creation** - deploy your own token, e.g. "Rohas Coin (RHS)", from two pinned templates: a fixed-supply coin, or an owner-mintable, holder-burnable, capped one.
2. **Mint/burn/transfer management** - mint more supply (if the template allows it), burn your own balance, or send tokens you've deployed, straight from the Deployed tokens list.
3. **Airdrops** - send the same or different amounts of a token to a batch of addresses in one flow.
4. **Allowance inspection and revocation** - see every contract you've approved to spend your tokens and revoke any that shouldn't still have access.
5. **Transaction simulation** - dry-run a contract call and see the result before it costs any gas.
6. **Verified-contract interaction** - read from or write to any verified, non-proxy contract using allowlisted methods, re-simulated right before signing.
7. **Address risk screening** - checks a destination address against sanctions/risk lists before you send it funds.
8. **Treasury monitoring** - see combined balances and exposure across every wallet and network in one view.
9. **Stablecoin route comparison** - estimates cost and time to move funds between two networks, e.g. Polygon to Arbitrum, for deciding how to rebalance.

**Sara Names**
1. **Commit/reveal registration** - reserve a name like `rohas` in two steps, commit then complete after a ~60s delay, so nobody can front-run your registration by watching the mempool.
2. **Renewals** - extend a name's expiry before it lapses; anyone can pay to renew it without changing who owns it.
3. **Transfers** - move ownership of a name, e.g. `rohas`, to a different wallet address.
4. **Subnames** - create names under one you own, e.g. `pay.rohas`, each with its own owner.
5. **EIP-712 signed multi-network address/payment-preference records** - publish a signed record so `rohas` resolves to different addresses per network (plus a preferred token/network for payments), without an on-chain transaction per update.

Backed by the Sara Names Polygon registry contract, developed and tested in a separate repo - this codebase only holds the client that talks to it (`backend/app/tools/names/`, `backend/app/routers/names.py`).

**Portfolio and wallet intelligence**
1. **Historical performance** - track total portfolio value over time across every wallet and network.
2. **Exposure** - see how holdings break down by token and network at a glance.
3. **Top-payee analysis** - "who have I sent the most to?", ranked from your own ledger, not a third-party service.
4. **Spend by category** - totals outgoing transactions by the tags/categories you've assigned them.
5. **Recurring-counterparty detection** - flags addresses you pay repeatedly, useful for spotting subscriptions or regular vendors.
6. **Unusual-activity detection** - flags transactions that look out of pattern compared to your usual activity.

**Alerts** can monitor payments, invoices, transactions and balance thresholds (e.g. "alert me if my Treasury wallet drops below 50 USDC") through Telegram, email or signed webhooks.

Sara runs locally on your laptop. The frontend is a single HTML app; the backend is a Python FastAPI server.

Sara also includes a credential-free BlockchainProof evidence vault. A user can
hash a file locally, review and authorize an exact 1 USDC Polygon checkout from
their own Sara wallet, monitor proof creation, retain the encrypted evidence ZIP
locally, and verify a file later. No BlockchainProof API key or shared billing
account is required; see the `BLOCKCHAINPROOF_*` values in `.env` when pointing
Sara at a compatible self-hosted service.

Sara Wallet is not a broker, exchange, custodian, investment adviser, trading platform, or financial services provider. It is a self-custodial wallet and interface that helps users interact with third-party networks and protocols. Sara Wallet does not execute, clear, custody, intermediate, guarantee, or provide advice for any transaction. All actions are initiated by the user and performed through third-party systems at the user's own risk. See [`DISCLAIMER.md`](DISCLAIMER.md) for the full legal disclaimer.

---

## ⛓️ Supported Chains & Stablecoins

<!-- Icons: atomiclabs/cryptocurrency-icons (MIT), pinned to v0.18.1 via jsDelivr -->

| Network | Native gas asset | USDC |
|---|---|:---:|
| Ethereum | ETH | ✅ |
| Arbitrum | ETH | ✅ |
| Base | ETH | ✅ |
| OP Mainnet | ETH | ✅ |
| Polygon PoS | POL | ✅ |

USDC contract addresses come from [Circle's official contract-address list](https://developers.circle.com/stablecoins/usdc-contract-addresses). Users can enable or hide these networks and USDC per network under **Settings → Manage Networks & Tokens**. Native gas assets remain enabled whenever their network is enabled.

---

## 🛣️ Release Status and Roadmap

Stages 0–5 are implemented locally. The Sara Names registry contract, its tests and deployment tooling live in a separate repo; this codebase's client, signed records and indexer are implemented and tested, but the registry has **not yet been broadcast to Polygon Amoy**. Sara Names remains unavailable until `SARA_NAME_REGISTRAR_ADDRESS` points to a verified deployment.

Before any mainnet launch, the project still requires an independent smart-contract audit, a hardware-controlled multisig, authoritative reconfirmation of network/token addresses and a low-value canary deployment - see that separate repo's mainnet-readiness notes.

Longer-term work includes broader live reconciliation coverage, realtime voice where supported and additional command languages.

---

## 🚀 Getting Started

Sara runs locally on your laptop. The frontend is a single HTML app; the backend is a Python FastAPI server.

### 1. Clone the repo

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet/backend
```

### 2. Create a Python 3.12 virtual environment

Python 3.12 is the supported release runtime. Do not use Python 3.14: the
security-fixed LiteLLM release in Sara's reviewed lockfile does not support it.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
```

### 4. Configure your environment

```bash
cd ..
cp .env .env.local
```

### 5. Run the app

```bash
cd backend
source .venv/bin/activate
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8888
```

Then open your browser at:

```
http://127.0.0.1:8888
```

### 6. First-run setup

The first time you open Sara, you'll be asked to **create a passphrase**. This protects your wallets' private keys. Remember it; there's no recovery if you lose it (existing wallets become permanently undecryptable). Every time after, you'll unlock with the same passphrase, and Sara auto-locks after 1 hour of inactivity.

Then go to **Settings** and add your OpenRouter API key, and pick any model from the dropdown.

**Optional - market data, token balances & payment reconciliation:**

```env
COINGECKO_API_KEY
ALCHEMY_API_KEY
```

`ALCHEMY_API_KEY` enables USDC balance discovery and automatic EVM payment-request reconciliation (instead of requiring a manual "mark paid").

**Optional - alerts, contract intelligence, risk screening and Sara Names:**

```env
POLYGONSCAN_API_KEY=
RISK_SCREENING_PROVIDER=
RISK_SCREENING_API_URL=
RISK_SCREENING_API_KEY=
RISK_SCREENING_MANDATORY=false
SARA_NAME_REGISTRAR_ADDRESS=
SARA_NAME_SERVICE_URL=
```

Risk screening is provider-neutral and reports `unavailable` unless a provider, HTTPS API URL and API key are configured. Setting `RISK_SCREENING_MANDATORY=true` makes sends fail closed when screening is flagged or unavailable. Sara Names should only be configured after the Amoy deployment is source-verified; the off-chain record service is optional and its records are always signature-checked against current registry state.

### Invoices and merchant API

The **Invoices** screen creates persistent Polygon USDC invoices and public payment pages. Sara checks active invoices in the background, links a matching on-chain transfer, and exposes a proof-of-payment receipt.

Create a merchant client in that screen, save the API key when shown, then use `X-Sara-Merchant-Key` with `POST /api/payments/merchant/invoices` and `GET /api/payments/merchant/invoices/{reference}`. An optional HTTPS webhook receives `payment_request.paid`; verify the exact request body using HMAC-SHA256 and the `X-Sara-Signature-256` header. Failed webhook deliveries use Sara's bounded retry queue.

### Business payments and approvals

The **Business** view manages counterparties, payment/airdrop batches, schedules, payroll runs, spending policies and accounting. Batch amounts remain integers in token base units through validation and signing. Transactions are signed and durably recorded before broadcast so an interrupted run can retry the same transaction without signing a duplicate.

When a policy requires dual control, create a checker credential in the Business view and give it to the authorised approver. Checker keys are shown once and stored only as SHA-256 hashes; caller-supplied names are not treated as identities.

### Token, safety and contract tools

The **Tools** view provides pinned and tested ERC-20 templates, treasury and wallet intelligence, stablecoin routing, allowance management, address screening and verified-contract calls. Contract writes are restricted to allowlisted methods, reject unverified/proxy contracts and unlimited approvals, and are rebuilt and simulated immediately before signing.

At any point, type **"How to use Sara"** in the chat (it's pinned as the first suggestion chip) for a feature list and current configuration status, including configured keys, Sara Names availability and the selected AI model.

---

## 🏗️ Architecture

Sara is designed as a local-first wallet and AI assistant.

```
sara-wallet/
├── index.html              # Frontend app
├── contracts/              # Foundry ERC-20 templates for the token creator
├── examples/x402/          # Runnable x402 demo seller + tool-calling agent
└── backend/
    ├── main.py             # FastAPI entrypoint
    ├── requirements.txt    # Developer dependency inputs
    ├── requirements-lock.txt # Reviewed, pinned release dependencies
    └── app/
        ├── routers/        # API routes
        ├── services/       # Batches, accounting, alerts, schedules and monitoring
        ├── tools/          # Wallet, market, names, tokens, risk, contract and x402 tools
        ├── chains/         # Chain-specific transaction logic
        ├── db/             # SQLite models and session setup
        ├── llm/            # AI provider integration
        └── core/           # App configuration
```

### Frontend

The frontend lives in `index.html`. It provides the wallet UI, chat interface, settings screen, address book, portfolio views, and local interaction flows. It communicates with the backend through local API routes under `/api/*`.

### Backend

The backend is a FastAPI app in `backend/main.py`. It handles:

- Wallet creation and import
- Encrypted private key storage
- Address book entries
- Chat commands
- Transaction preparation and confirmation
- Payment links, invoices, receipts, merchant API and automatic reconciliation
- Batch/recurring payments, payroll and authenticated approvals
- Accounting, fiat valuation, FIFO cost basis, reporting and exports
- Token creation/management, allowance controls and transaction simulation
- Treasury, wallet intelligence, risk screening and alerts
- Sara Names registration, resolution, signed records and indexing
- x402 pay-per-call payments, policy-gated for unattended/agent use
- Market data requests
- AI provider integration
- Local SQLite persistence

### Database

Sara uses SQLite by default at `backend/sara.db`. Versioned startup migrations preserve existing local databases. In addition to wallets and transactions, the schema stores invoices, receipts, counterparties, batches and approvals, schedules and payroll, spending policies, accounting classifications and cost lots, alert/outbox records, token deployments, risk checks and Sara Names state.

### Wallet Encryption & Locking

Private keys are encrypted (AES-256-GCM) before being stored in SQLite. The encryption key is derived from a passphrase you set on first run - Sara holds it in memory only for an unlocked session (auto-expiring after 1 hour of inactivity), not sitting loaded at all times the way early versions did. `.env` no longer holds this key. **Private keys never leave your laptop.**

### AI Layer

Sara connects to AI models through [OpenRouter](https://openrouter.ai), giving access to hundreds of models (GPT, Claude, Gemini, Llama, and more) via one API key. The AI layer lives in `backend/app/llm/`.

### Chain Layer

Chain-specific logic lives in `backend/app/chains/`. 

Transaction tools are kept separate from chat handling so wallet actions can be validated before execution.

### Tool Layer

Sara's tools live in `backend/app/tools/`, organized into:

- Wallet tools
- Market data tools
- Sara Names and name-resolution tools
- Token creation and management tools
- Contract simulation, allowance and risk tools
- Trading integrations (swaps & cross-chain bridging)
- Payment, invoicing, receipt and reconciliation tools
- x402 client (pay-per-call HTTP fetches, trusted-asset-only)

The chat interface routes user messages into these tools when a command can be handled deterministically.

---

## 🔒 Security Philosophy

Sara is built on a simple principle:

> **Your keys never leave your machine.**

- Private keys are encrypted and stored locally
- Sara locks like a normal wallet - passphrase required to unlock, auto-locks after 1 hour of inactivity
- Swaps and bridges are verified before signing: Sara simulates the transaction (or checks the aggregator's own quote/result) and refuses to sign if it would move more than the confirmed input amount - it doesn't trust calldata blindly
- Batch and token amounts are signed from exact integer base units; crash recovery reuses persisted signed transaction bytes instead of creating a second payment
- Spending limits are enforced immediately before chat, token, batch and supported contract sends; time windows use the policy's configured IANA timezone
- Dual-control approvals use independently generated, hashed checker credentials rather than self-declared actor names
- Risk screening can be configured to fail closed, and provider evidence is stored as bounded identifiers rather than allegation text
- Verified contract writes are allowlisted, confirmation-bound, re-simulated before signing and reject proxies and unlimited approvals
- Sara Names records use EIP-712 signatures, content hashes, sequence/epoch replay protection and live on-chain ownership checks
- Token symbols only ever resolve to a hardcoded, developer-verified contract address list - never an arbitrary on-chain lookup
- No telemetry, no cloud sync, no external key custody
- Open source - read every line, audit everything
- You own your wallet code

---

## 🤝 Contributing

Sara is open source and contributions are welcome.

1. Fork the repo
2. Create your branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'Add my feature'`
4. Push to the branch: `git push origin feature/my-feature`
5. Open a Pull Request

Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before submitting.

---

## 📄 License

Apache License 2.0 © 2026 Rohas Nagpal

See [`LICENSE`](LICENSE) for the full text, and [`DISCLAIMER.md`](DISCLAIMER.md) for the legal disclaimer.

---

<div align="center">
<br />
Sara Wallet is built in 🇮🇳 India for the world.
<br />
</div>
