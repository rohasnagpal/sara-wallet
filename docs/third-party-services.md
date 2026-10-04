# Third-Party Services

Sara connects to a number of external services to provide its features.
This page lists every one of them, grounded directly in the code (not
inferred) — what each is used for, whether it needs an API key, and what
happens if you don't configure one.

**Privacy**: what each of these services can see is summarised in
[privacy.md](privacy.md). Opening the Sara page itself contacts none of them
(fonts and all other assets are served locally).

**Fees**: Sara Wallet's own software takes no cut, markup, commission, or
spread on any swap, bridge or send routed through these
services — confirmed by reading the integration code itself (no
referrer/affiliate/fee parameter exists anywhere in the swap or bridge
clients). The third-party services themselves may charge their own
fees or spreads under their own terms, entirely outside Sara's control.
See [../DISCLAIMER.md](../DISCLAIMER.md) for the full legal disclaimer.

## Swaps & bridges

| Service | Used for | API key? |
|---|---|---|
| [Paraswap](https://paraswap.io) | Same-chain EVM swaps (USDC ↔ native gas token) | No |
| [LI.FI](https://li.fi) | Cross-chain bridges + swaps, aggregated | No |

## AI

| Service | Used for | API key? |
|---|---|---|
| [OpenRouter](https://openrouter.ai) (default) — or OpenAI, Anthropic, Groq, xAI, Google Gemini, Cloudflare Workers AI, or a local Ollama model | Chat, natural-language command parsing | **Required** — one of these, set via `LLM_PROVIDER`/the matching `*_API_KEY`. A local Ollama model needs no key and sends nothing off your machine. |

## Market data

| Service | Used for | API key? |
|---|---|---|
| [CoinGecko](https://coingecko.com) | Token prices | Optional — works keyless on the public tier; `COINGECKO_API_KEY` only raises the rate limit |
| [Alchemy](https://alchemy.com) | ERC-20 balance discovery, automatic payment reconciliation, and verifying every swap and bridge (simulating the exact transaction) before signing | Needed to swap or bridge: without `ALCHEMY_API_KEY` Sara refuses to sign them. Optional otherwise — without it, reconciliation falls back to manual "mark as paid" |

## Block explorers

| Service | Used for | API key? |
|---|---|---|
| Same five explorers, link only | "View on explorer" links in the transaction ledger | No — just a URL, no API call |

## Blockchain RPCs

| Service | Used for | API key? |
|---|---|---|
| Public RPC endpoints (publicnode.com, drpc.org, and each network's own default endpoint) | Reading/broadcasting on Ethereum, Arbitrum, Base, OP Mainnet, Polygon | No — overridable via `ETH_RPC`/`ARB_RPC`/`BASE_RPC`/`POLY_RPC`/`OP_RPC` if you want your own |

## Alerts

| Service | Used for | API key? |
|---|---|---|
| Telegram Bot API | Delivering alerts | Only if you set up an alert destination; you create your own bot with @BotFather |
