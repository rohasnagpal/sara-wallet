"""Pre-signing transaction simulation via Alchemy's Simulate API
(alchemy_simulateAssetChanges) — verifies what a swap/bridge transaction
would ACTUALLY do (real token/native asset movements) before Sara signs it.

A prior version of paraswap.py/lifi.py validated aggregator-provided calldata
by scanning its 32-byte words for the confirmed recipient/token/amount
values. That's bypassable: an attacker (or a compromised/malformed
aggregator response) can call an entirely different, malicious function
while padding the calldata with decoy words that happen to match the
confirmed values without those values having any bearing on what the
transaction actually executes — word *presence* was never proof of word
*use*. Simulation sidesteps this: instead of guessing intent from bytes, it
asks the chain what would really happen and checks that.

Response shape (verified against Alchemy's docs, 2026-07-15):
    result.changes: list of {assetType: NATIVE|ERC20|ERC721|ERC1155|SPECIAL_NFT,
                              changeType: TRANSFER|APPROVAL,
                              from, to, rawAmount (integer string), contractAddress (null for native), ...}
    result.error: null or a string describing why simulation/execution failed.
"""
import os
import requests
from app.chains.evm import ALCHEMY_NETWORK_SLUGS


class SimulationUnavailable(Exception):
    """Raised whenever Sara can't get a trustworthy simulation result at all
    (no API key configured, network unsupported, the call itself failed, or
    the simulated transaction would revert). Callers must treat this as
    "refuse to sign" — silently falling back to no validation at all is
    exactly the false-confidence mistake this module replaces."""


def _simulate(network: str, from_addr: str, to_addr: str, data: str, value_wei: int, *,
              gas: int | None = None, gas_price: int | None = None) -> list[dict]:
    api_key = os.getenv("ALCHEMY_API_KEY", "").strip()
    slug = ALCHEMY_NETWORK_SLUGS.get(network.lower())
    if not api_key or not slug:
        raise SimulationUnavailable(
            f"Sara can't verify this transaction's real effect before signing — that requires an "
            f"ALCHEMY_API_KEY (Settings → Alchemy API Key) and {network.capitalize()} isn't one of the "
            f"chains Alchemy's simulation covers yet either way. Rather than sign a third-party "
            f"aggregator's transaction unverified, Sara refuses."
            if not slug else
            f"Sara can't verify this transaction's real effect before signing without an "
            f"ALCHEMY_API_KEY configured (Settings → Alchemy API Key). Rather than sign a third-party "
            f"aggregator's transaction unverified, Sara refuses."
        )
    url = f"https://{slug}.g.alchemy.com/v2/{api_key}"
    transaction = {
        "from": from_addr, "to": to_addr,
        "data": data or "0x",
        "value": hex(value_wei),
    }
    # A contract can branch on GASPRICE/gasleft. Simulating a transaction
    # with different fee fields from the one Sara later signs creates a
    # validation/execution gap, so callers pass the exact locally-bounded
    # values used for broadcast.
    if gas is not None:
        transaction["gas"] = hex(gas)
    if gas_price is not None:
        transaction["gasPrice"] = hex(gas_price)
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "alchemy_simulateAssetChanges",
        "params": [transaction],
    }
    try:
        resp = requests.post(url, json=payload, timeout=20)
        body = resp.json()
    except Exception as e:
        raise SimulationUnavailable(f"Could not reach Alchemy to simulate this transaction: {e}")
    if "error" in body:
        raise SimulationUnavailable(f"Simulation request was rejected: {body['error']}")
    result = body.get("result") or {}
    if result.get("error"):
        raise SimulationUnavailable(f"This transaction would fail on-chain: {result['error']}")
    return result.get("changes") or []


def _raw_amount(change: dict) -> int:
    raw = change.get("rawAmount")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def verify_swap_effect(network: str, from_addr: str, to_addr: str, data: str, value_wei: int, *,
                        wallet_address: str, expected_src_token: str | None, expected_dst_token: str | None,
                        expected_src_amount: int, expected_min_dst_amount: int,
                        verify_destination: bool = True, gas: int | None = None,
                        gas_price: int | None = None) -> None:
    """Simulates the transaction and confirms the wallet's actual predicted
    balance changes match what the user confirmed:
      - it moves at most the exact expected_src_amount of expected_src_token OUT of the
        wallet, and no *other* asset out at all — that "some other asset
        left the wallet" case is the actual signature of a compromised
        aggregator response draining something unrelated;
      - it moves at least expected_min_dst_amount of expected_dst_token INTO
        the wallet;
      - it grants no new token approvals at all — a plain swap/bridge never
        needs one (allowances are handled separately, before this
        transaction, by ensure_allowance), so an APPROVAL change bundled
        into the swap/bridge transaction itself is never legitimate.
    expected_src_token/expected_dst_token of None means the native asset.
    Raises ValueError (a real, actionable rejection) or SimulationUnavailable
    (couldn't check at all) — both must stop Sara from signing.
    """
    changes = _simulate(
        network, from_addr, to_addr, data, value_wei, gas=gas, gas_price=gas_price,
    )
    wallet = wallet_address.lower()
    src_token = (expected_src_token or "").lower()
    dst_token = (expected_dst_token or "").lower()

    src_out = 0
    dst_in = 0
    for change in changes:
        if change.get("changeType") == "APPROVAL":
            if (change.get("from") or "").lower() == wallet:
                spender = change.get("to", "")
                raise ValueError(
                    f"Refusing to sign: simulation shows this transaction would grant a new token "
                    f"approval to {spender} — a plain swap/bridge never needs to; allowances are "
                    f"handled separately, before this step."
                )
            continue

        is_native = change.get("assetType") == "NATIVE" or not change.get("contractAddress")
        contract = (change.get("contractAddress") or "").lower()
        frm = (change.get("from") or "").lower()
        to = (change.get("to") or "").lower()
        amount = _raw_amount(change)

        if frm == wallet:
            matches_src = (is_native and not expected_src_token) or (not is_native and contract == src_token)
            if not matches_src:
                raise ValueError(
                    f"Refusing to sign: simulation shows this transaction would move an unexpected "
                    f"asset ({change.get('symbol') or contract or 'native asset'}) out of your wallet — "
                    f"not just the confirmed {expected_src_token or 'native asset'}."
                )
            src_out += amount

        if to == wallet:
            matches_dst = (is_native and not expected_dst_token) or (not is_native and contract == dst_token)
            if matches_dst:
                dst_in += amount

    if src_out > expected_src_amount:
        raise ValueError(
            f"Refusing to sign: simulation shows {src_out} leaving your wallet, more than the "
            f"confirmed input amount ({expected_src_amount})."
        )
    # Cross-chain bridge settlement happens later on another network and
    # cannot appear in a source-chain simulation. Bridge callers disable
    # this one check while retaining the source-asset and unexpected-asset
    # checks above; same-chain swaps always require the destination credit.
    if verify_destination and expected_min_dst_amount > 0 and dst_in < expected_min_dst_amount:
        raise ValueError(
            f"Refusing to sign: simulation shows only {dst_in} of the destination asset actually "
            f"returning to your wallet, below the confirmed minimum ({expected_min_dst_amount})."
        )
