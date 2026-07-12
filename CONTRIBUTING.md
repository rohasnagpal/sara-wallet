# Contributing to Sara Wallet

Thanks for your interest in contributing to Sara. This is an open-source project and all kinds of contributions are welcome — new tools, chain support, bug fixes, UI improvements, documentation, and ideas.

---

## Table of Contents

- [Ways to Contribute](#ways-to-contribute)
- [Before You Start](#before-you-start)
- [Setting Up for Development](#setting-up-for-development)
- [Project Structure](#project-structure)
- [Types of Contributions](#types-of-contributions)
  - [Adding a New Tool](#adding-a-new-tool)
  - [Adding a New Chain](#adding-a-new-chain)
  - [Adding a New AI Provider](#adding-a-new-ai-provider)
  - [Frontend Changes](#frontend-changes)
  - [Bug Fixes](#bug-fixes)
- [Pull Request Process](#pull-request-process)
- [Code Style](#code-style)
- [Security](#security)

---

## Ways to Contribute

- **Build a tool** — market data, DeFi, prediction markets, trading, anything useful
- **Add a chain** — new EVM chain, or a non-EVM network
- **Fix a bug** — check the Issues tab for known bugs
- **Improve the UI** — the frontend is a single `index.html`, no build step needed
- **Write docs** — better explanations, examples, edge case notes
- **Report issues** — if something is broken or confusing, open an issue

---

## Before You Start

For anything beyond a small fix, open an issue first and describe what you want to build. This avoids duplicated effort and lets us give early feedback on approach before you invest time writing code.

For small fixes (typos, broken links, obvious bugs), just open a PR directly.

---

## Setting Up for Development

The setup is the same as the main Getting Started guide in the README.

```bash
git clone https://github.com/rohasnagpal/sara-wallet.git
cd sara-wallet/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env` (repo root) to `.env.local` and set at minimum — `.env` is just
the tracked placeholder template, `.env.local` is where the app actually reads
config from and it's gitignored:

```env
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
OPENROUTER_API_KEY=your_key_here
DATABASE_URL=sqlite:///./sara.db
```

There's no `SARA_MASTER_KEY` to set — Sara locks/unlocks like a normal wallet now. The first time you run the app and open it in a browser, you'll be prompted to create a passphrase; that's stored automatically, not in `.env.local`.

Run the server:

```bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Use `--reload` during development so the server restarts automatically on file changes.

---

## Project Structure

```
sara-wallet/
├── index.html              # Entire frontend — UI, chat, settings, address book
└── backend/
    ├── main.py             # FastAPI app entrypoint
    ├── requirements.txt
    └── app/
        ├── routers/        # HTTP route handlers (/api/*)
        ├── tools/          # Wallet, market, trading, and utility functions, routed to by regex intent matching in chat.py
        ├── chains/         # Chain-specific send/balance/gas logic
        ├── db/             # SQLite models and session
        ├── llm/            # AI provider setup via LiteLLM
        └── core/           # Config and shared utilities
```

The key principle: **chat handling and wallet actions are separate**. The chat layer routes intent to tools; the tools and chain modules do the actual work. Keep that separation when adding new functionality.

---

## Types of Contributions

### Adding a New Tool

Tools live in `backend/app/tools/`. Important to understand before you start: **there is no LLM function-calling/tool registry in Sara today.** Routing is a hand-written keyword/regex matcher (`_detect_intent()` in `backend/app/routers/chat.py`), not the AI deciding which tool to call — docstrings are not read by anything and won't affect behavior.

Steps:

1. Create a new file in `backend/app/tools/`, e.g. `my_tool.py`, with your function(s)
2. Add a regex/keyword pattern for your command in `_detect_intent()` that returns `("my_tool_name", {...args})`
3. Add a matching branch in `_handle_tool_call()` (same file) that imports and calls your function, and formats the result as chat text
4. Test it by chatting with Sara: try the natural language commands your tool is meant to handle

Keep tools focused. One tool should do one thing well. If your tool moves funds or signs anything, it needs to go through the existing `_pending`/CONFIRM flow (see `send_crypto`/`swap_tokens` for the pattern) — never execute a money-moving action without an explicit typed CONFIRM.

### Adding a New Chain

Chain modules live in `backend/app/chains/`. Each module handles the specifics of sending transactions, fetching balances, estimating gas, and any chain-specific quirks. There's no separate chain registry — EVM-compatible chains are added directly as entries in the `_RPC`, `_CHAIN_IDS`, and `_NATIVE_TOKEN` dicts at the top of `backend/app/chains/evm.py`.

Steps:

1. For an EVM-compatible chain: add its RPC URL, chain ID, and native token symbol to those three dicts in `evm.py` — no new file needed. Also update `_TOKEN_TO_NETWORK` and `_NETWORK_NATIVE_TOKEN` in `chat.py` so chat commands recognize the new chain's native token.
2. For a genuinely different chain family (like Solana): create a new file in `backend/app/chains/`, e.g. `my_chain.py`, following the interface `evm.py`/`solana.py` already use (balance, transfer preview, send).
3. Document the new RPC env var in `.env` (the tracked template) and the README's Supported Chains table.

### Adding a New AI Provider

Sara connects to AI models exclusively through [OpenRouter](https://openrouter.ai) (one API key, hundreds of models — GPT, Claude, Gemini, Llama, and more), using [LiteLLM](https://github.com/BerriAI/litellm) under the hood. The AI layer lives in `backend/app/llm/`.

In practice this means you usually don't need to add a new provider — just pick a different model from the OpenRouter dropdown in Settings. If you have a real reason to support a provider outside OpenRouter (e.g. a fully local/offline model), open an issue first to discuss the approach before writing code.

### Frontend Changes

The entire frontend is `index.html` at the repo root — no build toolchain, no bundler, no framework. Just HTML, CSS, and vanilla JS (or minimal libraries loaded via CDN).

Keep it that way. The no-build-step design is intentional; it keeps the project accessible and easy to modify.

When making frontend changes: test across Chrome and Firefox, keep the file self-contained, and avoid adding CDN dependencies unless there's a strong reason.

### Bug Fixes

1. Check the issue exists and isn't already being worked on
2. Add a comment on the issue that you're picking it up
3. Fix it, test it, open a PR with a clear description of what was wrong and what you changed

---

## Pull Request Process

1. Fork the repo and create a branch from `main`:

```bash
git checkout -b fix/describe-the-fix
# or
git checkout -b feature/describe-the-feature
```

2. Make your changes. Keep commits focused — one logical change per commit.

3. Test your changes manually. Sara doesn't have an automated test suite yet; make sure the relevant chat commands and UI flows still work.

4. Open a pull request against `main`. In the PR description, include:
   - What the change does
   - How to test it (what commands to try, what to look for)
   - Any decisions or trade-offs worth noting

5. Be responsive to review feedback. PRs that go quiet get closed.

---

## Code Style

Sara's backend is Python. Follow these conventions:

- Use clear, descriptive names. Prefer readability over brevity.
- Don't introduce new dependencies without discussion. Open an issue first if you want to add a package.
- Keep `requirements.txt` updated if you do add a dependency.

For the frontend (`index.html`), keep JavaScript readable and avoid abstractions that obscure what's happening. This is a wallet — the code should be easy to audit.

---

## Security

Sara handles private keys and transaction signing. If you find a security vulnerability, **please do not open a public issue**. Contact the maintainer directly first.

When contributing code that touches wallets, keys, signing, or transaction logic:

- Never log private keys or seed phrases anywhere
- Never transmit key material off the device
- Keep encryption/lock logic in `backend/app/tools/wallet/encrypt.py` and `lock.py` where it can be reviewed in one place — don't scatter key-handling code elsewhere
- Add a comment explaining what you're doing and why — this code gets audited

---

*Sara is open source. Your code. Your keys. Your laptop. Let's keep it that way.*
