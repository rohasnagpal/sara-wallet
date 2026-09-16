#!/usr/bin/env python3
"""x402 test seller - multiple x402-gated demo resources plus a free
catalog listing them, for testing an agent that browses a directory and
picks the resource that best matches its task before paying for it. This
mirrors how x402 discovery actually works in the real ecosystem (the
"x402 Bazaar" concept) - an agent doesn't know your URLs up front, it
reads a catalog and decides.

Each resource is on Base SEPOLIA (testnet, free faucet USDC, no real
money). Uses x402's own public default facilitator (x402.org/facilitator)
to verify/settle - no API key needed. I checked its /supported endpoint
live: it currently only settles EVM "exact" payments on Base Sepolia
(eip155:84532), not any EVM mainnet - that's why testnet, not a choice.

Setup:
    pip install "x402[fastapi]"
    export X402_PAY_TO=0xYourWalletAddressHere   # gets the test USDC
    uvicorn demo_seller:app --host 0.0.0.0 --port 8001

Endpoints:
    GET /catalog  - free, lists every paid resource below (url, title,
                    description, price) - an agent reads this first.
    GET /weather  - $0.01 - a fake current-weather snapshot
    GET /trivia   - $0.01 - a random interesting fact
    GET /stock    - $0.02 - a fake stock quote
    GET /recipe   - $0.01 - a simple recipe suggestion

Then put this behind HTTPS (reverse proxy / Caddy / nginx / Cloudflare
Tunnel) and point Sara/agents at https://your-domain.example.com/... -
Sara's x402 client only allows https:// URLs (or http://localhost for
local testing).

Getting free Base Sepolia USDC to pay with: Circle's faucet at
https://faucet.circle.com (pick "Base Sepolia"). No testnet ETH needed -
EIP-3009 payments are gasless for the payer; the facilitator broadcasts
and pays gas.
"""
import os
import random

from fastapi import FastAPI, Request
from x402 import x402ResourceServer
from x402.http import HTTPFacilitatorClient
from x402.http.middleware.fastapi import payment_middleware
from x402.mechanisms.evm.exact import register_exact_evm_server

PAY_TO = os.environ.get("X402_PAY_TO")
if not PAY_TO:
    raise SystemExit("Set X402_PAY_TO to the wallet address that should receive payments.")

NETWORK_CAIP2 = "eip155:84532"  # Base Sepolia

app = FastAPI(title="x402 test seller")

facilitator = HTTPFacilitatorClient()  # defaults to https://x402.org/facilitator
server = x402ResourceServer(facilitator)
register_exact_evm_server(server, networks=NETWORK_CAIP2)


def _accepts(price: str) -> dict:
    return {"scheme": "exact", "payTo": PAY_TO, "price": price, "network": NETWORK_CAIP2}


# Each resource's price, title and description - the catalog below is
# generated from this one place so it can never drift out of sync with
# what's actually gated.
_RESOURCES = {
    "/weather": {"price": "$0.01", "title": "Live weather snapshot", "description": "Current conditions for a fixed demo city."},
    "/trivia": {"price": "$0.01", "title": "Random trivia fact", "description": "One randomly chosen interesting fact."},
    "/stock": {"price": "$0.02", "title": "Demo stock quote", "description": "A placeholder stock price snapshot."},
    "/recipe": {"price": "$0.01", "title": "Recipe of the day", "description": "A simple recipe suggestion."},
}

routes = {f"GET {path}": {"accepts": _accepts(meta["price"])} for path, meta in _RESOURCES.items()}


@app.middleware("http")
async def x402_mw(request, call_next):
    return await payment_middleware(routes, server)(request, call_next)


@app.get("/catalog")
async def catalog(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "resources": [
            {"url": f"{base}{path}", "title": meta["title"], "description": meta["description"], "price": meta["price"]}
            for path, meta in _RESOURCES.items()
        ]
    }


@app.get("/weather")
async def weather():
    return {
        "city": "Demoville", "condition": random.choice(["Sunny", "Cloudy", "Light rain", "Clear skies"]),
        "temperature_c": random.randint(15, 32), "price_paid": _RESOURCES["/weather"]["price"],
    }


_FACTS = [
    "The HTTP 402 status code was reserved in 1997 and left unimplemented for over 25 years.",
    "x402 lets any HTTP response carry its own price tag - no accounts, no API keys.",
    "USDC on Base (and Base Sepolia) typically settles in about 2 seconds.",
    "EIP-3009 payments are gasless for the payer - a facilitator broadcasts and pays gas on their behalf.",
]


@app.get("/trivia")
async def trivia():
    return {"fact": random.choice(_FACTS), "price_paid": _RESOURCES["/trivia"]["price"]}


@app.get("/stock")
async def stock():
    return {
        "symbol": "DEMO", "price_usd": round(random.uniform(50, 500), 2),
        "change_pct": round(random.uniform(-5, 5), 2), "price_paid": _RESOURCES["/stock"]["price"],
    }


_RECIPES = [
    {"name": "5-minute garlic noodles", "ingredients": ["noodles", "garlic", "soy sauce", "chili oil"]},
    {"name": "Tomato & basil toast", "ingredients": ["bread", "tomato", "basil", "olive oil"]},
    {"name": "One-pan lemon chicken", "ingredients": ["chicken", "lemon", "rosemary", "potatoes"]},
]


@app.get("/recipe")
async def recipe():
    r = random.choice(_RECIPES)
    return {**r, "price_paid": _RESOURCES["/recipe"]["price"]}


@app.get("/")
async def root():
    return {"try": "GET /catalog for the free list of paid resources on this demo site"}
