# x402 demo: seller + agent

Two standalone scripts that demonstrate Sara's x402 buyer feature
(`Tools -> x402` in the app, `POST /api/x402/fetch` in the backend) end to
end, including the realistic discovery pattern: an agent doesn't know your
URLs up front, it reads a free catalog, decides which listed resource
actually matches its task, and only pays for that one.

These are demo/example code, not part of Sara's own backend - they're
separate processes you run alongside Sara to exercise the feature.

## Files

- **`demo_seller.py`** - a FastAPI site with four different x402-gated
  resources (`/weather`, `/trivia`, `/stock`, `/recipe` - $0.01-$0.02
  each) plus a free `/catalog` endpoint listing all of them with a title,
  description and price. Everything runs on **Base Sepolia** (testnet,
  free faucet USDC, no real money) - the public default facilitator this
  script uses (`x402.org/facilitator`) currently only settles EVM
  payments there, confirmed by querying its own `/supported` endpoint
  live, not assumed.
- **`demo_seller.ts`** - the same demo site, in TypeScript/Express
  (`@x402/express`, `@x402/core`, `@x402/evm`), for anyone building their
  x402 site on Node instead of Python. Same four resources, same prices,
  same network. Use whichever language matches your own site.
- **`local_agent.py`** - a real LLM agent (OpenRouter, tool-calling) with
  two tools: `browse_catalog` (free, reads the list) and
  `fetch_paid_resource` (pays via Sara's x402 endpoint and returns the
  content). You give it a task in plain English; it decides which
  resource to read and calls it itself.

## Setup

```bash
cd /Users/samairahnagpal/sara-wallet/backend
source .venv/bin/activate          # x402[fastapi], httpx, openai are already installed here
```

### 1. Run the demo seller

```bash
export X402_PAY_TO=0xYourWalletAddressHere   # the address that receives payments
uvicorn demo_seller:app --app-dir /Users/samairahnagpal/sara-wallet/examples/x402 --host 127.0.0.1 --port 8001
```

Confirm it's alive: `curl http://127.0.0.1:8001/catalog` should return the
four listed resources; `curl -i http://127.0.0.1:8001/weather` should
return `402 Payment Required`.

#### TypeScript version instead

```bash
cd /Users/samairahnagpal/sara-wallet/examples/x402
npm install                                   # installs express, @x402/*, tsx, typescript
export X402_PAY_TO=0xYourWalletAddressHere
npx tsx demo_seller.ts                        # listens on 127.0.0.1:8002 by default (PORT to change)
```

Same checks apply, against port 8002 instead of 8001. Run either one (not
both need to be up at once) - point Sara/the agent at whichever port you
started.

### 2. Get free testnet USDC to pay with

[faucet.circle.com](https://faucet.circle.com) -> pick **Base Sepolia** ->
send some to whichever Sara wallet address will be paying. No testnet ETH
needed - EIP-3009 payments are gasless for the payer; the facilitator
broadcasts and pays gas.

### 3a. Try it manually first

In Sara: **Tools -> x402** -> pick your wallet -> network **Base Sepolia
(testnet)** -> URL `http://127.0.0.1:8001/weather` (or any of the other
three) -> Fetch. Confirms the whole pipe works before involving an LLM.

### 3b. Run the agent

```bash
export OPENROUTER_API_KEY=sk-or-...          # https://openrouter.ai/keys
export SARA_WALLET_ID=1                      # GET /api/wallets to check which id is which wallet
python3 local_agent.py "Check the catalog at http://127.0.0.1:8001 and get me whichever resource tells me today's weather."
```

Two things need to already be true for the agent to actually pay, not
just report that it needs a human:

1. **Sara must be unlocked.** The agent can't type your passphrase.
2. **A spending policy must cover this wallet + Base Sepolia + USDC**
   (Business -> Policies in Sara) - otherwise `fetch_paid_resource`
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
