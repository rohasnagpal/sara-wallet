#!/usr/bin/env python3
"""A minimal local AI agent that browses a small directory of paid
resources, picks the one that best matches a given task, and pays for it
via Sara Wallet's x402 buyer - deciding all of this itself (LLM
tool-calling), not scripted.

This is the realistic shape of x402 discovery: an agent doesn't know your
URLs up front. It reads a free catalog (this demo's GET /catalog, or in
the real ecosystem something like the x402 Bazaar), reasons about which
listed resource actually answers its task, and only then pays for that
one - not every resource, not a guess.

Two tools:
    browse_catalog(base_url) -> the free list of paid resources at that
        site (url, title, description, price). Always free, never pays.
    fetch_paid_resource(url) -> pays for and fetches one specific URL via
        Sara's POST /api/x402/fetch, which pays (if the resource asks for
        it) using whichever wallet/network you've configured below.

The agent never sees or holds your Sara passphrase - it can't. Payment
only goes through automatically if a spending policy (Business -> Policies
in Sara) already covers this wallet/network/USDC and the price. Without
one, Sara's endpoint replies "confirmation required" and the agent just
reports that back to you instead of pretending it paid.

Setup:
    pip install openai httpx                 # OpenRouter is OpenAI-API-compatible
    export OPENROUTER_API_KEY=sk-or-...       # https://openrouter.ai/keys
    export SARA_WALLET_ID=1                   # which Sara wallet pays - GET /api/wallets to list
    export SARA_NETWORK=base-sepolia          # testnet by default - safe, free
    export SARA_BASE_URL=http://127.0.0.1:8888
    # optional: export SARA_MODEL=anthropic/claude-haiku-4.5

    python3 local_agent.py "I want to know the weather - check http://127.0.0.1:8001/catalog for options and pick the best one."

Requires: Sara's backend running and unlocked, plus demo_seller.py (or any
other x402-gated site with a catalog-shaped free endpoint) reachable.
"""
from __future__ import annotations

import json
import os
import sys

import httpx
from openai import OpenAI

SARA_BASE_URL = os.environ.get("SARA_BASE_URL", "http://127.0.0.1:8888")
SARA_WALLET_ID = int(os.environ.get("SARA_WALLET_ID", "1"))
SARA_NETWORK = os.environ.get("SARA_NETWORK", "base-sepolia")
MODEL = os.environ.get("SARA_MODEL", "anthropic/claude-haiku-4.5")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise SystemExit("Set OPENROUTER_API_KEY (https://openrouter.ai/keys).")


def _sara_session_token() -> str:
    """Sara injects a per-launch CSRF token into its own root page's HTML -
    this reads it the same way the frontend's own JS does, since there's no
    separate "API key" concept for local callers."""
    html = httpx.get(SARA_BASE_URL + "/", timeout=10.0).text
    marker = "window.__SARA_SESSION__='"
    start = html.find(marker)
    if start == -1:
        raise RuntimeError(f"Could not find Sara's session token - is it running at {SARA_BASE_URL}?")
    start += len(marker)
    end = html.find("'", start)
    return html[start:end]


def browse_catalog(base_url: str) -> dict:
    """Free - reads a site's list of paid resources, never pays anything."""
    try:
        r = httpx.get(base_url.rstrip("/") + "/catalog", timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"error": f"Could not read catalog at {base_url}: {exc}"}


def fetch_paid_resource(url: str) -> dict:
    """Ask Sara to fetch a specific URL, paying via x402 if it demands it.
    Sara handles the whole protocol (probe, price check against your
    spending policy, sign, settle)."""
    token = _sara_session_token()
    r = httpx.post(
        f"{SARA_BASE_URL}/api/x402/fetch",
        headers={"X-Sara-Session": token, "Content-Type": "application/json"},
        json={"wallet_id": SARA_WALLET_ID, "network": SARA_NETWORK, "url": url, "method": "GET"},
        timeout=60.0,
    )
    data = r.json()
    if r.is_error:
        return {"error": data.get("detail", "request failed")}
    if data.get("requires_confirmation"):
        return {
            "error": "payment requires a human passphrase - no spending policy covers this "
                     "wallet/network/price. Set one in Sara (Business -> Policies) to let the "
                     "agent pay automatically, or approve it yourself in the Sara UI first.",
            "price": f"{data['amount']} {data['token']}",
        }
    return {
        "paid": data["paid"], "amount_raw": data.get("amount_raw"),
        "tx_hash": data.get("tx_hash"), "content": data["body"],
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browse_catalog",
            "description": "Read the free list of paid resources (url, title, description, price) "
                            "available at a site, so you can pick the one that best fits the task "
                            "before paying for anything. Always free.",
            "parameters": {
                "type": "object",
                "properties": {"base_url": {"type": "string", "description": "The site's base URL, e.g. http://127.0.0.1:8001"}},
                "required": ["base_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_paid_resource",
            "description": "Fetch one specific URL that may require an x402 micropayment (HTTP 402) "
                            "to access. Pays automatically via Sara Wallet if needed, then returns "
                            "the content. Only call this on a URL you've already decided is the "
                            "right one - each call may cost real (or test) money.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "The exact resource URL to fetch."}},
                "required": ["url"],
            },
        },
    },
]

_DISPATCH = {"browse_catalog": browse_catalog, "fetch_paid_resource": fetch_paid_resource}


def run_agent(goal: str) -> None:
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
    messages = [
        {"role": "system", "content": "You are an agent that can browse a free catalog of paid web "
                                       "resources (browse_catalog) and then pay for and fetch exactly "
                                       "the one that best matches the user's task (fetch_paid_resource). "
                                       "Never fetch a resource you haven't seen in a catalog or that the "
                                       "user didn't explicitly give you, and never fetch more than one "
                                       "resource unless the task genuinely needs it. Report the price "
                                       "paid and transaction hash (if any) in your final answer."},
        {"role": "user", "content": goal},
    ]

    for _ in range(6):  # bounded tool-call loop
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(msg.content)
            return

        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            print(f"[agent] calling {call.function.name}({args}) ...", file=sys.stderr)
            result = _DISPATCH[call.function.name](**args)
            print(f"[agent] tool result: {result}", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})

    print("[agent] stopped after too many tool calls without a final answer.", file=sys.stderr)


if __name__ == "__main__":
    goal = " ".join(sys.argv[1:]) or (
        "Check the catalog at http://127.0.0.1:8001 and get me whichever resource "
        "tells me today's weather."
    )
    run_agent(goal)
