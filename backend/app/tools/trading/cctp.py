"""Circle's Cross-Chain Transfer Protocol (CCTP) v2 — native burn-and-mint
USDC transfers between chains. Unlike app.tools.trading.lifi (a bridge/DEX
*aggregator* that can move any token pair, sometimes via a liquidity pool
or wrapped asset), CCTP only ever does one thing: burn real USDC on the
source chain, mint real, newly-issued USDC on the destination chain — no
wrapped asset, no liquidity pool, no slippage. It's Circle's own official
mechanism, straight from the issuer. LI.FI remains Sara's path for
everything CCTP can't do (non-USDC assets, swaps as part of a route, or
any network pair without a CCTP deployment) — this module is specifically
for a straight USDC-to-USDC move between two of Sara's five EVM networks,
all five of which have a live CCTP v2 deployment.

Every fact below was independently verified before writing anything that
signs a transaction:
  - TokenMessengerV2 and MessageTransmitterV2 sit at the identical address
    on every one of Sara's five networks (Circle deploys both via the same
    CREATE2 factory/salt everywhere) — confirmed live by calling each
    network's deployed MessageTransmitterV2.localDomain() and getting back
    that network's own correct domain id, and by calling
    TokenMessengerV2.localMessageTransmitter() and getting back the exact
    same MessageTransmitterV2 address on every network.
  - depositForBurn / receiveMessage signatures come straight from Circle's
    own contract source (github.com/circlefin/evm-cctp-contracts,
    src/v2/TokenMessengerV2.sol and MessageTransmitterV2.sol) — not a
    third-party summary.
  - The finality-threshold values (1000 = "Fast Transfer", ~8-20s;
    2000 = "Standard Transfer", waits for hard finality, ~13-19 minutes)
    come from Circle's own FinalityThresholds.sol constants
    (FINALITY_THRESHOLD_CONFIRMED / FINALITY_THRESHOLD_FINALIZED) — two
    secondary sources disagreed with each other on this exact point during
    research, which is exactly why the primary source was checked instead
    of picking one.
  - The Iris attestation API's endpoint shape and response fields were
    confirmed by actually calling it live against a real, recent Base
    TokenMessengerV2 DepositForBurn transaction found via eth_getLogs —
    not assumed from documentation alone.

Two chains are involved in every transfer, and — unlike LI.FI or Squid,
which run their own relayer and pay destination-chain gas so the user
only ever signs once — Sara has no always-on server to run a relayer
service like that, so the same wallet signs both legs here: depositForBurn
on the source chain, then (once Circle's attestation is ready)
receiveMessage on the destination chain. receiveMessage is permissionless
by design — anyone with the message + attestation can call it, gas paid
by whoever calls it, and the mint always goes to the address encoded
inside the message regardless of who submits it — so this module could
later be pointed at a third-party relayer instead of self-completing,
without changing the burn side at all. The real, current limitation this
creates: the wallet needs native gas on the destination chain too, not
just the source chain.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from web3 import Web3

from app.core.assets import NETWORKS

_MAX_GAS_LIMIT = 500_000
_MAX_FEE_WEI = 50_000_000_000_000_000  # 0.05 native asset — matches aave.py/paraswap.py's cap

# Identical address on every network below — see module docstring for how
# that was verified (not assumed just because Circle's docs said so).
TOKEN_MESSENGER = "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d"
MESSAGE_TRANSMITTER = "0x81D40F21F12A8F0E3252Bccb954D722d4c464B64"

# CCTP's own per-network "domain" ids — unrelated to EVM chain ids, and
# needed as-is for depositForBurn's destinationDomain parameter and the
# Iris API's URL. Confirmed live against each network's own
# MessageTransmitterV2.localDomain().
DOMAINS = {"ethereum": 0, "optimism": 2, "arbitrum": 3, "base": 6, "polygon": 7}
SUPPORTED_NETWORKS = tuple(DOMAINS)

_IRIS_API = "https://iris-api.circle.com/v2"

# Circle's own named constants (FinalityThresholds.sol) — see module
# docstring for why these specific values, not a guess.
FINALITY_FAST = 1000       # FINALITY_THRESHOLD_CONFIRMED — ~8-20s
FINALITY_STANDARD = 2000   # FINALITY_THRESHOLD_FINALIZED — waits for hard finality

_ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

_TOKEN_MESSENGER_ABI = [
    {"inputs": [
        {"name": "amount", "type": "uint256"}, {"name": "destinationDomain", "type": "uint32"},
        {"name": "mintRecipient", "type": "bytes32"}, {"name": "burnToken", "type": "address"},
        {"name": "destinationCaller", "type": "bytes32"}, {"name": "maxFee", "type": "uint256"},
        {"name": "minFinalityThreshold", "type": "uint32"},
     ], "name": "depositForBurn", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]

_MESSAGE_TRANSMITTER_ABI = [
    {"inputs": [{"name": "message", "type": "bytes"}, {"name": "attestation", "type": "bytes"}],
     "name": "receiveMessage", "outputs": [{"name": "success", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
]


class CctpError(Exception):
    pass


def _require_network(network: str) -> str:
    network = network.lower()
    if network not in SUPPORTED_NETWORKS:
        raise CctpError(f"CCTP is only available on: {', '.join(SUPPORTED_NETWORKS)}")
    return network


def usdc_address(network: str) -> str:
    """Always Sara's own trusted USDC contract — the exact same registry
    every other feature (swaps, x402, Aave) already trusts, never derived
    from anything Circle's own API returns."""
    network = _require_network(network)
    return NETWORKS[network]["usdc"]


