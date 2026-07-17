<div align="center">

**Sara AI Wallet** is an open source, AI-powered crypto wallet that makes sending stablecoins as easy as sending a text message: Send 50 USDT to Maria.

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

**Sara AI Wallet** is an open source, AI-powered crypto wallet that makes sending stablecoins as easy as sending a text message: Send 50 USDT to Maria.

Some of the things you can do with Sara Wallet:

1. Send stablecoins by typing or saying: `"send 100 usdc to zara"`
2. Request payment with a shareable link and QR code: `"payment link for 100 usdc"`
3. Bridge stablecoins across chains, like: `"bridge 100 usdc from polygon to arbitrum"`
4. Swap tokens with a message like: `"swap 100 usdt for usdc"`
5. Track payment requests automatically. Sara checks on-chain for a matching incoming transfer and reconciles your accounts.
6. Send to ENS, SNS, and bNames instead of copying long wallet addresses.
7. Analyze your portfolio across wallets, chains, tokens, and market moves.
8. Create and import wallets across EVM chains, Solana, and Tron.
9. Save addresses with easy-to-remember nicknames.
10. Choose the AI model that powers your wallet.
11. Use voice mode when you do not feel like typing.
12. Keep full control of your private keys.

Sara runs locally on your laptop. The frontend is a single HTML app; the backend is a Python FastAPI server.

Sara Wallet is not a broker, exchange, custodian, investment adviser, trading platform, or financial services provider. It is a self-custodial wallet and interface that helps users interact with third-party networks and protocols. Sara Wallet does not execute, clear, custody, intermediate, guarantee, or provide advice for any transaction. All actions are initiated by the user and performed through third-party systems at the user's own risk. See [`DISCLAIMER.md`](DISCLAIMER.md) for the full legal disclaimer.

---

## ⛓️ Supported Chains & Stablecoins

<!-- Icons: atomiclabs/cryptocurrency-icons (MIT), pinned to v0.18.1 via jsDelivr -->

| Chain | Native crypto | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/usdc.svg" width="16" height="16" valign="middle" alt="USDC"/> USDC | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/usdt.svg" width="16" height="16" valign="middle" alt="USDT"/> USDT |
|---|---|:---:|:---:|
| Arbitrum | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/eth.svg" width="16" height="16" valign="middle" alt="ETH"/> ETH | ✅ | ✅ |
| Avalanche C-Chain | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/avax.svg" width="16" height="16" valign="middle" alt="AVAX"/> AVAX | 🔜 Coming soon | 🔜 Coming soon |
| Base | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/eth.svg" width="16" height="16" valign="middle" alt="ETH"/> ETH | ✅ | 🔜 Coming soon |
| BNB Smart Chain | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/bnb.svg" width="16" height="16" valign="middle" alt="BNB"/> BNB | — | 🔜 Coming soon |
| Ethereum | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/eth.svg" width="16" height="16" valign="middle" alt="ETH"/> ETH | ✅ | ✅ |
| Optimism | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/eth.svg" width="16" height="16" valign="middle" alt="ETH"/> ETH | ✅ | ✅ |
| Polygon | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/matic.svg" width="16" height="16" valign="middle" alt="POL"/> POL | ✅ | ✅ |
| Solana | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/sol.svg" width="16" height="16" valign="middle" alt="SOL"/> SOL | ✅ | ✅ |
| Tron | <img src="https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@0.18.1/svg/color/trx.svg" width="16" height="16" valign="middle" alt="TRX"/> TRX | — | ✅ |

---

## 🛣️ Roadmap

Here's what's coming to Sara:

| Feature | Description |
|---|---|
| 📊 **Balance Monitoring** | Automate routine balance checks and get alerts on Telegram |
| 🔁 **Battle-test Solana & Tron reconciliation** | Automatic on-chain payment-request matching is built and live-verified for EVM (Alchemy); Solana and Tron use the same approach but haven't yet been proven against a real incoming transfer in the wild |
| 🛡️ **Send Limits** | Set max send limits as a safety guardrail |
| 🌍 **Multi-language commands & voice** | Chat commands and voice mode are English-only for now — this is a deliberate v1 scope choice, not an oversight |

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

**Optional — market data, token balances & payment reconciliation:**

```env
COINGECKO_API_KEY
ALCHEMY_API_KEY
HELIUS_RPC
TRONGRID_API_KEY
```

`TRONGRID_API_KEY` is required for Tron TRC20 (USDT) balance checks, sends, and payment-request reconciliation — get a free key at [trongrid.io](https://www.trongrid.io). Native TRX sends and TRX reconciliation work without one. `ALCHEMY_API_KEY` also enables automatic reconciliation for EVM payment requests (checks your wallet's real on-chain transfer history for a match instead of requiring a manual "mark paid").

At any point, type **"How to use Sara"** in the chat (it's pinned as the first suggestion chip) for a full feature list plus your current configuration status — which keys are set, whether bNames are ready, your AI model, and more.

---

## 🏗️ Architecture

Sara is designed as a local-first wallet and AI assistant.

```
sara-wallet/
├── index.html              # Frontend app
└── backend/
    ├── main.py             # FastAPI entrypoint
    ├── requirements.txt    # Developer dependency inputs
    ├── requirements-lock.txt # Reviewed, pinned release dependencies
    └── app/
        ├── routers/        # API routes
        ├── tools/          # Wallet, market, trading, and utility tools
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
- Payment links, QR codes, and automatic reconciliation
- Market data requests
- AI provider integration
- Local SQLite persistence

### Database

Sara uses SQLite by default at `backend/sara.db`. The main tables are `wallets`, `address_book`, `transactions`, `payment_requests`, `chat_messages`, and `config`.

### Wallet Encryption & Locking

Private keys are encrypted (AES-256-GCM) before being stored in SQLite. The encryption key is derived from a passphrase you set on first run — Sara holds it in memory only for an unlocked session (auto-expiring after 1 hour of inactivity), not sitting loaded at all times the way early versions did. `.env` no longer holds this key. **Private keys never leave your laptop.**

### AI Layer

Sara connects to AI models through [OpenRouter](https://openrouter.ai), giving access to hundreds of models (GPT, Claude, Gemini, Llama, and more) via one API key. The AI layer lives in `backend/app/llm/`.

### Chain Layer

Chain-specific logic lives in `backend/app/chains/`. 

Transaction tools are kept separate from chat handling so wallet actions can be validated before execution.

### Tool Layer

Sara's tools live in `backend/app/tools/`, organized into:

- Wallet tools
- Market data tools
- Name resolution tools
- Trading integrations (swaps & cross-chain bridging)
- Payment links & reconciliation tools

The chat interface routes user messages into these tools when a command can be handled deterministically.

---

## 🔒 Security Philosophy

Sara is built on a simple principle:

> **Your keys never leave your machine.**

- Private keys are encrypted and stored locally
- Sara locks like a normal wallet — passphrase required to unlock, auto-locks after 1 hour of inactivity
- Swaps and bridges are verified before signing: Sara simulates the transaction (or checks the aggregator's own quote/result) and refuses to sign if it would move more than the confirmed input amount — it doesn't trust calldata blindly
- Token symbols only ever resolve to a hardcoded, developer-verified contract address list — never an arbitrary on-chain lookup
- No telemetry, no cloud sync, no external key custody
- Open source — read every line, audit everything
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
