<div align="center">

# Sara AI Wallet

Sara Wallet is a local, self-custodial stablecoin wallet for small businesses: send and receive payments, invoice customers, run batch and recurring payments, and keep an auditable ledger.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-f4a261?style=flat-square)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-e63946?style=flat-square)](ROADMAP.md)
[![CI](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml/badge.svg)](https://github.com/rohasnagpal/sara-wallet/actions/workflows/security.yml)
[![Release](https://img.shields.io/github/v/release/rohasnagpal/sara-wallet?style=flat-square&include_prereleases&label=release&color=e76f51)](https://github.com/rohasnagpal/sara-wallet/releases)
[![GitHub stars](https://img.shields.io/github/stars/rohasnagpal/sara-wallet?style=flat-square&color=f4a261)](https://github.com/rohasnagpal/sara-wallet/stargazers)

<br />
<img width="1436" height="775" alt="image" src="https://github.com/user-attachments/assets/040779b8-4ccf-4d8b-9069-4e79ff059ce8" />

</div>

> **Status: alpha.** Use small amounts and testnets while Sara is under active development. There has been no third-party security audit yet; see [SECURITY.md](SECURITY.md). What is planned next is in [ROADMAP.md](ROADMAP.md).

Sara runs on your own computer. The frontend is a single HTML app and the backend is a Python FastAPI server. It is self-custodial and open source (Apache 2.0): your keys, wallet database and signing all stay on your machine, and every line is auditable.

## What you can do

| Area | What it does | Docs |
|---|---|---|
| **Payments** | Natural-language stablecoin sends with the exact amount and fee shown before you confirm. Swaps and bridges show their route and quote before signing. | |
| **Invoicing** | USDC invoices on Ethereum, Arbitrum, Base, OP Mainnet and Polygon, with a QR code, automatic on-chain reconciliation and proof-of-payment receipts. | [invoicing.md](docs/invoicing.md) |
| **Business and accounting** | Batch payments from a CSV, recurring payments, payroll, spending policies, income and expense reports, CSV and Excel exports. | [business-payments.md](docs/business-payments.md) |
| **Alerts** | Telegram alerts, and balance monitoring that messages you when a wallet crosses a limit. | [business-payments.md](docs/business-payments.md) |
| **Recovery phrase** | One 24-word BIP-39 recovery phrase backs every wallet Sara creates, using the standard Ethereum derivation path, so it also works in MetaMask and other wallets. An existing phrase can be restored before creating a wallet. | [install.md](docs/install.md#back-up-your-recovery-phrase) |
| **Directory handles** | Save an EVM address with a name and unique local handle, such as `Rohas Nagpal` and `rohasnagpal`, and use the handle wherever Sara accepts a recipient. Dotted names are reserved for on-chain naming. | |

### Supported networks

| Network | Gas asset | USDC | EURC | USDT | Open USD (OUSD) |
|---|---|:---:|:---:|:---:|:---:|
| Ethereum | ETH | ✅ | ✅ | ✅ | ✅ |
| Arbitrum | ETH | ✅ | — | ✅ | — |
| Base | ETH | ✅ | ✅ | — | ✅ |
| OP Mainnet | ETH | ✅ | — | ✅ | — |
| Polygon PoS | POL | ✅ | — | ✅ | — |
| Arc | USDC | ✅ | ✅ | — | — |
| Tempo | USD stablecoin | ✅ USDC.e | — | ✅ USDT0 | ✅ |

✅ means Sara can display the balance and make plain sends; — means the asset is not supported on that network. Protocol-specific features such as swaps, bridges and invoices have their own narrower network and asset support.

USDC and EURC use Circle-published contracts ([contract addresses](https://developers.circle.com/stablecoins/usdc-contract-addresses)). Ethereum USDT uses Tether's [official deployment](https://tether.to/en/supported-protocols/); the Arbitrum, OP Mainnet and Polygon entries use the network deployments listed by [USDT0](https://docs.usdt0.to/technical-documentation/deployments). Open USD uses the Ethereum, Base and Tempo contracts published by [Open Standard](https://joinopenstandard.com/blog/ousd-is-live/).

Arc is Circle's network where gas is paid in USDC, so its native balance and USDC balance are the same money and Sara counts it once. On Arc, Sara supports wallets, balances and plain sends. Swaps and bridges are not available on Arc yet.

Tempo has no separate native gas coin. Sara pays a transfer's fee in the TIP-20 stablecoin being sent and requires at least one Tempo stablecoin to remain enabled.

You can enable or hide networks and tokens per network under **Settings → Manage Networks & Tokens**.

### Natural-language sends

Use a raw EVM address, an ENS name, or an exact local Directory handle:

```text
send 10 EURC to rohasnagpal
send 10 EURC to rohasnagpal on base
send 25 USDT to 0x1234... on polygon
send 5 OUSD to rohasnagpal on tempo
```

If no network is named, stablecoin sends default to Ethereum. Add `on <network>` for any other supported pair in the table above. Directory display names are descriptive; commands use the unique handle, so `Rohas Nagpal` can be saved with the handle `rohasnagpal` and addressed without ambiguity.

## Privacy

Sara is private by default: no account, no telemetry, no cloud sync, and the page loads nothing from third parties. Your keys, wallet database and chat history stay on your machine.

Some services necessarily see part of what you do. Your AI provider sees your chat, public blockchain nodes see the addresses you look up, and LI.FI and ParaSwap see your address when you swap or bridge. Blockchains are public, and Sara does not hide on-chain activity. To keep chats local, use a local Ollama model. The full "who sees what" table and ways to reduce exposure are in [docs/privacy.md](docs/privacy.md).

## Security

**Your keys never leave your machine.**

- **Does the AI see my private key?** No. Keys are decrypted only in local signing code and never sent to the AI model.
- **Who signs transactions?** Local Python code, using web3.py, not the AI.
- **Can the AI send money itself?** No. Every send needs your explicit `CONFIRM` and passphrase.

More in [docs/security-model.md](docs/security-model.md), including the threat model. To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Quick start

New installer builds are paused during the current alpha build-out. Use Docker or run from source for testing; installers will return after the feature set is complete.

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

**After every `git pull`, re-run** `python -m pip install -r requirements-lock.txt` inside the same virtualenv. Releases sometimes add a dependency (recovery phrases added `mnemonic`), and skipping this shows up as `ModuleNotFoundError` when Sara starts.

### First run

You'll be asked to **create a passphrase**, which protects your wallets' private keys. The passphrase itself cannot be recovered. If you lose it, restore Sara-created wallets from the recovery phrase in a fresh installation; individually imported keys require their own separate backup. Afterwards you unlock with the same passphrase, and Sara auto-locks after 1 hour of inactivity.

The first time you create a wallet, Sara shows a **24-word recovery phrase** exactly once. Write it down and keep it offline: it can restore your wallets in Sara or in any standard wallet such as MetaMask.

Then open **Settings**, add your OpenRouter API key, and pick a model. At any point, type **"How to use Sara"** in the chat for a feature list and your current configuration status.

### Configuration

Set these in **Settings** or in `.env.local`. Only an AI provider is required.

| Setting | Needed? | What it's for |
|---|---|---|
| `OPENROUTER_API_KEY` (or another provider's key, or a local Ollama model) | Required for chat | The AI that reads your requests |
| `ALCHEMY_API_KEY` | Required to swap or bridge; optional otherwise | Sara verifies every swap and bridge before signing and refuses without it. Also token balances and automatic payment reconciliation |
| `COINGECKO_API_KEY` | Optional | Higher rate limit for prices |
| `ETH_RPC`, `ARB_RPC`, `BASE_RPC`, `POLY_RPC`, `OP_RPC`, `ARC_RPC`, `TEMPO_RPC` | Optional | Use your own blockchain node instead of public ones ([privacy](docs/privacy.md)) |
| `SARA_ENABLED_NETWORKS` | Optional | Comma-separated list of networks to show |
| `SARA_<SYMBOL>_NETWORKS` | Optional | Limit the networks enabled for one stablecoin, for example `SARA_EURC_NETWORKS=ethereum,base` |

Telegram alerts need no setting: you enter your bot token and chat ID on the **Alerts** page.

## Manual alpha smoke test

Before using real funds, complete this short pass with a new local database and test wallets:

1. Set and change the app passphrase; lock and unlock Sara; create a wallet, reveal its recovery phrase, restore that phrase in a fresh test database, and import/export one test key.
2. In **Settings**, verify every enabled network and stablecoin appears in **Balance**. Disable one pair and confirm it disappears, then re-enable it.
3. Add `Rohas Nagpal` with handle `rohasnagpal` in **Directory**. Preview `send 10 EURC to rohasnagpal`, then try explicit-network previews for each checked pair in the support table. Confirm the preview shows the token, network, handle, full address and display name. Cancel unless using a deliberately funded test wallet.
4. Confirm an unsupported pair, such as `send 10 EURC to rohasnagpal on arbitrum`, is rejected rather than substituted.
5. Preview one same-chain swap and one cross-chain bridge. Check the provider, output amount, fees, destination and approval before cancelling or confirming.
6. Create, open, export and delete a USDC invoice on each enabled invoice network; scan its QR in another wallet; verify reconciliation and its receipt with a small test payment.
7. Import and validate a batch CSV; create a schedule and its generated payment; create a payroll profile/run; verify a spending policy blocks an over-limit preview.
8. Review **Ledger** and **Accounts**, edit one classification, and export CSV/XLSX. Add a balance monitor and send a Telegram test alert.
9. Lock Sara and confirm protected actions require unlocking again. Restart the backend and confirm wallets, Directory entries, invoices and ledger records persist.

This checklist validates UI and workflow wiring; it is not a substitute for an independent security audit. Use disposable test wallets and the smallest practical amounts during alpha.

## Contributing

We're looking for contributors interested in wallets, stablecoins, AI agents, security and Web3 UX. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first, then look at [good first issues](https://github.com/rohasnagpal/sara-wallet/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22), [help wanted](https://github.com/rohasnagpal/sara-wallet/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) or start a conversation in [Discussions](https://github.com/rohasnagpal/sara-wallet/discussions).

## Documentation

- [docs/architecture.md](docs/architecture.md): directory layout and the frontend, backend, database, AI, chain and tool layers
- [docs/privacy.md](docs/privacy.md): what stays on your machine, who can see what, and how to reduce exposure
- [docs/security-model.md](docs/security-model.md): trust questions, threat model and security philosophy
- [docs/third-party-services.md](docs/third-party-services.md): every external service Sara connects to, which need API keys, and Sara's no-markup policy
- [docs/invoicing.md](docs/invoicing.md), [docs/business-payments.md](docs/business-payments.md): feature guides
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
