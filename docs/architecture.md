# Architecture

Sara is designed as a local-first wallet and AI assistant. The frontend is a
single HTML app; the backend is a Python FastAPI server. Both run on your
machine.

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

## Frontend

The frontend lives in `index.html`. It provides the wallet UI, chat
interface, settings screen, address book, portfolio views, and local
interaction flows. It communicates with the backend through local API
routes under `/api/*`.

## Backend

The backend is a FastAPI app in `backend/main.py`. It handles:

- Wallet creation and import
- Encrypted private key storage
- Address book entries
- Chat commands
- Transaction preparation and confirmation
- Invoices, payment QR codes, Scan to Pay, receipts, merchant API and automatic reconciliation
- Batch/recurring payments, payroll and authenticated approvals
- Accounting, fiat valuation, FIFO cost basis, reporting and exports
- Token creation/management, allowance controls and transaction simulation
- Treasury, wallet intelligence, risk screening and alerts
- Sara Names registration, resolution, signed records and indexing
- x402 pay-per-call payments, policy-gated for unattended/agent use
- Market data requests
- AI provider integration
- Local SQLite persistence

## Database

Sara uses SQLite by default at `backend/sara.db`. Versioned startup
migrations preserve existing local databases. In addition to wallets and
transactions, the schema stores invoices, receipts, counterparties, batches
and approvals, schedules and payroll, spending policies, accounting
classifications and cost lots, alert/outbox records, token deployments,
risk checks and Sara Names state.

## Wallet Encryption & Locking

Private keys are encrypted (AES-256-GCM) before being stored in SQLite. The
encryption key is derived from a passphrase you set on first run — Sara
holds it in memory only for an unlocked session (auto-expiring after 1 hour
of inactivity), not sitting loaded at all times the way early versions did.
`.env` no longer holds this key. **Private keys never leave your laptop.**

See [security-model.md](security-model.md) for the full threat model and
answers to common trust questions.

## AI Layer

Sara connects to AI models through [OpenRouter](https://openrouter.ai),
giving access to hundreds of models (GPT, Claude, Gemini, Llama, and more)
via one API key. It also supports OpenAI, Anthropic, Groq, xAI, Gemini,
Cloudflare and local Ollama models directly. The AI layer lives in
`backend/app/llm/`.

The AI layer never receives private key material — see
[security-model.md](security-model.md#does-the-ai-see-my-private-key) for
why.

## Chain Layer

Chain-specific logic lives in `backend/app/chains/`.

Transaction tools are kept separate from chat handling so wallet actions
can be validated before execution.

## Tool Layer

Sara's tools live in `backend/app/tools/`, organized into:

- Wallet tools
- Market data tools
- Sara Names and name-resolution tools
- Token creation and management tools
- Contract simulation, allowance and risk tools
- Trading integrations (swaps & cross-chain bridging)
- Payment, invoicing, receipt and reconciliation tools
- x402 client (pay-per-call HTTP fetches, trusted-asset-only)

The chat interface routes user messages into these tools when a command
can be handled deterministically — the model decides *which* tool to call
with *which* parameters, but the tool code itself (not the model) performs
key decryption, transaction building and signing.

---

See also: [security-model.md](security-model.md) ·
[x402.md](x402.md) · [business-payments.md](business-payments.md) ·
[token-creator.md](token-creator.md) · [sara-names.md](sara-names.md) ·
[merchant-api.md](merchant-api.md)
