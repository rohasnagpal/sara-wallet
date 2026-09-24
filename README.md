<div align="center">

# Sara AI Wallet

**An open-source, local-first AI wallet for stablecoin payments.** Send USDC in plain English: `send 50 USDC to something.sara`.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-f4a261?style=flat-square)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-e63946?style=flat-square)](ROADMAP.md)
[![CI](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml/badge.svg)](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml)
[![Release](https://img.shields.io/github/v/release/rohasnagpal/sara-wallet?style=flat-square&include_prereleases&label=release&color=e76f51)](https://github.com/rohasnagpal/sara-wallet/releases)
[![GitHub stars](https://img.shields.io/github/stars/rohasnagpal/sara-wallet?style=flat-square&color=f4a261)](https://github.com/rohasnagpal/sara-wallet/stargazers)

<br />
<img width="2836" height="1536" alt="Sara's chat-first wallet interface running on a local machine" src="https://github.com/user-attachments/assets/2f704c7e-e1c0-4187-a697-912ea5663f59" />
</div>

> **Status: alpha.** Use small amounts and testnets while Sara is under active development. There has been no third-party security audit yet; see [SECURITY.md](SECURITY.md). What is planned next is in [ROADMAP.md](ROADMAP.md).

Sara runs on your own computer. The frontend is a single HTML app and the backend is a Python FastAPI server. It is self-custodial and open source (Apache 2.0): your keys, wallet database and signing all stay on your machine, and every line is auditable. It is also built for agents: with x402, Sara can pay machine-priced resources on its own, within spending policies you set.

## What you can do

| Area | What it does | Docs |
|---|---|---|
| **Payments** | Natural-language USDC sends with the exact amount and fee shown before you confirm. Swaps and bridges with the quote shown before you sign, and a choice between routes when there is more than one. | |
| **Invoicing** | Invoices with a QR code any wallet app can scan, automatic on-chain reconciliation and proof-of-payment receipts. | [invoicing.md](docs/invoicing.md) |
| **Business and accounting** | Batch payments from a CSV, recurring payments, payroll, spending policies, cost basis and profit and loss, income and expense reports, CSV and Excel exports. | [business-payments.md](docs/business-payments.md) |
| **Agentic payments (x402)** | Pay-per-call for machine-priced HTTP resources, policy-gated for unattended use. A runnable demo site and agent are in [`examples/x402/`](examples/x402/README.md). | [x402.md](docs/x402.md) |
| **Tokens and treasury** | Deploy your own ERC-20 token, review and revoke leftover approvals, screen addresses against a sanctions list, and compare stablecoin routes between networks. | [token-creator.md](docs/token-creator.md) |
| **Alerts** | Telegram alerts, and balance monitoring that messages you when a wallet crosses a limit. | [business-payments.md](docs/business-payments.md) |
| **Sara Names** (coming soon) | Human-readable names for wallet addresses, with signed multi-network records. The registry has not yet been broadcast to Polygon Amoy. | [sara-names.md](docs/sara-names.md) |
| **File proofs** (coming soon) | Hash a file locally, authorize an exact 1 USDC Polygon checkout from your own wallet, and verify the file later. No BlockchainProof API key or shared billing account needed. | [third-party-services.md](docs/third-party-services.md) |

### Supported networks

| Network | Native gas asset | USDC |
|---|---|:---:|
| Ethereum | ETH | Yes |
| Arbitrum | ETH | Yes |
| Base | ETH | Yes |
| OP Mainnet | ETH | Yes |
| Polygon PoS | POL | Yes |

USDC contract addresses come from [Circle's official contract-address list](https://developers.circle.com/stablecoins/usdc-contract-addresses). You can enable or hide networks and USDC per network under **Settings → Manage Networks & Tokens**.

## Privacy

Sara is private by default: no account, no telemetry, no cloud sync, and the page loads nothing from third parties. Your keys, wallet database and chat history stay on your machine.

Some services necessarily see part of what you do. Your AI provider sees your chat, public blockchain nodes see the addresses you look up, and LI.FI and ParaSwap see your address when you swap or bridge. Blockchains are public, and Sara does not hide on-chain activity. To keep chats local, use a local Ollama model. The full "who sees what" table and ways to reduce exposure are in [docs/privacy.md](docs/privacy.md).

## Security

**Your keys never leave your machine.**

- **Does the AI see my private key?** No. Keys are decrypted only in local signing code and never sent to the AI model.
- **Who signs transactions?** Local Python code, using web3.py, not the AI.
- **Can the AI send money itself?** No. Every send needs your explicit `CONFIRM` and passphrase, except a spending-policy cap you configure in advance for unattended x402 auto-pay.

More in [docs/security-model.md](docs/security-model.md), including the threat model. To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Quick start

### One-click installer (no terminal, no Python needed)

Download the installer for your system from the latest [release](https://github.com/rohasnagpal/sara-wallet/releases), then double-click it:

- **Mac (Apple Silicon):** `Install-Sara-Mac.zip`, then right-click `Install-Sara.command` → Open
- **Windows 10/11 (64-bit):** `Install-Sara-Windows.zip`, extract it, then run `Install-Sara.bat`
- **Linux:** `install.sh`, run with `sh install.sh`

The installers are plain, readable scripts that check every download against a hash before using it, never need admin rights, and never touch your wallet data. They're unsigned for now, so macOS and Windows will show a one-time warning. Details, what to expect and how to uninstall are in [docs/install.md](docs/install.md). Intel Macs and Windows on ARM aren't supported by the installer yet; use Docker or run from source.

### Docker (the fastest way to try it)

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet
export OPENROUTER_API_KEY=sk-or-...   # https://openrouter.ai/keys
docker compose up --build
```

Then open `http://localhost:8888`. Your wallet database persists in a named Docker volume: `docker compose down` keeps it and `docker compose down -v` deletes it. This is meant for evaluating Sara. It is designed to run directly on your machine so your keys never leave it; see [docs/security-model.md](docs/security-model.md).

### Run from source

Use Python 3.12. Do not use 3.14: the security-fixed LiteLLM release in Sara's reviewed lockfile does not support it.

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet/backend
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-lock.txt
cp ../.env ../.env.local
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8888
```

Then open `http://127.0.0.1:8888`.

### First run

You'll be asked to **create a passphrase**, which protects your wallets' private keys. Remember it: there is no recovery, and if you lose it your existing wallets become permanently undecryptable. Afterwards you unlock with the same passphrase, and Sara auto-locks after 1 hour of inactivity.

Then open **Settings**, add your OpenRouter API key, and pick a model. At any point, type **"How to use Sara"** in the chat for a feature list and your current configuration status.

### Configuration

Set these in **Settings** or in `.env.local`. Only an AI provider is required.

| Setting | Needed? | What it's for |
|---|---|---|
| `OPENROUTER_API_KEY` (or another provider's key, or a local Ollama model) | Required for chat | The AI that reads your requests |
| `ALCHEMY_API_KEY` | Required to swap or bridge; optional otherwise | Sara verifies every swap and bridge before signing and refuses without it. Also token balances and automatic payment reconciliation |
| `COINGECKO_API_KEY` | Optional | Higher rate limit for prices |
| `ETH_RPC`, `ARB_RPC`, `BASE_RPC`, `POLY_RPC`, `OP_RPC` | Optional | Use your own blockchain node instead of public ones ([privacy](docs/privacy.md)) |
| `RISK_SCREENING_PROVIDER`, `_API_URL`, `_API_KEY` | Optional | Broader screening (scams, hacks, mixers) than the built-in sanctions check |
| `RISK_SCREENING_MANDATORY` | Optional, default `false` | Block sends to flagged addresses, or when screening can't run |
| `POLYGONSCAN_API_KEY` | Optional | Submit a deployed token's source for public verification |
| `SARA_NAME_REGISTRAR_ADDRESS`, `SARA_NAME_SERVICE_URL` | Coming soon | Sara Names |

Telegram alerts need no setting: you enter your bot token and chat ID on the **Alerts** page.

## Contributing

We're looking for contributors interested in wallets, stablecoins, x402, AI agents, security and Web3 UX. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first, then look at [good first issues](https://github.com/rohasnagpal/sara-wallet/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22), [help wanted](https://github.com/rohasnagpal/sara-wallet/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) or start a conversation in [Discussions](https://github.com/rohasnagpal/sara-wallet/discussions).

## Documentation

- [docs/architecture.md](docs/architecture.md): directory layout and the frontend, backend, database, AI, chain and tool layers
- [docs/privacy.md](docs/privacy.md): what stays on your machine, who can see what, and how to reduce exposure
- [docs/security-model.md](docs/security-model.md): trust questions, threat model and security philosophy
- [docs/third-party-services.md](docs/third-party-services.md): every external service Sara connects to, which need API keys, and Sara's no-markup policy
- [docs/invoicing.md](docs/invoicing.md), [docs/business-payments.md](docs/business-payments.md), [docs/x402.md](docs/x402.md), [docs/token-creator.md](docs/token-creator.md), [docs/sara-names.md](docs/sara-names.md): feature guides
- [ROADMAP.md](ROADMAP.md): what's Now, Next and Exploring
- [SECURITY.md](SECURITY.md): supported versions, vulnerability reporting, audit status

## License

Apache License 2.0, © 2026 Rohas Nagpal. See [LICENSE](LICENSE).

Sara Wallet is a self-custodial wallet and interface, not a broker, exchange, custodian, investment adviser, trading platform or financial services provider. It helps you interact with third-party networks and protocols; every action is initiated by you and performed through third-party systems at your own risk. See [DISCLAIMER.md](DISCLAIMER.md) for the full legal disclaimer.

<div align="center">
<br />
Sara Wallet is built in India for the world.
<br />
</div>
