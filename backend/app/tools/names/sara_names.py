"""Client for the Sara Names registry contract (CLAUDE_STAGES_3_TO_7.md
Stage 6). Fully replaces the earlier tx-history-scanning `.sara`/`.bname`
resolver — the deployed SaraNamesRegistry contract on Polygon Amoy testnet
is now the one authoritative source for ownership/expiry/record-signer/
epoch. Off-chain signed records (addresses, payment preferences) are a
separate concern — see app.tools.names.eip712_records.

Amoy is intentionally hardcoded here, not added to app.core.assets/
app.chains.evm's production network list — Sara Names stays testnet-only
until Stage 7 explicitly authorises a mainnet migration; keeping this
module self-contained means Amoy can never accidentally show up as a
selectable payment network elsewhere in the app.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import json
import re

from eth_utils import keccak
from web3 import Web3

from app.core.resource_paths import backend_path

AMOY_CHAIN_ID = 80002
# __file__-relative would break under a frozen (PyInstaller) build — see
# app/core/resource_paths.py.
_ABI_PATH = backend_path("app", "tools", "names", "registry_abi.json")

# Same DNS-label-style rule as the contract's isValidLabel(): lowercase
# a-z0-9-, no leading/trailing hyphen, 3-63 chars. A full name is one or
# more dot-separated labels (subnames), e.g. "pay.rohas" — dots never
# appear inside a single label.
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
MIN_LABEL_LENGTH = 3
MAX_LABEL_LENGTH = 63


class SaraNamesError(Exception):
    pass


@lru_cache(maxsize=1)
def _load_abi() -> list:
    if not _ABI_PATH.exists():
        raise SaraNamesError(
            f"{_ABI_PATH} is missing — run `cd contracts && forge build && "
            f"python3 scripts/export_artifacts.py` first."
        )
    return json.loads(_ABI_PATH.read_text())["abi"]


def is_valid_label(label: str) -> bool:
    if not (MIN_LABEL_LENGTH <= len(label) <= MAX_LABEL_LENGTH):
        return False
    return bool(_LABEL_RE.match(label))


def validate_label(label: str) -> str | None:
    label = (label or "").strip().lower()
    if not label:
        return "Please provide a name."
    if not is_valid_label(label):
        return (
            "Names can only contain lowercase letters, numbers, and hyphens "
            "(no leading/trailing hyphen), and must be 3-63 characters."
        )
    return None


def validate_name(name: str) -> str | None:
    """Validates every dot-separated label in a possibly-multi-level name
    (e.g. "pay.rohas")."""
    name = (name or "").strip().lower()
    if not name:
        return "Please provide a name."
    for label in name.split("."):
        error = validate_label(label)
        if error:
            return error
    return None


def normalize_name(name: str) -> str:
    return (name or "").strip().lower()


# ── namehash ─────────────────────────────────────────────────────────────

def namehash_label(parent_node: bytes, label: str) -> bytes:
    """Single ENS-style namehash step — mirrors SaraNamesRegistry.namehash()
    exactly (keccak256(parentNode ++ keccak256(label))). Computed locally
    (no RPC) since it's a pure function of its inputs."""
    return keccak(parent_node + keccak(label.encode("utf-8")))


def namehash_name(name: str) -> bytes:
    """Full recursive namehash of a dot-separated name, right-to-left —
    equivalent to composing namehash_label() once per label, parent-first.
    See contracts/test/vectors/namehash_vectors.json for cross-checked
    values this must continue to match."""
    node = b"\x00" * 32
    name = normalize_name(name)
    if not name:
        return node
    for label in reversed(name.split(".")):
        node = namehash_label(node, label)
    return node


def node_hex(name: str) -> str:
    return "0x" + namehash_name(name).hex()


# ── chain access ─────────────────────────────────────────────────────────

def amoy_rpc_urls() -> list[str]:
    """SARA_NAME_AMOY_RPC_URL may be a single URL or a comma-separated list
    — redundant RPC providers (Stage 7 reliability) so one provider's outage
    doesn't take Sara Names down with it."""
    from app.core.config import settings
    return [u.strip() for u in settings.SARA_NAME_AMOY_RPC_URL.split(",") if u.strip()]


def get_web3() -> Web3:
    urls = amoy_rpc_urls()
    if not urls:
        raise SaraNamesError("SARA_NAME_AMOY_RPC_URL is not configured")
    errors = []
    for url in urls:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
            if w3.is_connected():
                return w3
            errors.append(f"{url}: not connected")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise SaraNamesError(f"could not connect to any configured Amoy RPC — {'; '.join(errors)}")


def registry_address() -> str:
    from app.core.config import settings
    if not settings.SARA_NAME_REGISTRAR_ADDRESS:
        raise SaraNamesError("SARA_NAME_REGISTRAR_ADDRESS is not configured — no registry deployed/known yet")
    return Web3.to_checksum_address(settings.SARA_NAME_REGISTRAR_ADDRESS)


def is_configured() -> bool:
    from app.core.config import settings
    return bool(settings.SARA_NAME_REGISTRAR_ADDRESS)


def _contract(w3: Web3):
    return w3.eth.contract(address=registry_address(), abi=_load_abi())


# ── reads ────────────────────────────────────────────────────────────────

def is_available(label: str) -> bool:
    w3 = get_web3()
    return _contract(w3).functions.isAvailable(label).call()


def price_for(label: str, duration_seconds: int) -> int:
    w3 = get_web3()
    return _contract(w3).functions.priceFor(label, duration_seconds).call()