def address_to_bytes32(address: str) -> bytes:
    """CCTP encodes every address (mintRecipient, destinationCaller) as a
    left-padded bytes32, since the protocol also supports non-EVM chains
    that use longer native addresses."""
    return bytes(12) + bytes.fromhex(address[2:] if address.startswith("0x") else address)


def _local_fee_fields(w3, call: dict) -> tuple[int, int]:
    estimate = int(w3.eth.estimate_gas(call))
    gas = max(21_000, (estimate * 120 + 99) // 100)
    if gas > _MAX_GAS_LIMIT:
        raise CctpError(f"Refusing to sign: locally estimated gas limit {gas} is excessive.")
    gas_price = int(w3.eth.gas_price * 1.2)
    if gas * gas_price > _MAX_FEE_WEI:
        raise CctpError("Refusing to sign: locally estimated network fee exceeds 0.05 native asset.")
    return gas, gas_price


def _ensure_allowance(w3, account, token_addr: str, spender: str, amount_raw: int, chain_id: int) -> None:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    current = contract.functions.allowance(account.address, Web3.to_checksum_address(spender)).call()
    if current >= amount_raw:
        return
    # Approve exactly the amount being burned — no standing allowance left
    # behind, same principle applied everywhere else in this app.
    call = contract.functions.approve(Web3.to_checksum_address(spender), amount_raw).build_transaction({
        "from": account.address, "nonce": w3.eth.get_transaction_count(account.address),
    })
    gas, gas_price = _local_fee_fields(w3, {"from": account.address, "to": token_addr, "data": call["data"]})
    call.update({"gas": gas, "gasPrice": gas_price, "chainId": chain_id})
    signed = w3.eth.account.sign_transaction(call, account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)


def execute_burn(
    private_key: str, source_network: str, destination_network: str, amount_raw: int,
    recipient_address: str, *, fast: bool = True,
) -> str:
    """Burns amount_raw USDC on source_network, addressed to
    recipient_address on destination_network. Returns the burn
    transaction's hash — fetch_attestation(source_network, tx_hash) is the
    next step, then execute_mint() once it's ready. destinationCaller is
    left as bytes32(0) (anyone may call receiveMessage), matching CCTP's
    own permissionless design rather than restricting it to this same
    wallet, in case a future version wants to complete via a relayer."""
    from app.chains.evm import get_web3, _CHAIN_IDS

    source_network = _require_network(source_network)
    destination_network = _require_network(destination_network)
    if amount_raw <= 0:
        raise CctpError("Amount must be greater than zero")

    w3 = get_web3(source_network)
    chain_id = _CHAIN_IDS[source_network]
    account = w3.eth.account.from_key(private_key)
    asset = usdc_address(source_network)

    _ensure_allowance(w3, account, asset, TOKEN_MESSENGER, amount_raw, chain_id)

    messenger = w3.eth.contract(address=Web3.to_checksum_address(TOKEN_MESSENGER), abi=_TOKEN_MESSENGER_ABI)
    mint_recipient = address_to_bytes32(recipient_address)
    call = messenger.functions.depositForBurn(
        amount_raw, DOMAINS[destination_network], mint_recipient,
        Web3.to_checksum_address(asset), bytes(32), 0,
        FINALITY_FAST if fast else FINALITY_STANDARD,
    ).build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address)})
    gas, gas_price = _local_fee_fields(w3, {"from": account.address, "to": TOKEN_MESSENGER, "data": call["data"]})
    call.update({"gas": gas, "gasPrice": gas_price, "chainId": chain_id})
    signed = w3.eth.account.sign_transaction(call, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return tx_hash.hex()


@dataclass
class Attestation:
    message: str  # 0x-prefixed hex, passed to receiveMessage() as-is
    attestation: str  # 0x-prefixed hex, passed to receiveMessage() as-is


def fetch_attestation(source_network: str, burn_tx_hash: str, *, timeout_seconds: float = 10.0) -> Attestation | None:
    """Polls Circle's Iris API once for the attestation covering a burn
    transaction. Returns None if it isn't ready yet (still the normal case
    for the first several seconds after a Fast Transfer burn) — callers
    poll this themselves rather than the module blocking internally, so a
    caller can decide its own retry cadence/timeout."""
    source_network = _require_network(source_network)
    domain = DOMAINS[source_network]
    url = f"{_IRIS_API}/messages/{domain}"
    try:
        response = httpx.get(url, params={"transactionHash": burn_tx_hash}, timeout=timeout_seconds)
    except Exception as exc:
        raise CctpError(f"Could not reach Circle's attestation service: {exc}") from exc
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except Exception:
        return None
    messages = body.get("messages") or []
    if not messages:
        return None
    entry = messages[0]
    if entry.get("status") != "complete":
        return None
    message = entry.get("message")
    attestation = entry.get("attestation")
    if not message or not attestation:
        return None
    return Attestation(message=message, attestation=attestation)


def wait_for_attestation(
    source_network: str, burn_tx_hash: str, *, max_wait_seconds: float = 60.0, poll_interval_seconds: float = 3.0,
) -> Attestation | None:
    """Convenience loop around fetch_attestation for the common case (a
    Fast Transfer, ready in roughly 8-20s) — returns None rather than
    raising if it simply isn't ready within max_wait_seconds, so a caller
    can surface "still pending, try completing again shortly" instead of
    a hard failure."""
    deadline = time.monotonic() + max_wait_seconds
    while True:
        attestation = fetch_attestation(source_network, burn_tx_hash)
        if attestation is not None:
            return attestation
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_interval_seconds)


def execute_mint(private_key: str, destination_network: str, attestation: Attestation) -> str:
    """Completes a transfer by calling receiveMessage on the destination
    chain with the message + attestation fetched above. Permissionless by
    protocol design — this wallet doesn't have to be the one that pays
    this leg's gas, but today it always is (see module docstring)."""
    from app.chains.evm import get_web3, _CHAIN_IDS

    destination_network = _require_network(destination_network)
    w3 = get_web3(destination_network)
    chain_id = _CHAIN_IDS[destination_network]
    account = w3.eth.account.from_key(private_key)

    transmitter = w3.eth.contract(address=Web3.to_checksum_address(MESSAGE_TRANSMITTER), abi=_MESSAGE_TRANSMITTER_ABI)
    call = transmitter.functions.receiveMessage(
        bytes.fromhex(attestation.message[2:]), bytes.fromhex(attestation.attestation[2:]),
    ).build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address)})
    gas, gas_price = _local_fee_fields(w3, {"from": account.address, "to": MESSAGE_TRANSMITTER, "data": call["data"]})
    call.update({"gas": gas, "gasPrice": gas_price, "chainId": chain_id})
    signed = w3.eth.account.sign_transaction(call, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return tx_hash.hex()
