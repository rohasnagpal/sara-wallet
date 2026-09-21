# Third-Party Services

Sara connects to a number of external services to provide its features.
This page lists every one of them, grounded directly in the code (not
inferred) — what each is used for, whether it needs an API key, and what
happens if you don't configure one.

**Fees**: Sara Wallet's own software takes no cut, markup, commission, or
spread on any swap, bridge, send, or x402 payment routed through these
services — confirmed by reading the integration code itself (no
referrer/affiliate/fee parameter exists anywhere in the swap, bridge, or
x402 client). The third-party services themselves may charge their own
fees or spreads under their own terms, entirely outside Sara's control.
The one exception is **Sara Names** — a separate, Sara-operated on-chain
naming registry with its own transparent, on-chain registration/renewal
pricing (see [sara-names.md](sara-names.md)) — not a markup hidden inside
another service's quote. See [../DISCLAIMER.md](../DISCLAIMER.md) for the
full legal disclaimer.

## Swaps & bridges

| Service | Used for | API key? |
|---|---|---|
| [Paraswap](https://paraswap.io) | Same-chain EVM swaps (USDC ↔ native gas token) | No |
| [LI.FI](https://li.fi) | Cross-chain bridges + swaps, aggregated | No |
| [Jupiter](https://jup.ag) | Solana swaps | No |

## AI

| Service | Used for | API key? |
|---|---|---|
| [OpenRouter](https://openrouter.ai) (default) — or OpenAI, Anthropic, Groq, xAI, Google Gemini, Cloudflare Workers AI, or a local Ollama model | Chat, natural-language command parsing | **Required** — one of these, set via `LLM_PROVIDER`/the matching `*_API_KEY`. A local Ollama model needs no key and sends nothing off your machine. |

## Market data & news

| Service | Used for | API key? |
|---|---|---|
| [CoinGecko](https://coingecko.com) | Token prices | Optional — works keyless on the public tier; `COINGECKO_API_KEY` only raises the rate limit |
| [Alchemy](https://alchemy.com) | ERC-20 balance discovery, automatic payment reconciliation | Optional — without `ALCHEMY_API_KEY`, reconciliation falls back to manual "mark as paid" |
| CoinTelegraph + CoinDesk RSS, [Alternative.me](https://alternative.me) Fear & Greed Index | News headlines and market sentiment | No — free, keyless sources |

## Block explorers

| Service | Used for | API key? |
|---|---|---|
| Etherscan, Arbiscan, Basescan, Optimistic Etherscan, Polygonscan | Optionally submitting the source of a token you deploy for public verification | Optional: without a `POLYGONSCAN_API_KEY` Sara simply skips it (the setting name is legacy, not a sign it's Polygon-only) |
| Same five explorers, link only | "View on explorer" links in the transaction ledger | No — just a URL, no API call |

## Blockchain RPCs

| Service | Used for | API key? |
|---|---|---|
| Public RPC endpoints (publicnode.com, drpc.org, and each network's own default endpoint) | Reading/broadcasting on Ethereum, Arbitrum, Base, OP Mainnet, Polygon | No — overridable via `ETH_RPC`/`ARB_RPC`/`BASE_RPC`/`POLY_RPC`/`OP_RPC` if you want your own |
| Solana public RPC | Solana chain calls | No |
| Bonfida's SNS proxy | Solana Name Service resolution | No |

## Payments & evidence

| Service | Used for | API key? |
|---|---|---|
| x402 facilitator (`x402.org`, or whichever you configure) | Verifying/settling x402 pay-per-call payments | No |
| BlockchainProof | Credential-free file-evidence checkout (see [security-model.md](security-model.md)) | No — no API key or shared billing account by design |

## Address risk screening

| Service | Used for | API key? |
|---|---|---|
| Chainalysis sanctions oracle (public on-chain contract, read through the same public RPC nodes Sara uses for balances) | Checking whether a destination address is on a sanctions list (sanctions only). The address you screen is visible to the RPC node that answers | No |
| Optional provider-neutral adapter | Broader risk screening (scams, hacks, mixers) | Yes, and takes priority when you set `RISK_SCREENING_PROVIDER`/`_API_KEY`/`_API_URL` |

## Alerts

| Service | Used for | API key? |
|---|---|---|
| Telegram Bot API | Delivering alerts | Only if you set up an alert destination; you create your own bot with @BotFather |