def get_node(node: bytes) -> dict | None:
    w3 = get_web3()
    owner_, expiry, record_signer, record_epoch, owner_generation, parent_node, exists = (
        _contract(w3).functions.getNode(node).call()
    )
    if not exists:
        return None
    return {
        "owner": owner_, "expiry": expiry, "record_signer": record_signer, "record_epoch": record_epoch,
        "owner_generation": owner_generation, "parent_node": "0x" + parent_node.hex(), "exists": exists,
    }


def is_live(node: bytes) -> bool:
    w3 = get_web3()
    return _contract(w3).functions.isLive(node).call()


def resolve(name: str) -> dict | None:
    """Resolves a Sara Name to its current on-chain owner (the fail-safe,
    always-available baseline). Richer multi-chain preference data comes
    from a verified signed record when SARA_NAME_SERVICE_URL is configured
    — see app.routers.names for that layer; this function only ever
    returns what the registry itself can prove."""
    error = validate_name(name)
    if error or not is_configured():
        return None
    node = namehash_name(name)
    info = get_node(node)
    if not info:
        return None
    if not is_live(node):
        return None
    return {"name": name, "node": "0x" + node.hex(), "owner": info["owner"], "expiry": info["expiry"]}


# ── writes (require a decrypted private key from the caller — this module
# never touches wallet storage/passphrases itself) ─────────────────────────

def compute_commitment(label: str, owner_address: str, secret: bytes) -> bytes:
    w3 = get_web3()
    return _contract(w3).functions.computeCommitment(
        label, Web3.to_checksum_address(owner_address), secret
    ).call()


def _send(w3: Web3, private_key: str, build_fn) -> str:
    account = w3.eth.account.from_key(private_key)
    gas_price = w3.eth.gas_price
    base_tx = {
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": gas_price,
        "chainId": AMOY_CHAIN_ID,
    }
    tx = build_fn(base_tx)
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def ensure_usdc_allowance(private_key: str, amount_raw: int) -> str | None:
    """Approves the registry for at least `amount_raw` of Amoy USDC if the
    current allowance is insufficient. Returns the approval tx hash, or
    None if no approval was needed."""
    from app.core.config import settings
    w3 = get_web3()
    account = w3.eth.account.from_key(private_key)
    erc20_abi = [
        {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
         "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
        {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
         "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    ]
    usdc = w3.eth.contract(address=Web3.to_checksum_address(settings.SARA_NAME_AMOY_USDC_ADDRESS), abi=erc20_abi)
    current = usdc.functions.allowance(account.address, registry_address()).call()
    if current >= amount_raw:
        return None
    return _send(w3, private_key, lambda base: usdc.functions.approve(registry_address(), amount_raw).build_transaction(base))


def commit(private_key: str, commitment: bytes) -> str:
    w3 = get_web3()
    contract = _contract(w3)
    return _send(w3, private_key, lambda base: contract.functions.commit(commitment).build_transaction(base))


def register(private_key: str, label: str, owner_address: str, duration_seconds: int, secret: bytes) -> str:
    w3 = get_web3()
    contract = _contract(w3)
    owner_checksum = Web3.to_checksum_address(owner_address)
    return _send(
        w3, private_key,
        lambda base: contract.functions.register(label, owner_checksum, duration_seconds, secret).build_transaction(base),
    )


def renew(private_key: str, label: str, duration_seconds: int) -> str:
    w3 = get_web3()
    contract = _contract(w3)
    return _send(w3, private_key, lambda base: contract.functions.renew(label, duration_seconds).build_transaction(base))


def transfer_root(private_key: str, label: str, new_owner: str) -> str:
    w3 = get_web3()
    contract = _contract(w3)
    new_owner_checksum = Web3.to_checksum_address(new_owner)
    return _send(w3, private_key, lambda base: contract.functions.transferRoot(label, new_owner_checksum).build_transaction(base))


def create_subname(private_key: str, parent_node: bytes, label: str, owner_address: str) -> str:
    w3 = get_web3()
    contract = _contract(w3)
    owner_checksum = Web3.to_checksum_address(owner_address)
    return _send(
        w3, private_key,
        lambda base: contract.functions.createSubname(parent_node, label, owner_checksum).build_transaction(base),
    )


# ── operator/fee-recipient side (Stage 7.3 revenue operations) ────────────

def fee_recipient() -> str:
    w3 = get_web3()
    return _contract(w3).functions.feeRecipient().call()


def accumulated_fees() -> int:
    """The registry's own USDC balance — everything collected and not yet
    withdrawn. Reads the payment token directly rather than trusting any
    local ledger, since this number must always match on-chain reality."""
    from app.core.config import settings
    w3 = get_web3()
    erc20_abi = [{"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
                  "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
    usdc = w3.eth.contract(address=Web3.to_checksum_address(settings.SARA_NAME_AMOY_USDC_ADDRESS), abi=erc20_abi)
    return usdc.functions.balanceOf(registry_address()).call()


def withdraw_fees(private_key: str, amount_raw: int) -> str:
    """The contract itself enforces that only the fee recipient or admin may
    call this — a wallet without that authority gets a revert, surfaced to
    the caller rather than silently failing."""
    w3 = get_web3()
    contract = _contract(w3)
    return _send(w3, private_key, lambda base: contract.functions.withdrawFees(amount_raw).build_transaction(base))


def price_decimal(price_raw: int) -> str:
    return format(Decimal(price_raw) / Decimal(10 ** 6), "f")  # Amoy USDC is 6 decimals, same as mainnet
