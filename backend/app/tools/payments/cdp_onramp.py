"""Buy USDC with a card or bank transfer, directly into a Sara wallet —
via Coinbase's CDP Onramp API (docs.cdp.coinbase.com/api-reference/v2/
rest-api/onramp/create-an-onramp-session), publicly documented and
self-serve (GA).

This is deliberately Coinbase's product, not Circle's "Onramp Kit" from
Circle's own Arc App Kits: that one's actual session-creation logic lives
entirely inside `@crcl-main/onramp-kit`, a private npm package that isn't
on the public registry, with no public REST reference either (confirmed:
every api-reference/onramp URL 404s, and Circle's own comprehensive
developers.circle.com/llms.txt index doesn't list a single onramp page).
There's nothing to responsibly verify or implement there, so this uses
Coinbase's separately-built, publicly documented equivalent instead — the
end result for a user (buy USDC with a card, land in a self-custodial
wallet) is the same regardless of which company's product does it.

Authentication reuses the exact CDP JWT scheme already implemented in PHP
for the x402 paywall's CDP facilitator (see x402_paywall_codegen.py) —
Ed25519 ("EdDSA"), per docs.cdp.coinbase.com/api-reference/v2/
authentication — reproduced here in Python via the `cryptography` library
Sara already depends on for AES-GCM, no new dependency needed.

Sara never touches the actual payment. Creating a session only ever asks
CDP for a single-use, Coinbase-hosted checkout URL; the user pays with
their own card/bank directly on Coinbase's page, and the purchased USDC
is sent straight to the destination wallet address Sara supplies — a
completely ordinary on-chain USDC transfer that Sara's existing balance
and portfolio code already handles with no changes at all. There's no
webhook to wire up and nothing to poll: the money either arrives on-chain
or it doesn't, exactly like a transfer from anyone else.
"""
from __future__ import annotations

import base64
import json
import secrets
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CDP_HOST = "api.cdp.coinbase.com"
_SESSIONS_PATH = "/platform/v2/onramp/sessions"
_BASE_URL = f"https://{_CDP_HOST}"


class OnrampError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def build_jwt(key_id: str, key_secret_b64: str, method: str, path: str) -> str:
    """The same CDP JWT scheme already implemented in PHP for the x402
    paywall's CDP facilitator — Ed25519, header {alg, kid, typ, nonce},
    claims {iss, sub, aud, nbf, exp, uri}, 120-second expiry."""
    if not key_id or not key_secret_b64:
        raise OnrampError("A CDP API key id and secret are required")
    now = int(time.time())
    header = {"alg": "EdDSA", "kid": key_id, "typ": "JWT", "nonce": secrets.token_hex(8)}
    claims = {
        "iss": "cdp", "sub": key_id, "aud": ["cdp_service"],
        "nbf": now, "exp": now + 120, "uri": f"{method} {_CDP_HOST}{path}",
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    try:
        raw_secret = base64.b64decode(key_secret_b64, validate=True)
    except Exception as exc:
        raise OnrampError("CDP API key secret must be valid base64") from exc
    if len(raw_secret) != 64:
        raise OnrampError(
            f"CDP API key secret must decode to 64 bytes (32-byte Ed25519 seed + "
            f"32-byte public key) — got {len(raw_secret)}. Only Ed25519 keys are "
            f"supported, not EC/ES256."
        )
    seed = raw_secret[:32]
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    signature = private_key.sign(signing_input.encode())
    return f"{signing_input}.{_b64url(signature)}"


def create_session(
    key_id: str, key_secret: str, *, destination_address: str, network: str,
    payment_amount_usd: str | None = None, country: str | None = None,
) -> dict:
    """Returns {"onramp_url": str, "quote": dict | None}. Sara's caller
    opens onramp_url in a new browser tab (Coinbase's hosted checkout page
    doesn't allow being framed from an arbitrary/local origin, so this is
    never embedded in an iframe) — nothing here signs or moves any of the
    wallet's existing funds."""
    body = {
        "destinationAddress": destination_address,
        "purchaseCurrency": "USDC",
        "destinationNetwork": network,
    }
    if payment_amount_usd:
        body["paymentAmount"] = payment_amount_usd
        body["paymentCurrency"] = "USD"
    if country:
        body["country"] = country

    jwt = build_jwt(key_id, key_secret, "POST", _SESSIONS_PATH)
    try:
        response = httpx.post(
            _BASE_URL + _SESSIONS_PATH, json=body,
            headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
            timeout=20.0,
        )
    except OnrampError:
        raise
    except Exception as exc:
        raise OnrampError(f"Could not reach Coinbase: {exc}") from exc
    if response.status_code >= 400:
        # Deliberately not pre-validated against a hardcoded network list:
        # Coinbase's own asset/network catalog is the actual source of
        # truth and can change, so an unsupported network is surfaced as
        # exactly the error CDP itself gives, not a guess made here.
        raise OnrampError(f"Coinbase refused the request ({response.status_code}): {response.text}")
    try:
        data = response.json()
    except Exception as exc:
        raise OnrampError(f"Coinbase returned an unreadable response: {exc}") from exc
    onramp_url = (data.get("session") or {}).get("onrampUrl")
    if not onramp_url:
        raise OnrampError("Coinbase's response didn't include a checkout URL")
    return {"onramp_url": onramp_url, "quote": data.get("quote")}
