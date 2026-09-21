# Privacy

Sara is private by default. There is no account to create, no telemetry or
analytics, no cloud sync, and no server of Sara's own that your data is sent
to. Opening the app contacts no third party: even its fonts are served from
the app itself.

A wallet also has to talk to other systems to do its job, and blockchains are
public. This page says exactly what stays on your machine, who can see what,
and where privacy has limits, so you can decide what is right for you. For
how keys are protected, see [security-model.md](security-model.md).

## What stays on your machine

- **Private keys**, encrypted with your passphrase (AES-256-GCM) and only
  decrypted inside local signing code.
- **Everything Sara remembers about you**: your wallet database, ledger,
  address book, invoices, batches, notes, tags, **chat history**, settings and
  any API keys you enter. It all lives in one local SQLite file
  (`backend/sara.db` by default, or a Docker volume).
- **Transaction signing.** The AI model never signs anything and never sees
  your keys or passphrase.

## Who can see what

| Who | What they can see | When |
|---|---|---|
| **Your AI provider** (OpenRouter by default, or OpenAI, Anthropic, Groq, xAI, Gemini, Cloudflare) | Your chat messages and the replies in the conversation, which can include amounts, recipient names or addresses, and balances (the last 20 messages are sent with each turn). Never keys or your passphrase. | Whenever you chat. With a local **Ollama** model, nothing leaves your machine. |
| **Public blockchain nodes** (publicnode.com, drpc.org, each network's default endpoint) | The addresses you look up and your IP address. | Reading balances and allowances, screening an address, broadcasting a transaction. You can use your own node (see below). |
| **Alchemy** | Your wallet addresses (for token balances and payment matching) and the full transaction you are about to sign (sender, recipient, data). | Sara verifies every swap and bridge with Alchemy before signing and **refuses without an Alchemy key**, so this applies to anyone who swaps or bridges. |
| **LI.FI and ParaSwap** | The tokens, amount, network and your wallet address. | When you swap or bridge. Comparing routes in Treasury does not send your address. |
| **CoinGecko** | Token names or symbols, to fetch prices. Not your addresses. | Prices and market data. |
| **x402 facilitator** | The details of an x402 payment you make. | Only when you pay for an x402 resource. |
| **Telegram** | The alert messages you set up, such as a wallet name and balance, and your bot. | Only if you set up Telegram alerts. |
| **Block explorers** | Nothing from Sara. Links open in your browser. | When you click a link. Optionally, a deployed token's source code is submitted for verification if you set an explorer key. |
| **News and sentiment sources** (CoinTelegraph and CoinDesk feeds, Alternative.me) | Your IP address only. | Market news and sentiment. |
| **The sanctions list** (Chainalysis oracle, read through a public node) | The address you screen. | Only when you screen an address. |

Coming soon: **File proofs** send only a file's SHA-256 fingerprint to
BlockchainProof, never the file, and **Sara Names** will be described here
when it returns.

## What Sara does not do

- No account, sign-up or email address.
- No telemetry, analytics, crash reporting or advertising.
- No cloud sync or backup of your data.
- No affiliate, referral or fee parameters on swaps, bridges or payments.
- The page loads no script, stylesheet, font or image from a third party, and
  its Content-Security-Policy only allows connections to your own Sara.

## Where privacy has limits

- **Blockchains are public.** Every transaction, address and amount you send
  is visible to anyone, permanently. Sara does not hide it, and it is not a
  mixer or an anonymity tool. Using one wallet on several networks links that
  activity together.
- **Your local data is not encrypted, apart from your private keys.** Your
  ledger, address book, notes, chat history, API keys and Telegram bot token
  are stored as ordinary data in `sara.db`. Anyone who can read your computer
  or its backups can read them. Turn on full-disk encryption and keep backups
  private.
- **Your AI provider sees your conversation** and applies its own privacy
  policy. Avoid pasting things into chat that you would not want them to see.
- **Public nodes see your IP address.** Use your own node or a VPN if that
  matters to you.

## Ways to reduce what leaves your machine

1. **Use a local model (Ollama).** Your chats then never leave your computer.
2. **Run your own node.** Set `ETH_RPC`, `ARB_RPC`, `BASE_RPC`, `POLY_RPC` and
   `OP_RPC` in your `.env.local` so balances, screening and broadcasts do not
   go through public nodes.
3. **Leave optional keys blank** where you can (CoinGecko, and Alchemy if you
   never swap or bridge). Without an Alchemy key, payment reconciliation
   falls back to marking invoices paid by hand.
4. **Encrypt your disk** and keep `sara.db` and any backups private.

## Check it yourself

- Sara is open source. Search the code for telemetry or analytics and you will
  find none.
- [third-party-services.md](third-party-services.md) lists every external
  service Sara connects to and why.
- A test (`backend/tests/test_no_third_party_requests.py`) fails if the page
  ever loads a resource from another site.
