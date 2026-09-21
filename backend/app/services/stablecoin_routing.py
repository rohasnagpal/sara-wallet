"""Stablecoin routing comparison (CLAUDE_STAGES_3_TO_7.md Stage 5.4).

Compares delivered amount, fees, gas and time across Sara's existing,
already-hardened aggregator integrations (app.tools.market.paraswap for
same-chain swaps, app.tools.trading.lifi for same-chain or cross-chain
bridges) and returns a ranked recommendation with reasons. This module only
*compares quotes* — execution reuses the existing execute_swap/execute_bridge
functions (validated executor/spender/value/simulated-effect checks already
built for Stage 0-2), never a new signing path.

Field names below are read from each provider's real, live response shape
(verified 2026-09-13), not guessed from docs. Neither API returns an
explicit quote-expiry timestamp — both are documented here as valid for
immediate use only, and callers must re-quote immediately before execution
rather than caching a quote across a confirmation step.
"""
from __future__ import annotations

from decimal import Decimal

_NO_EXPIRY_NOTE = "not provided by this API's quote response — treat as valid for immediate use only, re-quote before executing"
_STABLECOINS = {"USDC", "USDT", "DAI"}
_PROVIDER_SECURITY = {
    "paraswap": "same-chain route; no bridge finality risk",
    "lifi": "aggregated route; cross-chain routes add bridge and destination-finality risk",
}


def _paraswap_route(from_network: str, from_addr: str, from_dec: int, to_addr: str, to_dec: int, amount_raw: int) -> dict | None:
    from app.tools.market import paraswap

    quote = paraswap.get_quote(from_addr, from_dec, to_addr, to_dec, amount_raw, from_network)
    price_route = (quote or {}).get("priceRoute")
    if not price_route or not price_route.get("destAmount"):
        return None
    return {
        "provider": "paraswap", "kind": "same_chain_swap",
        "delivered_amount_raw": price_route["destAmount"], "delivered_decimals": to_dec,
        "gas_cost_usd": price_route.get("gasCostUSD"), "fee_usd": None,
        "estimated_seconds": None, "expiry": _NO_EXPIRY_NOTE,
    }


# LI.FI picks one route per request. Its cheapest and fastest are often
# different bridges (e.g. a slow standard bridge vs. a near-instant one that
# costs a couple of cents more), so ask for both and show both when they differ.
_LIFI_ORDERS = ("CHEAPEST", "FASTEST")


def _lifi_routes(from_network: str, to_network: str, from_addr: str, to_addr: str, to_dec: int,
                 amount_raw: int, from_address: str) -> list[dict]:
    from app.tools.trading import lifi

    routes: list[dict] = []
    seen: set[tuple] = set()
    for order in _LIFI_ORDERS:
        quote = lifi.get_quote(from_network, to_network, from_addr, to_addr, amount_raw, from_address, order=order)
        estimate = (quote or {}).get("estimate")
        if not estimate or not estimate.get("toAmount"):
            continue
        fee_usd = sum((Decimal(f["amountUSD"]) for f in estimate.get("feeCosts", []) if f.get("amountUSD")), Decimal(0))
        gas_usd = sum((Decimal(g["amountUSD"]) for g in estimate.get("gasCosts", []) if g.get("amountUSD")), Decimal(0))
        tool = ((quote.get("toolDetails") or {}).get("name") or quote.get("tool"))
        key = (tool, estimate["toAmount"], estimate.get("executionDuration"))
        if key in seen:
            continue  # cheapest and fastest are the same route
        seen.add(key)
        routes.append({
            "provider": "lifi", "tool": tool,
            "kind": "same_chain_swap" if from_network.lower() == to_network.lower() else "cross_chain_bridge",
            "delivered_amount_raw": estimate["toAmount"], "delivered_amount_min_raw": estimate.get("toAmountMin"),
            "delivered_decimals": to_dec,
            "gas_cost_usd": str(gas_usd) if gas_usd else None, "fee_usd": str(fee_usd) if fee_usd else None,
            "estimated_seconds": estimate.get("executionDuration"), "expiry": _NO_EXPIRY_NOTE,
        })
    return routes


def compare_routes(*, from_network: str, to_network: str, from_token: str, to_token: str,
                    amount: str, from_address: str) -> dict:
    from app.tools.market import paraswap as paraswap_tool
    from app.tools.trading import lifi as lifi_tool
    from app.core.amounts import to_base_units

    from_network = from_network.lower()
    to_network = to_network.lower()
    same_chain = from_network == to_network

    warnings: list[str] = []
    resolved_from = lifi_tool.resolve_token(from_token, from_network)
    resolved_to = lifi_tool.resolve_token(to_token, to_network)
    if not resolved_from or not resolved_to:
        return {"routes": [], "warnings": [f"{from_token} or {to_token} could not be resolved on the requested network(s)"]}
    from_addr, from_dec = resolved_from
    to_addr, to_dec = resolved_to
    try:
        amount_raw = to_base_units(amount, from_dec, from_token)
    except ValueError as exc:
        return {"routes": [], "warnings": [str(exc)]}

    routes: list[dict] = []
    if same_chain:
        try:
            pa_from = paraswap_tool.resolve_token(from_token, from_network)
            pa_to = paraswap_tool.resolve_token(to_token, to_network)
            if pa_from and pa_to:
                route = _paraswap_route(from_network, pa_from[0], pa_from[1], pa_to[0], pa_to[1], amount_raw)
                if route:
                    routes.append(route)
        except Exception as exc:
            warnings.append(f"Paraswap quote failed: {exc}")

    try:
        routes.extend(_lifi_routes(from_network, to_network, from_addr, to_addr, to_dec, amount_raw, from_address))
    except Exception as exc:
        warnings.append(f"LI.FI quote failed: {exc}")

    for route in routes:
        route["delivered_amount"] = format(Decimal(route["delivered_amount_raw"]) / (Decimal(10) ** route["delivered_decimals"]), "f")
        fee = Decimal(str(route.get("fee_usd") or "0"))
        gas = Decimal(str(route.get("gas_cost_usd") or "0"))
        if to_token.upper() in _STABLECOINS:
            route["estimated_net_value_usd"] = format(Decimal(route["delivered_amount"]) - fee - gas, "f")
        else:
            route["estimated_net_value_usd"] = None
        route["security_note"] = _PROVIDER_SECURITY.get(route["provider"], "provider security not assessed")

    routes.sort(
        key=lambda r: (
            Decimal(r["estimated_net_value_usd"] or r["delivered_amount"]),
            -int(r.get("estimated_seconds") or 10**9),
            r["kind"] == "same_chain_swap",
        ),
        reverse=True,
    )
    reasons = []
    for i, route in enumerate(routes):
        route["recommended"] = i == 0
        if i == 0 and len(routes) > 1:
            reasons.append(
                f"{route['provider']}{' via ' + route['tool'] if route.get('tool') else ''} has the highest estimated net value after quoted fees and gas "
                f"({route['estimated_net_value_usd'] or route['delivered_amount']})"
            )
    if not routes:
        warnings.append("no route could be quoted for this pair/network combination")

    return {
        "from_network": from_network, "to_network": to_network, "from_token": from_token.upper(),
        "to_token": to_token.upper(), "amount": amount, "routes": routes, "recommendation_reasons": reasons,
        "warnings": warnings,
    }
