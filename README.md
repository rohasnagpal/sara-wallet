<div align="center">

<br />

```
███████╗ █████╗ ██████╗  █████╗
██╔════╝██╔══██╗██╔══██╗██╔══██╗
███████╗███████║██████╔╝███████║
╚════██║██╔══██║██╔══██╗██╔══██║
███████║██║  ██║██║  ██║██║  ██║
╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝
```

**सारा** · *Everything, in one wallet.*

[![License: MIT](https://img.shields.io/badge/License-MIT-f4a261?style=flat-square)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open-Source-e76f51?style=flat-square&logo=github)](https://github.com)
[![AI Powered](https://img.shields.io/badge/AI-Powered-264653?style=flat-square&logoColor=white)](https://github.com)
[![Runs Locally](https://img.shields.io/badge/Runs-Locally-2a9d8f?style=flat-square)](https://github.com)

<br />

> **Sara** (सारा) is the Hindi word for *everything* — and that's exactly what this wallet aims to be.  
> An open-source, AI-powered crypto wallet that runs entirely on your laptop.  
> Your code. Your keys. Your laptop.

<br />

</div>

---

## ✦ What is Sara Wallet?

Sara Wallet is an **open-source, AI-native crypto wallet** that lives entirely on your laptop. Drop in your AI API key and Sara becomes a conversational interface for your entire crypto life. Send tokens with a message like: "send 100 pol to zara". 

```
You:   send 100 pol to zara
Sara:  ✓ Sent 100 POL to zara.eth — tx hash: 0xab3f...
```

No dashboards to navigate. No buttons to click. Just talk.

<img width="2836" height="1536" alt="image" src="https://github.com/user-attachments/assets/2f704c7e-e1c0-4187-a697-912ea5663f59" />

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

ERC-20 token balances (beyond native tokens) require an Alchemy API key. Token *sends* are native-asset only for now — ERC-20/SPL token transfers aren't wired up yet. Swaps currently route through Paraswap (EVM) and Jupiter (Solana) only — BSC and Avalanche support native sends and balance checks, swaps aren't wired up for them yet.

---

## ⚡ Features

### 🔁 Send Crypto Naturally

Type the way you think. Sara handles the rest.

```
send 100 pol to zara
send 0.5 eth to 0x4f3c...
send 50 usdc to alice
```

### 📈 Prices — Crypto, Stocks & Commodities

```
gold price
btc price
apple stock
silver
```

### 📰 News & Sentiment

```
btc sentiment
silver sentiment
eth news
```

### 🔐 Wallet Management

- Create and import wallets across **multiple chains**
- Save addresses with **names and nicknames**
- Encrypted private keys stored **locally on your laptop**
- Full control — no custodian, no cloud sync

### ⛽ Live Gas Fees

View real-time gas estimates before every transaction. No surprises.

### 🔄 Token Swaps

```
swap 1000 pol for usdc
swap 1 sol for usdc
```

EVM swaps route through Paraswap, Solana swaps through Jupiter. Always previewed with a **CONFIRM** step before anything executes.

### 📉 Perps Trading

Open and close leveraged crypto perpetuals on Hyperliquid, straight from chat — with a preview and **CONFIRM** step before any order is placed.

```
long btc $500 5x
close my eth position
```

### 🌾 DeFi & Prediction Markets

```
top defi yields on ethereum
tvl on aave
will bitcoin hit 100k?
what's trending
```

### 🌐 ENS & SNS Resolution

Send to `alice.eth` or `bob.sol` directly — Sara resolves the name to an address before asking you to confirm.

### 🔖 bNames — buy a human-readable name for your wallet

```
buy a bname
register rohas.sara
```

Pay a small fee to link a name like `rohas.sara` or `rohas.bname` to your wallet, then send/receive using it just like `alice.eth` or `bob.sol`. No smart contract — names live as plain, publicly verifiable Polygon transactions, resolved by reading the chain directly (see [`registrar-service/DEPLOYMENT.md`](registrar-service/DEPLOYMENT.md) for the technical design).

**This requires a separately deployed registrar service** — the wallet-side code is fully built, but registration won't complete until you (or whoever runs your instance) deploys `registrar-service/` per that guide and set the `SARA_NAME_*` variables in `.env`. Without it, Sara will show you a price quote but registration will fail at the payment-confirmation step.

### 🎙️ Voice Mode

Click the mic icon next to the chat box to speak instead of type. English only for now. For safety, **CONFIRM must always be typed**, never spoken — Sara won't act on a spoken "confirm," even by accident.

### 🔒 Wallet Security

Sara locks like a normal wallet. The first time you open it, you'll create a passphrase; after that, you unlock with it each session, and Sara auto-locks after 15 minutes of inactivity (or immediately via a manual "Lock Now" in Settings). Only money-moving actions — send, swap, perps, bName registration, wallet create/import — require being unlocked. Price checks, news, portfolio views, and general chat all work while locked.

### 💼 Portfolio

`show my portfolio` aggregates native balances across Ethereum, Arbitrum, Base, Polygon, Optimism, BNB Smart Chain, Avalanche, and Solana in one view. ERC-20 token balances require an Alchemy API key (Settings → Data APIs).

### 🤖 Any AI Model, One API Key

Sara connects through **OpenRouter**, so you can pick from hundreds of models (GPT, Claude, Gemini, Llama, and more) with a single API key — no juggling separate provider accounts. Change your model anytime in Settings.

### 🔧 Extend It Your Way

- Integrate any external API or service
- Build DeFi tools, dashboards, and bots on top
- Modify the code without restrictions — it's yours

---

## 🛣️ Roadmap

Here's what's coming to Sara:

| Feature | Description |
|---|---|
| 💸 **x402 payments** | Let Sara pay for x402-gated resources/APIs on your behalf — researched, not yet built |
| 📊 **Balance Monitoring** | Automate routine balance checks and alerts |
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

Copy the tracked template and fill in what you need. `.env` in the repo is a
placeholder-only template safe to commit; your real config goes in
`.env.local`, which is gitignored:

```bash
cd ..
cp .env .env.local
```

At minimum, set your [OpenRouter](https://openrouter.ai) API key:

```env
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

> Note there's no `SARA_MASTER_KEY` to set here. Sara locks/unlocks like a normal wallet now — the first time you open the app, you'll create a passphrase directly in the UI, and it's stored automatically. See step 6.

### 5. Run the app

```bash
cd backend
source .venv/bin/activate
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Then open your browser at:

```
http://127.0.0.1:8000
```

### 6. First-run setup

The first time you open Sara, you'll be asked to **create a passphrase** — this protects your wallets' private keys. Remember it; there's no recovery if you lose it (existing wallets become permanently undecryptable). Every time after, you'll unlock with the same passphrase, and Sara auto-locks after 15 minutes of inactivity.

Then go to **Settings** and add your OpenRouter API key, and pick any model from the dropdown.

**Optional — market data, extra EVM chains & RPC endpoints:**

```env
COINGECKO_API_KEY
ALCHEMY_API_KEY
HELIUS_RPC
ETH_RPC
ARB_RPC
BASE_RPC
POLY_RPC
OP_RPC
BSC_RPC
AVAX_RPC
```

**Optional — bName (blockchain name) registration:**

```env
SARA_NAME_REGISTRAR_ADDRESS
SARA_NAME_LOG_ADDRESS
SARA_NAME_SERVICE_URL
SARA_NAME_REGISTRATION_FEE
POLYGONSCAN_API_KEY
```
Requires a separately deployed registrar service — see [`registrar-service/DEPLOYMENT.md`](registrar-service/DEPLOYMENT.md).

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

Chain-specific logic lives in `backend/app/chains/`. Current modules cover:

- EVM chains — Ethereum, Polygon, Arbitrum, Base, Optimism, BNB Smart Chain, Avalanche C-Chain
- Solana

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

**What's gated behind unlocking, and what isn't:**

| Requires unlock | Works while locked |
|---|---|
| Send crypto | Price checks (crypto/stock/commodity/forex) |
| Swap tokens | News & sentiment |
| Hyperliquid perps (open/close) | Portfolio view |
| bName registration | Gas fees, DeFi TVL/yields |
| Create/import a wallet | Polymarket search |
| | General chat |

Only actions that move money or touch a private key require your passphrase — everything else works whether Sara is locked or not.

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

MIT © Sara Wallet Contributors

---

<div align="center">

<br />

*Built for the curious. Owned by you.*

**सारा** — everything.

<br />

</div>
