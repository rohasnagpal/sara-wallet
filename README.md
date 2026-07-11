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

<img width="1501" height="981" alt="sara-1" src="https://github.com/user-attachments/assets/47e4e5d2-1160-4608-acbe-ec01812d22c4" />

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

### 💼 Portfolio

`show my portfolio` aggregates native balances across Ethereum, Arbitrum, Base, Polygon, Optimism, and Solana in one view. ERC-20 token balances require an Alchemy API key (Settings → Data APIs).

### 🔧 Extend It Your Way

- Integrate any external API or service
- Build DeFi tools, dashboards, and bots on top
- Modify the code without restrictions — it's yours

---

## 🛣️ Roadmap

Here's what's coming to Sara:

| Feature | Description |
|---|---|
| 📊 **Balance Monitoring** | Automate routine balance checks and alerts |
| 📋 **Unified Portfolio** | Bring stock & commodity holdings into the portfolio view, alongside crypto |
| 🛡️ **Send Limits** | Set max send limits as a safety guardrail |

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

Create a `.env` file in the repo root:

```bash
cd ..
touch .env
```

Example `.env`:

```env
LLM_PROVIDER=groq
LLM_MODEL=llama-3.1-8b-instant
GROQ_API_KEY=your_api_key_here

DATABASE_URL=sqlite:///./sara.db

SARA_MASTER_KEY=your_wallet_passphrase_or_derived_key
```

> **`SARA_MASTER_KEY`** protects your locally stored private keys. You can use a normal passphrase in Settings — Sara derives the actual encryption key from it.  
> ⚠️ If you reset this key, existing encrypted wallets cannot be decrypted unless you restore the original key or re-import the wallets.

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

### 6. Add your API keys

Open Sara in your browser, go to **Settings**, and add your preferred AI provider key.

**AI providers:**

```env
GROQ_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
XAI_API_KEY
GOOGLE_API_KEY
```

**Optional — market data & RPC endpoints:**

```env
COINGECKO_API_KEY
ALCHEMY_API_KEY
HELIUS_RPC
ETH_RPC
ARB_RPC
BASE_RPC
POLY_RPC
OP_RPC
```

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

### Wallet Encryption

Private keys are encrypted before being stored in SQLite. The encryption key is derived from `SARA_MASTER_KEY`, stored locally in `.env` or configured through Settings. **Private keys never leave your laptop.**

### AI Layer

Sara uses [LiteLLM](https://github.com/BerriAI/litellm) to connect to different AI providers through one interface. The AI layer lives in `backend/app/llm/`.

Supported providers: **Groq, OpenAI, Anthropic, xAI, Gemini, Ollama**, and Cloudflare-compatible models.

### Chain Layer

Chain-specific logic lives in `backend/app/chains/`. Current modules cover:

- EVM chains — Ethereum, Polygon, Arbitrum, Base, Optimism
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

MIT © Sara Wallet Contributors

---

<div align="center">

<br />

*Built for the curious. Owned by you.*

**सारा** — everything.

<br />

</div>
