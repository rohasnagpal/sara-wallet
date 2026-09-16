"""x402 protocol client (docs.x402.org) - lets Sara pay for and fetch an
HTTP 402-gated resource using one of the wallet's own wallets, signing an
EIP-3009 USDC authorization (gasless for Sara: it's an off-chain signature,
not a transaction - a facilitator service broadcasts it and pays the gas),
then returns the resource.

Mainnet networks: base, ethereum, polygon, arbitrum - each independently
confirmed against x402's own installed default-asset registry
(x402.mechanisms.evm.default_assets.DEFAULT_ASSETS) to use the *exact same*
USDC contract address Sara already trusts (app.core.assets.NETWORKS), with
EIP-3009 support (no "permit2"/no-EIP-3009 marker on those entries).
Optimism is deliberately excluded: it has no entry in x402's own registry
at all, so its USDC's EIP-3009 support isn't confirmed - never assume.

Also: base-sepolia (testnet, free USDC via faucet, no real money). Kept in
a separate dict from app.core.assets.NETWORKS (Sara's *production* network
policy) rather than added there, so a testnet chain never leaks into
balance displays or send flows elsewhere in the app. It's here because
x402's own public default facilitator (x402.org/facilitator, queried live
at facilitator.x402.org/facilitator/supported) currently only supports EVM
settlement on Base Sepolia, not any EVM mainnet - confirmed by asking the
facilitator itself, not assumed - so it's the only network that actually
lets someone test the full buy flow for free before ever risking real USDC.

Every payment only ever authorizes Sara's own trusted USDC contract for the
network - never whatever asset address a 402 response itself claims -
matching the app's existing "token symbols only ever resolve to a
hardcoded, developer-verified contract list" principle (see
app.tools.market.paraswap.trusted_symbols).

Two entry points:
- probe(): sends the request once, unpaid. Returns None if the resource
  doesn't need payment, or the matching trusted-USDC requirement (exact
  price, recipient) if it does - callers use this to decide whether a
  spending policy covers the price *before* ever paying anything.
- pay_and_fetch(): actually pays (if needed) and returns the resource.
"""
from __future__ import annotations

from dataclasses import dataclass

from eth_account import Account

from app.core.assets import NETWORKS

# base-sepolia's chain_id/USDC address, straight from x402's own installed
# DEFAULT_ASSETS registry (x402.mechanisms.evm.default_assets) - not part
# of app.core.assets.NETWORKS since that's Sara's production network list.
_TESTNET_ASSETS = {
    "base-sepolia": {"chain_id": 84532, "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"},
}

# Extend only after independently confirming both the network's USDC
# contract supports EIP-3009 transferWithAuthorization, AND that a
# facilitator that will actually settle on it is reachable - never assume.
SUPPORTED_NETWORKS = ("base", "ethereum", "polygon", "arbitrum", "base-sepolia")
TESTNET_NETWORKS = ("base-sepolia",)


def _network_asset(network: str) -> dict:
    return _TESTNET_ASSETS.get(network) or NETWORKS[network]


class X402Error(Exception):
    pass


@dataclass
class X402Requirement:
    network: str
    asset: str
    amount_raw: str
    pay_to: str


@dataclass
class X402Result:
    status_code: int
    body_text: str
    content_type: str | None
    paid: bool
    tx_hash: str | None = None
    amount_raw: str | None = None
    payer: str | None = None
    pay_to: str | None = None
    network: str | None = None
    asset: str | None = None


def _require_supported(network: str) -> str:
    network = network.lower()
    if network not in SUPPORTED_NETWORKS:
        raise X402Error(f"x402 is only supported on: {', '.join(SUPPORTED_NETWORKS)}")
    return network


def _require_https(url: str) -> None:
    lowered = url.lower()
    if lowered.startswith("https://"):
        return
    # Loopback-only http:// exception, for testing a locally-run seller
    # (e.g. before it's deployed behind real TLS). Traffic never leaves the
    # machine, so this doesn't weaken the https-only rule for anything real.
    from urllib.parse import urlsplit
    host = urlsplit(lowered).hostname or ""
    if lowered.startswith("http://") and host in ("127.0.0.1", "localhost", "::1"):
        return
    raise X402Error("x402 fetch only allows https:// URLs (or http://localhost for local testing)")


