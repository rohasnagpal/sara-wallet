"""EIP-712 signed off-chain Sara Name records (CLAUDE_STAGES_3_TO_7.md
Stage 6.3).

Frequently-changing resolution data (addresses per network, payment
preferences) lives off-chain as a versioned, signed record — the registry
contract never sees these, only tracks ownership/expiry/recordEpoch per
node (app.tools.names.sara_names talks to the contract for that half).

Two distinct serializations exist on purpose:
  - the EIP-712 typed-data structure (native ints/arrays) is what actually
    gets signed/verified via eth_account — the standard already defines its
    own canonical hashing, no need to reinvent one for the signature itself.
  - `content_json()` is the deterministic serialization hashed into
    `content_hash`; `canonical_json()` is the complete persisted envelope,
    including that hash. Neither is the EIP-712 signing serialization.

Verification never trusts anything cached: the caller must supply the
*current* on-chain owner/recordSigner/recordEpoch/last-seen-sequence, fetched
fresh — this module only checks the record against whatever state it's given.
Any failure returns (False, reason); callers must treat that as "no record",
never a stale/wrong address (fail-safe, per the doc).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from web3 import Web3

DOMAIN_NAME = "SaraNames"
DOMAIN_VERSION = "1"


def build_domain(chain_id: int, registry_address: str) -> dict:
    return {
        "name": DOMAIN_NAME,
        "version": DOMAIN_VERSION,
        "chainId": chain_id,
        "verifyingContract": Web3.to_checksum_address(registry_address),
    }


_TYPES = {
    "NetworkAddress": [
        {"name": "network", "type": "string"},   # CAIP-2 identifier, e.g. "eip155:137", "solana:...", "tron:..."
        {"name": "addr", "type": "string"},
    ],
    "SaraNameRecord": [
        {"name": "node", "type": "bytes32"},
        {"name": "recordEpoch", "type": "uint32"},
        {"name": "sequence", "type": "uint64"},
        {"name": "issuedAt", "type": "uint64"},
        {"name": "expiresAt", "type": "uint64"},
        {"name": "addresses", "type": "NetworkAddress[]"},
        {"name": "preferredNetwork", "type": "string"},
        {"name": "preferredToken", "type": "string"},
        {"name": "contentHash", "type": "bytes32"},
    ],
}


@dataclass
class NameRecord:
    node: str  # 0x-prefixed 32-byte hex
    record_epoch: int
    sequence: int
    issued_at: int  # unix seconds
    expires_at: int
    addresses: list[dict] = field(default_factory=list)  # [{"network": "eip155:137", "addr": "0x..."}]
    preferred_network: str = ""
    preferred_token: str = ""
    content_hash: str = "0x" + "00" * 32

    def _canonical_payload(self) -> dict:
        return {
            "node": self.node.lower(),
            "recordEpoch": str(self.record_epoch),
            "sequence": str(self.sequence),
            "issuedAt": str(self.issued_at),
            "expiresAt": str(self.expires_at),
            "addresses": [
                {"network": a["network"], "addr": a["addr"]}
                for a in sorted(self.addresses, key=lambda a: a["network"])
            ],
            "preferredNetwork": self.preferred_network,
            "preferredToken": self.preferred_token,
        }

    def content_json(self) -> str:
        """Canonical fields covered by contentHash (excluding the hash itself)."""
        return json.dumps(self._canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def canonical_json(self) -> str:
        """Complete persisted record, including the value used in EIP-712."""
        payload = self._canonical_payload()
        payload["contentHash"] = self.content_hash.lower()
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def validate_addresses(self) -> tuple[bool, str | None]:
        seen: set[str] = set()
        for entry in self.addresses:
            if not isinstance(entry, dict) or set(entry) != {"network", "addr"}:
                return False, "each address must contain exactly network and addr"
            network = str(entry["network"]).strip().lower()
            address = str(entry["addr"]).strip()
            if not network or network in seen:
                return False, "address networks must be non-empty and unique"
            seen.add(network)
            try:
                if network.startswith("eip155:"):
                    if not Web3.is_address(address):
                        return False, f"invalid EVM address for {network}"
                elif network.startswith("solana:"):
                    from solders.pubkey import Pubkey
                    Pubkey.from_string(address)
                elif network.startswith("tron:"):
                    from tronpy.keys import to_hex_address
                    to_hex_address(address)
                else:
                    return False, f"unsupported network identifier: {network}"
            except Exception:
                return False, f"invalid address for {network}"
        if self.preferred_network and self.preferred_network.lower() not in seen:
            return False, "preferredNetwork must identify one of the record addresses"
        return True, None

    def with_content_hash(self) -> "NameRecord":
        digest = "0x" + keccak(text=self.content_json()).hex()
        return NameRecord(
            node=self.node, record_epoch=self.record_epoch, sequence=self.sequence,
            issued_at=self.issued_at, expires_at=self.expires_at, addresses=self.addresses,
            preferred_network=self.preferred_network, preferred_token=self.preferred_token,
            content_hash=digest,
        )

    def to_typed_message(self) -> dict:
        return {
            "node": bytes.fromhex(self.node[2:] if self.node.startswith("0x") else self.node),
            "recordEpoch": self.record_epoch,
            "sequence": self.sequence,
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
            "addresses": [
                {"network": a["network"], "addr": a["addr"]}
                for a in sorted(self.addresses, key=lambda a: a["network"])
            ],
            "preferredNetwork": self.preferred_network,
            "preferredToken": self.preferred_token,
            "contentHash": bytes.fromhex(self.content_hash[2:] if self.content_hash.startswith("0x") else self.content_hash),
        }


def sign_record(record: NameRecord, *, chain_id: int, registry_address: str, private_key: str) -> str:
    signable = encode_typed_data(
        domain_data=build_domain(chain_id, registry_address),
        message_types={"NetworkAddress": _TYPES["NetworkAddress"], "SaraNameRecord": _TYPES["SaraNameRecord"]},
        message_data=record.to_typed_message(),
    )
    signed = Account.sign_message(signable, private_key=private_key)
    return "0x" + signed.signature.hex()


def recover_signer(record: NameRecord, signature: str, *, chain_id: int, registry_address: str) -> str:
    signable = encode_typed_data(
        domain_data=build_domain(chain_id, registry_address),
        message_types={"NetworkAddress": _TYPES["NetworkAddress"], "SaraNameRecord": _TYPES["SaraNameRecord"]},
        message_data=record.to_typed_message(),
    )
    return Account.recover_message(signable, signature=signature)


def verify_record(
    record: NameRecord, signature: str, *,
    chain_id: int, registry_address: str, expected_node: str,
    current_owner: str, current_record_signer: str, current_epoch: int,
    last_seen_sequence: int = 0, now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Returns (valid, reason). `current_owner`/`current_record_signer`/
    `current_epoch` must be freshly read from the chain by the caller —
    this function never fetches them itself, so it can't silently trust a
    cache. Any failure means "treat as no record," never "use it anyway.\""""
    now = now or datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    if record.node.lower() != expected_node.lower():
        return False, "record node does not match the requested name"
    if record.record_epoch != current_epoch:
        return False, (
            f"record epoch {record.record_epoch} does not match the current on-chain epoch "
            f"{current_epoch} (name was transferred or its signer changed since this record was signed)"
        )
    if record.sequence <= last_seen_sequence:
        return False, f"stale sequence {record.sequence} (last seen {last_seen_sequence})"
    if now_ts < record.issued_at:
        return False, "record is not yet valid (issuedAt is in the future)"
    if now_ts > record.expires_at:
        return False, "record has expired"
    valid_addresses, address_reason = record.validate_addresses()
    if not valid_addresses:
        return False, address_reason

    try:
        signer = recover_signer(record, signature, chain_id=chain_id, registry_address=registry_address)
    except Exception as exc:
        return False, f"could not recover a signer from this signature: {exc}"

    allowed = {a.lower() for a in (current_owner, current_record_signer) if a}
    if signer.lower() not in allowed:
        return False, f"signed by {signer}, which is neither the current owner nor the current record signer"

    if record.content_hash != ("0x" + "00" * 32):
        expected_hash = "0x" + keccak(text=record.content_json()).hex()
        if record.content_hash.lower() != expected_hash.lower():
            return False, "contentHash does not match the record's own canonical JSON"

    return True, None
