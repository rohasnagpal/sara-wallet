<div align="center">

**Sara Wallet** is an open source, AI-powered local crypto wallet that you can talk to and chat with. 

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-f4a261?style=flat-square)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open-Source-e76f51?style=flat-square&logo=github)](https://github.com)
[![AI Powered](https://img.shields.io/badge/AI-Powered-264653?style=flat-square&logoColor=white)](https://github.com)
[![Runs Locally](https://img.shields.io/badge/Runs-Locally-2a9d8f?style=flat-square)](https://github.com)

<br />
<img width="2836" height="1536" alt="image" src="https://github.com/user-attachments/assets/2f704c7e-e1c0-4187-a697-912ea5663f59" />
</div>

---

## ✦ What is Sara Wallet?

**Sara Wallet** is an open source, AI-powered local crypto wallet that you can talk to and chat with. Sara Wallet gets its name from the hindi word सारा that means everything.

Some of the things you can do with Sara Wallet:

1. Send crypto with natural language, like: `"send 100 POL to zara"`
2. Trade commodities, crypto, forex, and stocks through Hyperliquid
3. Swap tokens with a message like: `"swap 1 ETH for USDC"`
4. Use DeFi tools to explore, yields, liquidity, and on-chain opportunities
5. Track prediction markets and ask Sara about odds, outcomes, and narratives
6. Send to ENS and SNS names instead of copying long wallet addresses
7. Analyze your portfolio across wallets, chains, tokens, and market moves
8. Choose the AI model that powers your wallet
9. Get prices, stats, news, and market sentiment across commodities, crypto, forex, and stocks
10. Use voice mode when you do not feel like typing
11. Create and import wallets across multiple chains
12. Save addresses with easy-to-remember nicknames
13. Keep full control of your private keys

Sara runs locally on your laptop. The frontend is a single HTML app; the backend is a Python FastAPI server.

Sara Wallet is not a broker, exchange, custodian, investment adviser, trading platform, or financial services provider. It is a self-custodial wallet and interface that helps users interact with third-party networks, protocols, and services such as Hyperliquid. Sara Wallet does not execute, clear, custody, intermediate, guarantee, or provide advice for any transaction. All actions are initiated by the user and performed through third-party systems at the user's own risk. See [`DISCLAIMER.md`](DISCLAIMER.md) for the full legal disclaimer.

---

## ⛓️ Supported Chains

| Chain | Native sends | Swaps | Perps |
|---|---|---|---|
| Ethereum | ✅ | ✅ (Paraswap) | — |
| Polygon | ✅ | ✅ (Paraswap) | — |
| Arbitrum | ✅ | ✅ (Paraswap) | — |
| Base | ✅ | ✅ (Paraswap) | — |
| Optimism | ✅ | ✅ (Paraswap) | — |
| BNB Smart Chain | ✅ | — | — |
| Avalanche C-Chain | ✅ | — | — |
| Solana | ✅ | ✅ (Jupiter) | — |
| Hyperliquid | — | — | ✅ |
| Bitcoin | Coming soon | — | — |

---

## 🛣️ Roadmap

Here's what's coming to Sara:

| Feature | Description |
|---|---|
| 💸 **x402 payments** | Let Sara pay for x402-gated resources/APIs on your behalf — researched, not yet built |
| 📊 **Balance Monitoring** | Automate routine balance checks and get alerts on Telegram |
| 📋 **Unified Portfolio** | Bring stock & commodity holdings into the portfolio view, alongside crypto |
| 🛡️ **Send Limits** | Set max send limits as a safety guardrail |
| 🌍 **Multi-language commands & voice** | Chat commands and voice mode are English-only for now — this is a deliberate v1 scope choice, not an oversight |
| ₿ **Bitcoin support** | Native BTC holding/sending — a real, separate integration (UTXO model, not account-based like EVM/Solana) |

---

## 🚀 Getting Started

Sara runs locally on your laptop. The frontend is a single HTML app; the backend is a Python FastAPI server.

### 1. Clone the repo

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet/backend
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
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

The first time you open Sara, you'll be asked to **create a passphrase**. This protects your wallets' private keys. Remember it; there's no recovery if you lose it (existing wallets become permanently undecryptable). Every time after, you'll unlock with the same passphrase, and Sara auto-locks after 15 minutes of inactivity.

Then go to **Settings** and add your OpenRouter API key, and pick any model from the dropdown.

**Optional — market data, extra EVM chains & RPC endpoints:**

```env
COINGECKO_API_KEY
ALCHEMY_API_KEY
HELIUS_RPC
```

At any point, type **"How to use Sara"** in the chat (it's pinned as the first suggestion chip) for a full feature list plus your current configuration status — which keys are set, whether bNames are ready, your AI model, and more.

---

## 🏗️ Architecture

Sara is designed as a local-first wallet and AI assistant.

```
sara-wallet/
├── index.html              # Frontend app
└── backend/
    ├── main.py             # FastAPI entrypoint
    ├── requirements.txt    # Python dependencies
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
- Market data requests
- AI provider integration
- Local SQLite persistence

### Database

Sara uses SQLite by default at `backend/sara.db`. The main tables are `wallets`, `address_book`, `transactions`, `chat_messages`, and `config`.

### Wallet Encryption & Locking

Private keys are encrypted (AES-256-GCM) before being stored in SQLite. The encryption key is derived from a passphrase you set on first run — Sara holds it in memory only for an unlocked session (auto-expiring after 15 minutes of inactivity), not sitting loaded at all times the way early versions did. `.env` no longer holds this key. **Private keys never leave your laptop.**

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
- Trading integrations
- Prediction market helpers

The chat interface routes user messages into these tools when a command can be handled deterministically.

---

## 🔒 Security Philosophy

Sara is built on a simple principle:

> **Your keys never leave your machine.**

- Private keys are encrypted and stored locally
- Sara locks like a normal wallet — passphrase required to unlock, auto-locks after 15 minutes of inactivity
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