def _caip2(network: str) -> str:
    return f"eip155:{_network_asset(network)['chain_id']}"


def _pick_trusted_requirement(accepts, network: str) -> X402Requirement:
    caip2 = _caip2(network)
    trusted_usdc = _network_asset(network)["usdc"].lower()
    for req in accepts:
        if req.network == caip2 and req.asset.lower() == trusted_usdc:
            return X402Requirement(
                network=network, asset=req.asset, amount_raw=req.amount, pay_to=req.pay_to,
            )
    raise X402Error(
        f"The resource's 402 response did not offer payment in Sara's trusted USDC "
        f"contract on {network} - refusing to authorize an unrecognized asset."
    )


async def probe(
    *, url: str, method: str, network: str,
    headers: dict[str, str] | None = None, json_body: dict | None = None,
    timeout_seconds: float = 15.0,
) -> X402Requirement | None:
    """Unpaid request. Returns None if nothing needs paying, else the exact
    trusted-USDC price and recipient - callers must check this against a
    spending policy before ever calling pay_and_fetch()."""
    _require_https(url)
    network = _require_supported(network)

    import httpx
    from x402 import x402Client
    from x402.http.x402_http_client import x402HTTPClient

    async with httpx.AsyncClient(timeout=timeout_seconds) as http:
        response = await http.request(method.upper(), url, headers=headers, json=json_body)
    if response.status_code != 402:
        return None

    http_client = x402HTTPClient(x402Client())
    try:
        body = response.json()
    except Exception:
        body = None
    try:
        payment_required = http_client.get_payment_required_response(response.headers.get, body)
    except Exception as exc:
        raise X402Error(f"Could not parse 402 response: {exc}") from exc
    return _pick_trusted_requirement(payment_required.accepts, network)


async def pay_and_fetch(
    *, url: str, method: str, private_key: str, network: str,
    headers: dict[str, str] | None = None, json_body: dict | None = None,
    timeout_seconds: float = 30.0,
) -> X402Result:
    """Pays (if the resource asks for it, in Sara's trusted USDC contract
    only) and returns the resource. Raises X402Error on anything else."""
    _require_https(url)
    network = _require_supported(network)
    caip2 = _caip2(network)
    trusted_usdc = _network_asset(network)["usdc"].lower()

    from x402 import x402Client
    from x402.http.clients.httpx import wrapHttpxWithPayment
    from x402.http.x402_http_client import x402HTTPClient
    from x402.mechanisms.evm.exact import register_exact_evm_client
    from x402.mechanisms.evm.signers import EthAccountSigner

    def _trusted_selector(version, requirements):
        for req in requirements:
            if req.network == caip2 and req.asset.lower() == trusted_usdc:
                return req
        raise X402Error(
            f"The resource's 402 response did not offer payment in Sara's trusted USDC "
            f"contract on {network} - refusing to authorize an unrecognized asset."
        )

    account = Account.from_key(private_key)
    client = x402Client(payment_requirements_selector=_trusted_selector)
    register_exact_evm_client(client, EthAccountSigner(account), networks=caip2)
    http_client = x402HTTPClient(client)

    try:
        async with wrapHttpxWithPayment(client, timeout=timeout_seconds) as http:
            response = await http.request(method.upper(), url, headers=headers, json=json_body)
    except X402Error:
        raise
    except Exception as exc:
        raise X402Error(str(exc)) from exc

    result = X402Result(
        status_code=response.status_code, body_text=response.text,
        content_type=response.headers.get("content-type"), paid=False,
    )
    try:
        settle = http_client.get_payment_settle_response(response.headers.get)
    except Exception:
        settle = None
    if settle is not None and settle.success:
        result.paid = True
        result.tx_hash = settle.transaction
        result.amount_raw = settle.amount
        result.payer = settle.payer
        result.network = str(settle.network)
        result.asset = trusted_usdc
    return result
