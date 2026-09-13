"""Contract interaction assistant (CLAUDE_STAGES_3_TO_7.md Stage 5.7):
read verified contracts, decode calldata via their real ABI, simulate every
call before Sara signs it, and refuse anything it can't positively vouch
for — an unverified ABI, a proxy/delegatecall-shaped contract, or an
unlimited approval — rather than degrade to "sign it anyway."

Read/write methods are allowlisted (doc: "allowlist supported methods
initially") rather than opened to arbitrary ABI functions; anything else
needs a separately designed expert flow, per the doc's explicit carve-out.
"""
from __future__ import annotations

import json
import hashlib
import hmac
import os

import requests
from web3 import Web3

_EXPLORER_APIS = {
    "polygon": "https://api.polygonscan.com/api",
    "ethereum": "https://api.etherscan.io/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "base": "https://api.basescan.org/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
}

_READ_ALLOWLIST = {"name", "symbol", "decimals", "totalSupply", "balanceOf", "owner", "allowance", "cap", "paused"}
_WRITE_ALLOWLIST = {"approve", "transfer", "transferFrom"}
# 2**255 rather than max uint256 exactly — any approval at or above "half of
# max uint" is functionally unlimited for as long as this contract exists,
# so treat it the same as the canonical infinite-approval value.
_UNLIMITED_APPROVAL_THRESHOLD = 2 ** 255
_PROXY_SIGNALS = ("delegatecall", "_implementation", "upgradeto(", "proxy")


class ContractAssistantError(Exception):
    pass


