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

Create a `.env` in the repo root with at minimum:

```env
LLM_PROVIDER=groq
LLM_MODEL=llama-3.1-8b-instant
GROQ_API_KEY=your_key_here
DATABASE_URL=sqlite:///./sara.db
SARA_MASTER_KEY=dev_passphrase
```

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
        ├── tools/          # Tools the AI can call
        ├── chains/         # Chain-specific send/balance/gas logic
        ├── db/             # SQLite models and session
        ├── llm/            # AI provider setup via LiteLLM
        └── core/           # Config and shared utilities
```

The key principle: **chat handling and wallet actions are separate**. The chat layer routes intent to tools; the tools and chain modules do the actual work. Keep that separation when adding new functionality.

---

## Types of Contributions

### Adding a New Tool

Tools live in `backend/app/tools/`. Each tool is a function (or set of functions) the AI can invoke when it detects relevant intent in the chat.

Steps:

1. Create a new file in `backend/app/tools/`, e.g. `my_tool.py`
2. Write your tool function(s) with clear docstrings — the AI uses these to understand when to call the tool
3. Register the tool in the tools registry (see existing tools for the pattern)
4. Test it by chatting with Sara: try the natural language commands your tool is meant to handle

Keep tools focused. One tool should do one thing well. If you're building something complex, break it into smaller composable tools.

### Adding a New Chain

Chain modules live in `backend/app/chains/`. Each module handles the specifics of sending transactions, fetching balances, estimating gas, and any chain-specific quirks.

Steps:

1. Create a new file in `backend/app/chains/`, e.g. `my_chain.py`
2. Implement the standard interface that other chain modules follow (send, balance, gas estimate)
3. Add the chain to the chain registry in `core/`
4. Add relevant RPC config keys to `.env` if needed, and document them in the README

For EVM-compatible chains, you can likely extend the existing EVM base rather than writing from scratch.

### Adding a New AI Provider

Sara uses [LiteLLM](https://github.com/BerriAI/litellm), so adding a provider is usually just a config change rather than new code. The AI layer lives in `backend/app/llm/`.

If the provider is already supported by LiteLLM, add the relevant `*_API_KEY` to `.env` and document it in the README. If it needs special handling, add it to `backend/app/llm/` and open a PR explaining why LiteLLM alone wasn't sufficient.

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
- Add docstrings to tool functions — the AI layer reads them.
- Don't introduce new dependencies without discussion. Open an issue first if you want to add a package.
- Keep `requirements.txt` updated if you do add a dependency.

For the frontend (`index.html`), keep JavaScript readable and avoid abstractions that obscure what's happening. This is a wallet — the code should be easy to audit.

---

## Security

Sara handles private keys and transaction signing. If you find a security vulnerability, **please do not open a public issue**. Contact the maintainer directly first.

When contributing code that touches wallets, keys, signing, or transaction logic:

- Never log private keys or seed phrases anywhere
- Never transmit key material off the device
- Keep encryption logic in `backend/app/core/` where it can be reviewed in one place
- Add a comment explaining what you're doing and why — this code gets audited

---

*Sara is open source. Your code. Your keys. Your laptop. Let's keep it that way.*
