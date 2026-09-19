<div align="center">

**Sara AI Wallet** is an open-source, local-first AI wallet for stablecoin payments, x402 agentic payments, business tools and Web3.

> ⚠️ **Status: Alpha.** Use small amounts and testnets while Sara is under active development. No third-party security audit has been done yet — see [SECURITY.md](SECURITY.md).

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-f4a261?style=flat-square)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-e63946?style=flat-square)](ROADMAP.md)
[![CI](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml/badge.svg)](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml)
[![Foundry tests](https://github.com/rohasnagpal/sara-wallet/actions/workflows/contracts.yml/badge.svg)](https://github.com/rohasnagpal/sara-wallet/actions/workflows/contracts.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-264653?style=flat-square&logo=python&logoColor=white)](docs/architecture.md)
[![Release](https://img.shields.io/github/v/release/rohasnagpal/sara-wallet?style=flat-square&include_prereleases&label=release&color=e76f51)](https://github.com/rohasnagpal/sara-wallet/releases)
[![x402 supported](https://img.shields.io/badge/x402-supported-2a9d8f?style=flat-square)](docs/x402.md)
[![Networks](https://img.shields.io/badge/networks-5%20chains-f4a261?style=flat-square)](https://developers.circle.com/stablecoins/usdc-contract-addresses)
[![Runs Locally](https://img.shields.io/badge/Runs-Locally-2a9d8f?style=flat-square)](docs/architecture.md)
[![GitHub stars](https://img.shields.io/github/stars/rohasnagpal/sara-wallet?style=flat-square&color=f4a261)](https://github.com/rohasnagpal/sara-wallet/stargazers)

<br />
<img width="2836" height="1536" alt="image" src="https://github.com/user-attachments/assets/2f704c7e-e1c0-4187-a697-912ea5663f59" />
</div>

---

Interested in open-source AI wallets? ⭐ [Star Sara](https://github.com/rohasnagpal/sara-wallet/stargazers) to follow development.

---

## ✦ What is Sara Wallet?

**Sara AI Wallet** is an open source, self-custodial, local-first AI wallet — a crypto wallet and stablecoin wallet that makes USDC payments as easy as sending a text message: `send 50 USDC to Maria`.

Sara runs locally on your laptop. The frontend is a single HTML app; the backend is a Python FastAPI server. It combines five product layers:

- **Wallet and payments** — natural-language sends, payment links/QR, crypto invoicing with reconciliation, receipts, swaps and bridges
- **Agentic payments (x402)** — pay-per-call for machine-priced HTTP resources, policy-gated for unattended use
- **Business and accounting** — batch payments, crypto payroll, FIFO cost basis, dual-control approvals, CSV/XLSX exports
- **ERC-20 token creator & safety tools** — deploy your own tokens, manage allowances, screen addresses, simulate contract calls
- **Sara Names** — human-readable names for wallet addresses, with signed multi-network records

Sara also includes a credential-free **BlockchainProof** evidence vault: hash a file locally, review and authorize an exact 1 USDC Polygon checkout from your own wallet, and verify the file later — no BlockchainProof API key or shared billing account required. See the `BLOCKCHAINPROOF_*` values in `.env` when pointing Sara at a compatible self-hosted service.

Sara Wallet is not a broker, exchange, custodian, investment adviser, trading platform, or financial services provider. It is a self-custodial wallet and interface that helps users interact with third-party networks and protocols. All actions are initiated by the user and performed through third-party systems at the user's own risk. See [`DISCLAIMER.md`](DISCLAIMER.md) for the full legal disclaimer.

---

## 🎬 Demo

The screenshot above is Sara's chat-first wallet UI, running entirely on your own laptop against your own local backend — nothing staged or hosted.

For something you can actually run and watch pay for itself: `examples/x402/` ships a runnable multi-resource paid demo site (weather/trivia/stock/recipe, each priced differently) with a free catalog, plus a tool-calling agent that browses it, picks the resource matching its task, and pays for only that one. See [`examples/x402/README.md`](examples/x402/README.md).

---

## 💡 Why Sara

- **Local-first & self-custodial** — your wallet database and keys live on your laptop; no cloud account, no custodian
- **Talk to it like a person** — "send 50 USDC to Maria" instead of hunting through menus and pasting addresses
- **Built for agents, not just humans** — x402 lets Sara pay machine-priced resources autonomously, within policies you set
- **Business-grade from day one** — batch payments, payroll, FIFO accounting and dual-control approvals are built in, not bolted on
- **Open source, Apache 2.0** — every line is auditable; you own your wallet code

---

## ⭐ Key Features

- 💸 **Natural-language USDC payments** — resolve a recipient, show the exact amount and fee, confirm, sign
- 🔗 **Payment links, QR codes & crypto invoicing** — shareable payment requests with automatic on-chain reconciliation and proof-of-payment receipts. → [docs/merchant-api.md](docs/merchant-api.md)
- 🔁 **Swaps & bridges** — trade or move stablecoins across networks with the quote shown before you sign
- 🤖 **Agentic payments (x402)** — pay-per-call for machine-priced HTTP resources, policy-gated for unattended use. → [docs/x402.md](docs/x402.md)
- 🏢 **Business & accounting** — batch payments, crypto payroll, spending policies, dual-control approvals, FIFO cost basis, CSV/XLSX exports. → [docs/business-payments.md](docs/business-payments.md)
- 🪙 **ERC-20 token creator & safety tools** — deploy your own tokens, manage allowances, screen addresses, simulate and verify contract calls. → [docs/token-creator.md](docs/token-creator.md)
- 🪪 **Sara Names** — human-readable names for your wallet addresses, with signed multi-network records. → [docs/sara-names.md](docs/sara-names.md)
- 📊 **Portfolio intelligence & alerts** — spend analysis, unusual-activity detection, Telegram/email/webhook alerts. → [docs/business-payments.md](docs/business-payments.md)

### Supported Chains & Stablecoins

<!-- Icons: atomiclabs/cryptocurrency-icons (MIT), pinned to v0.18.1 via jsDelivr -->

| Network | Native gas asset | USDC |
|---|---|:---:|
| Ethereum | ETH | ✅ |
| Arbitrum | ETH | ✅ |
| Base | ETH | ✅ |
| OP Mainnet | ETH | ✅ |
| Polygon PoS | POL | ✅ |

USDC contract addresses come from [Circle's official contract-address list](https://developers.circle.com/stablecoins/usdc-contract-addresses). Users can enable or hide these networks and USDC per network under **Settings → Manage Networks & Tokens**.

---

## 🔒 Security

> **Your keys never leave your machine.**

- **Does the AI see my private key?** No — keys are decrypted only in local signing code, never sent to the AI model.
- **Who signs transactions?** Local Python code, using web3.py — not the AI.
- **What runs locally?** The frontend, backend, database, key storage, and signing — everything except AI inference (or all of it, if you use a local Ollama model).
- **What leaves my machine?** Your chat messages (to whichever AI provider you configure), signed transactions (broadcast to the public chain), and any optional third-party lookups you've enabled (market data, balances, risk screening). Never key material. No telemetry, no cloud sync.
- **Can the AI send money itself?** No — every send needs your explicit `CONFIRM` + passphrase, except a spending-policy cap you configure in advance for unattended x402 auto-pay.

Full answers, threat model and the complete security philosophy live in [docs/security-model.md](docs/security-model.md). Sara has **not** undergone a third-party audit — see [SECURITY.md](SECURITY.md) for audit status, supported versions, and how to privately report a vulnerability.

---

## 🛣️ Release Status and Roadmap

Stages 0–5 are implemented locally. Sara Names' registry has **not yet been broadcast to Polygon Amoy** — see [docs/sara-names.md](docs/sara-names.md).

See [ROADMAP.md](ROADMAP.md) for what's actively being worked on, what's next, and what's just being explored — each item links to its tracking issue.

---

## 🚀 Getting Started

### Option A: Download a build

Grab a `sara-wallet-<macos|windows|linux>.zip` from the [Releases page](https://github.com/rohasnagpal/sara-wallet/releases) and unzip it. It opens your browser to `http://localhost:8888` on its own once started. Your wallet database and config live in your OS's standard app-data directory (e.g. `~/Library/Application Support/Sara` on macOS), not next to the app.

- **macOS:** double-click `Sara.app`.
- **Windows:** double-click `sara-wallet.exe`.
- **Linux:** run `./install-desktop-entry.sh` once (adds Sara to your application menu so future launches are a double-click too), or just run `./sara-wallet` directly.

These builds are unsigned, so the OS will warn you on first launch — this is expected for an alpha, source-available project without a paid code-signing certificate, not a sign anything's wrong:
- **macOS:** right-click the app → **Open** → **Open** again in the Gatekeeper prompt
- **Windows:** click **More info** → **Run anyway** in the SmartScreen prompt

If no build is listed for your platform yet, or you'd rather run from source, use one of the options below.

### Option B: Docker (fastest way to try it from source)

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet
export OPENROUTER_API_KEY=sk-or-...   # https://openrouter.ai/keys
docker compose up --build
```

Then open `http://localhost:8888`. Your wallet database persists in a
named Docker volume across restarts (`docker compose down` keeps it;
`docker compose down -v` deletes it). This is meant for quickly
evaluating Sara, not as its primary way to run — Sara is designed to run
directly on your machine so your keys never leave it; see
[docs/security-model.md](docs/security-model.md).

### Option C: Run it directly

#### 1. Clone the repo

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet/backend
```

#### 2. Create a Python 3.12 virtual environment

Python 3.12 is the supported release runtime. Do not use Python 3.14: the
security-fixed LiteLLM release in Sara's reviewed lockfile does not support it.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

#### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
```

#### 4. Configure your environment

```bash
cd ..
cp .env .env.local
```

#### 5. Run the app

```bash
cd backend
source .venv/bin/activate
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8888
```

Then open your browser at:

```
http://127.0.0.1:8888
```

#### 6. First-run setup

The first time you open Sara, you'll be asked to **create a passphrase**. This protects your wallets' private keys. Remember it; there's no recovery if you lose it (existing wallets become permanently undecryptable). Every time after, you'll unlock with the same passphrase, and Sara auto-locks after 1 hour of inactivity.

Then go to **Settings** and add your OpenRouter API key, and pick any model from the dropdown.

**Optional - market data, token balances & payment reconciliation:**

```env
COINGECKO_API_KEY
ALCHEMY_API_KEY
```

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

At any point, type **"How to use Sara"** in the chat (it's pinned as the first suggestion chip) for a feature list and current configuration status, including configured keys, Sara Names availability and the selected AI model.

For invoices/merchant API, business payments, and token/contract tools usage, see [docs/merchant-api.md](docs/merchant-api.md), [docs/business-payments.md](docs/business-payments.md) and [docs/token-creator.md](docs/token-creator.md).

---

## 🙌 Help build Sara

We're looking for contributors interested in wallets, stablecoins, x402, AI agents, security and Web3 UX.

[![Good first issues](https://img.shields.io/github/issues/rohasnagpal/sara-wallet/good%20first%20issue?style=flat-square&label=good%20first%20issues&color=7057ff)](https://github.com/rohasnagpal/sara-wallet/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
[![Help wanted](https://img.shields.io/github/issues/rohasnagpal/sara-wallet/help%20wanted?style=flat-square&label=help%20wanted&color=008672)](https://github.com/rohasnagpal/sara-wallet/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
[![Discussions](https://img.shields.io/badge/GitHub-Discussions-e76f51?style=flat-square&logo=github)](https://github.com/rohasnagpal/sara-wallet/discussions)
[![Contributing guide](https://img.shields.io/badge/read-Contributing%20guide-2a9d8f?style=flat-square)](CONTRIBUTING.md)

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

## 🔗 Links

- [docs/architecture.md](docs/architecture.md) — directory layout, frontend/backend/database/AI/chain/tool layers
- [docs/security-model.md](docs/security-model.md) — trust questions, threat model, full security philosophy
- [docs/x402.md](docs/x402.md) — agentic pay-per-call payments
- [docs/business-payments.md](docs/business-payments.md) — batches, payroll, accounting, intelligence, alerts
- [docs/token-creator.md](docs/token-creator.md) — ERC-20 creation, allowances, contract tools
- [docs/sara-names.md](docs/sara-names.md) — human-readable wallet names
- [docs/merchant-api.md](docs/merchant-api.md) — invoices, webhooks, merchant API
- [ROADMAP.md](ROADMAP.md) — what's Now / Next / Exploring
- [SECURITY.md](SECURITY.md) — supported versions, vulnerability reporting, audit status
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute
- [DISCLAIMER.md](DISCLAIMER.md) — legal disclaimer
- [LICENSE](LICENSE) — Apache 2.0

---

<div align="center">
<br />
Sara Wallet is built in 🇮🇳 India for the world.
<br />
</div>
