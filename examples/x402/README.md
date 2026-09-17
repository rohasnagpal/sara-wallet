<div align="center">

# x402 Python Agent Demo

**A runnable [x402](https://www.x402.org/) paid API + a tool-calling AI agent that discovers, decides, and pays for it — in Python.**

Part of [Sara AI Wallet](https://github.com/rohasnagpal/sara-wallet), an open-source, local-first, self-custodial AI wallet for stablecoin payments and agentic payments.

</div>

---

## What this is

Two standalone scripts that demonstrate x402 agentic payments end to end,
using Sara Wallet as the buyer:

- **`demo_seller.py`** (or `demo_seller.ts` for Node) — a FastAPI site
  with four x402-gated resources (`/weather`, `/trivia`, `/stock`,
  `/recipe`, $0.01–$0.02 each) plus a free `/catalog` endpoint.
- **`local_agent.py`** — a real LLM agent (OpenRouter, tool-calling) that
  reads the catalog, decides which resource matches its task, and pays
  for only that one via Sara's `POST /api/x402/fetch`.

This mirrors how x402 discovery actually works in the wild (the "x402
Bazaar" pattern): an agent doesn't know your URLs up front, it reads a
catalog and reasons about which listed resource to buy.

These are demo/example scripts, not part of Sara's own backend — separate
processes you run alongside Sara to exercise the feature.

Everything here runs on **Base Sepolia** (testnet, free faucet USDC, no
real money) — the public default facilitator (`x402.org/facilitator`)
currently only settles EVM "exact" payments there, confirmed by querying
its own `/supported` endpoint live, not assumed.

## Architecture

```
                     1. GET /catalog  (free)
   ┌─────────────┐ ─────────────────────────────▶ ┌───────────────────┐
   │ local_agent │                                 │   demo_seller      │
   │ (LLM +      │ ◀───────── catalog JSON ─────── │ (FastAPI, x402-    │
   │  x402 tools)│                                 │  gated resources)  │
   └──────┬──────┘                                 └─────────▲──────────┘
          │ 2. GET /weather  (no payment yet)                 │
          │────────────────────────────────────────────────────
          │◀─────────────── 402 Payment Required ──────────────
          │
          │ 3. POST /api/x402/fetch  { url: /weather }
          ▼
   ┌──────────────┐  4. GET /weather + X-PAYMENT header (signed)
   │ Sara backend │─────────────────────────────────────────────▶
   │ (your wallet,│◀──────────────── 200 OK + resource ───────────
   │  your keys)  │
   └──────────────┘
```

The agent never sees or holds your Sara passphrase, and never touches a
private key — it calls Sara's local API, and Sara's backend (not the
model) builds, signs and settles the payment. See
[../../docs/security-model.md](../../docs/security-model.md) for how that
boundary is enforced in code.

## 2-minute setup

```bash
# 1. From your sara-wallet clone
cd sara-wallet/backend
source .venv/bin/activate            # x402[fastapi], httpx, openai already in requirements-lock.txt

# 2. Start the demo seller (pick any address for now — it just receives testnet USDC)
export X402_PAY_TO=0xYourWalletAddressHere
uvicorn demo_seller:app --app-dir ../examples/x402 --host 127.0.0.1 --port 8001
```

In another terminal, confirm it's alive:

```bash
curl -s http://127.0.0.1:8001/catalog
```

```json
{
    "resources": [
        {"url": "http://127.0.0.1:8001/weather", "title": "Live weather snapshot", "description": "Current conditions for a fixed demo city.", "price": "$0.01"},
        {"url": "http://127.0.0.1:8001/trivia", "title": "Random trivia fact", "description": "One randomly chosen interesting fact.", "price": "$0.01"},
        {"url": "http://127.0.0.1:8001/stock", "title": "Demo stock quote", "description": "A placeholder stock price snapshot.", "price": "$0.02"},
        {"url": "http://127.0.0.1:8001/recipe", "title": "Recipe of the day", "description": "A simple recipe suggestion.", "price": "$0.01"}
    ]
}
```

That's the free catalog — no payment needed. Now try a gated resource
without paying:

```bash
curl -i http://127.0.0.1:8001/weather
```

```
HTTP/1.1 402 Payment Required
content-type: application/json
payment-required: eyJ4NDAyVmVyc2lvbiI6Mi...   (base64 — decodes to the JSON below)
cache-control: no-store

{}
```

Decoding the `payment-required` header (this is real, captured output —
not a mock-up):

```json
{
    "x402Version": 2,
    "error": "Payment required",
    "resource": {"url": "http://127.0.0.1:8001/weather", "description": "", "mimeType": ""},
    "accepts": [{
        "scheme": "exact",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "amount": "10000",
        "payTo": "0xYourWalletAddressHere",
        "maxTimeoutSeconds": 300,
        "extra": {"name": "USDC", "version": "2"}
    }]
}
```

That's the whole protocol in one response: what it costs, which asset,
which network, where it goes. No account, no API key, on either side.
Sara's `POST /api/x402/fetch` reads exactly this and pays it for you.

### TypeScript version instead

```bash
cd sara-wallet/examples/x402
npm install                                   # installs express, @x402/*, tsx, typescript
export X402_PAY_TO=0xYourWalletAddressHere
npx tsx demo_seller.ts                        # listens on 127.0.0.1:8002 by default (PORT to change)
```

Same checks apply, against port 8002 instead of 8001. Run either one (not
both need to be up at once) — point Sara/the agent at whichever port you
started.

## Base Sepolia free testing

No real money is involved anywhere in this demo:

1. Get free testnet USDC: [faucet.circle.com](https://faucet.circle.com) →
   pick **Base Sepolia** → send some to whichever Sara wallet address will
   be paying.
2. No testnet ETH needed — EIP-3009 payments are gasless for the payer;
   the facilitator broadcasts and pays the gas.

## Try it manually first

In Sara: **Tools → x402** → pick your wallet → network **Base Sepolia
(testnet)** → URL `http://127.0.0.1:8001/weather` (or any of the other
three) → Fetch. This confirms the whole pipe works before involving an
LLM.

## Run the agent

```bash
export OPENROUTER_API_KEY=sk-or-...          # https://openrouter.ai/keys
export SARA_WALLET_ID=1                      # GET /api/wallets to check which id is which wallet
python3 local_agent.py "Check the catalog at http://127.0.0.1:8001 and get me whichever resource tells me today's weather."
```

Two things need to already be true for the agent to actually pay, not
just report that it needs a human:

1. **Sara must be unlocked.** The agent can't type your passphrase.
2. **A spending policy must cover this wallet + Base Sepolia + USDC**
   (Business → Policies in Sara) — otherwise `fetch_paid_resource`
   correctly comes back with "payment requires a human passphrase"
   instead of the agent guessing or getting stuck.

Watch stderr while it runs to see which tools it calls and why:

```
[agent] calling browse_catalog({'base_url': 'http://127.0.0.1:8001'}) ...
[agent] tool result: {'resources': [...]}
[agent] calling fetch_paid_resource({'url': 'http://127.0.0.1:8001/weather'}) ...
[agent] tool result: {'paid': True, 'tx_hash': '0x...', 'content': '...'}
```

## Going beyond localhost

Sara's x402 client only allows `https://` URLs (plus `http://localhost`/
`127.0.0.1` specifically for this kind of local testing). To actually put
`demo_seller.py` on a real site, put it behind a reverse proxy with real
TLS (nginx, Caddy, Cloudflare Tunnel) and point Sara/the agent at
`https://your-domain.example.com/...` instead.

## Files

| File | What it is |
|---|---|
| `demo_seller.py` | x402-gated FastAPI demo site (Python) |
| `demo_seller.ts` | Same demo site, TypeScript/Express (`@x402/*`) |
| `local_agent.py` | Tool-calling LLM agent that browses + pays via Sara |

## Roadmap

Broadening seller/network coverage and policy-gating scenarios beyond this
demo is tracked in [Sara's roadmap](../../ROADMAP.md) (Now) —
[issue #3](https://github.com/rohasnagpal/sara-wallet/issues/3).

---

<div align="center">

Built on [Sara AI Wallet](https://github.com/rohasnagpal/sara-wallet) — an open-source, self-custodial, local-first AI wallet for crypto payments, stablecoin invoicing, crypto payroll, ERC-20 token creation and x402 agentic payments.

⭐ [Star the repo](https://github.com/rohasnagpal/sara-wallet/stargazers) if this is useful to you.

</div>