def _confirmation_token(address: str, network: str, from_address: str, method: str,
                        args: list, value_wei: int) -> str:
    from app.core.session_auth import LAUNCH_TOKEN
    material = json.dumps({
        "address": address.lower(), "network": network.lower(), "from": from_address.lower(),
        "method": method, "args": args, "value_wei": str(value_wei),
    }, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(LAUNCH_TOKEN.encode(), material.encode(), hashlib.sha256).hexdigest()


def _explorer_api(network: str) -> str:
    api = _EXPLORER_APIS.get(network.lower())
    if not api:
        raise ContractAssistantError(f"no source-verification explorer is configured for {network}")
    return api


def fetch_verified_contract(address: str, network: str) -> dict:
    """Fetches the verified ABI + source from the network's block explorer.
    Raises if the contract isn't verified — Sara never guesses at an ABI or
    accepts one supplied by the caller."""
    api_key = os.getenv("POLYGONSCAN_API_KEY", "").strip()
    if not api_key:
        raise ContractAssistantError("POLYGONSCAN_API_KEY is not configured; cannot fetch a verified ABI")
    checksum = Web3.to_checksum_address(address)
    resp = requests.get(
        _explorer_api(network),
        params={"module": "contract", "action": "getsourcecode", "address": checksum, "apikey": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("result") or []
    if not results or not results[0].get("ABI") or results[0]["ABI"] == "Contract source code not verified":
        raise ContractAssistantError(f"{checksum} is not a verified contract on {network}")
    entry = results[0]
    abi = json.loads(entry["ABI"])
    source = (entry.get("SourceCode") or "").lower()
    is_proxy = entry.get("Proxy") == "1" or any(signal in source for signal in _PROXY_SIGNALS)
    return {
        "address": checksum, "abi": abi, "is_proxy": is_proxy,
        "contract_name": entry.get("ContractName", "") or checksum,
    }


def _refuse_if_proxy(contract_info: dict) -> None:
    if contract_info["is_proxy"]:
        raise ContractAssistantError(
            f"{contract_info['address']} looks like a proxy/delegatecall contract — Sara can't verify what "
            f"implementation actually runs behind it, so this is refused until a separately designed expert "
            f"flow safely handles proxy contracts"
        )


def _coerce_args(abi: list, method: str, args: list) -> list:
    candidates = [e for e in abi if e.get("type") == "function" and e.get("name") == method]
    entry = next((e for e in candidates if len(e.get("inputs", [])) == len(args)), None)
    if entry is None:
        return args
    coerced = []
    for value, spec in zip(args, entry.get("inputs", [])):
        kind = spec.get("type", "")
        if isinstance(value, str) and (kind.startswith("uint") or kind.startswith("int")):
            value = int(value, 0)
        elif isinstance(value, str) and kind == "bool":
            if value.lower() not in ("true", "false"):
                raise ContractAssistantError(f"invalid boolean argument: {value}")
            value = value.lower() == "true"
        coerced.append(value)
    return coerced


def read_contract(address: str, network: str, method: str, args: list) -> dict:
    if method not in _READ_ALLOWLIST:
        raise ContractAssistantError(f"'{method}' is not on the allowlist of supported read methods")
    contract_info = fetch_verified_contract(address, network)
    _refuse_if_proxy(contract_info)
    args = _coerce_args(contract_info["abi"], method, args)
    from app.chains.evm import get_web3

    w3 = get_web3(network)
    contract = w3.eth.contract(address=contract_info["address"], abi=contract_info["abi"])
    fn = getattr(contract.functions, method, None)
    if fn is None:
        raise ContractAssistantError(f"{contract_info['contract_name']} has no method '{method}'")
    value = fn(*args).call()
    return {
        "address": contract_info["address"], "contract_name": contract_info["contract_name"],
        "method": method, "args": args, "result": str(value),
    }


def prepare_call(address: str, network: str, from_address: str, method: str, args: list, value_wei: int = 0) -> dict:
    if method not in _WRITE_ALLOWLIST:
        raise ContractAssistantError(f"'{method}' is not on the allowlist of supported write methods")
    contract_info = fetch_verified_contract(address, network)
    _refuse_if_proxy(contract_info)
    args = _coerce_args(contract_info["abi"], method, args)
    if method == "approve" and len(args) >= 2 and int(args[1]) >= _UNLIMITED_APPROVAL_THRESHOLD:
        raise ContractAssistantError(
            "refusing to prepare an unlimited (or near-unlimited) approval — approve an exact amount instead"
        )
    from app.chains.evm import get_web3

    w3 = get_web3(network)
    contract = w3.eth.contract(address=contract_info["address"], abi=contract_info["abi"])
    fn = getattr(contract.functions, method, None)
    if fn is None:
        raise ContractAssistantError(f"{contract_info['contract_name']} has no method '{method}'")
    checksum_from = Web3.to_checksum_address(from_address)
    # gasPrice/nonce/chainId supplied up front (legacy-style tx) so web3.py
    # never falls into its automatic EIP-1559 fee default-fill, which reads
    # the latest block and fails against Polygon's POA-formatted extraData.
    tx = fn(*args).build_transaction({
        "from": checksum_from, "value": value_wei,
        "nonce": w3.eth.get_transaction_count(checksum_from),
        "gasPrice": w3.eth.gas_price, "chainId": w3.eth.chain_id,
    })
    gas_estimate = w3.eth.estimate_gas(tx)
    return {
        "address": contract_info["address"], "contract_name": contract_info["contract_name"],
        "method": method, "args": args, "data": tx["data"], "value_wei": value_wei,
        "estimated_gas": int(gas_estimate * 1.2),
        "confirmation_token": _confirmation_token(
            contract_info["address"], network, checksum_from, method, args, value_wei,
        ),
    }


def execute_reviewed_call(private_key: str, address: str, network: str, method: str,
                          args: list, value_wei: int, confirmation_token: str) -> dict:
    """Rebuild, re-simulate, and execute exactly the call the user reviewed."""
    from app.chains.evm import broadcast_raw_transaction, get_web3

    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    prepared = prepare_call(address, network, account.address, method, args, value_wei)
    if not hmac.compare_digest(prepared["confirmation_token"], confirmation_token):
        raise ContractAssistantError("the contract call changed after review; prepare and review it again")
    simulation = simulate_and_explain(network, account.address, prepared["address"], prepared["data"], value_wei)
    if not simulation.get("safe_to_review"):
        raise ContractAssistantError("simulation did not approve this call for signing")
    tx = {
        "nonce": w3.eth.get_transaction_count(account.address), "to": prepared["address"],
        "value": value_wei, "data": prepared["data"], "gas": prepared["estimated_gas"],
        "gasPrice": w3.eth.gas_price, "chainId": w3.eth.chain_id,
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return {
        "tx_hash": broadcast_raw_transaction(network, signed.raw_transaction.hex()),
        "simulation": simulation,
    }


def simulate_and_explain(network: str, from_address: str, to_address: str, data: str, value_wei: int = 0) -> dict:
    from app.tools.market.tx_simulate import SimulationUnavailable, _simulate

    try:
        changes = _simulate(network, from_address, to_address, data, value_wei)
    except SimulationUnavailable as exc:
        raise ContractAssistantError(str(exc))
    summary = []
    for change in changes:
        kind = change.get("changeType", "CHANGE")
        asset = change.get("symbol") or change.get("contractAddress") or "native asset"
        if kind == "APPROVAL":
            summary.append(f"Grants an approval on {asset} to {change.get('to', '?')}")
        else:
            amount = change.get("rawAmount", "")
            summary.append(f"{kind}: {amount} {asset} from {change.get('from', '?')} to {change.get('to', '?')}")
    return {"changes": changes, "summary": summary, "safe_to_review": True}
