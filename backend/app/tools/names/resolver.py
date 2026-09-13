"""Shared recipient-input resolver (CLAUDE_STAGES_3_TO_7.md Stage 7.5:
"Sara Names wherever a recipient is entered"). Extracts the exact
resolution order already proven in app.routers.chat's send flow — a raw
address wins outright, then the local address book, then the Sara Names
registry, never guessing — into one function so it isn't duplicated (and
doesn't drift) across every router that accepts a recipient.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session
from web3 import Web3

_EVM_CAIP = {
    "ethereum": "eip155:1", "polygon": "eip155:137", "arbitrum": "eip155:42161",
    "base": "eip155:8453", "optimism": "eip155:10", "amoy": "eip155:80002",
}


@dataclass
class ResolvedRecipient:
    address: str
    source: str  # "address" | "address_book" | "sara_name_record" | "sara_name"
    input_label: str | None = None  # the nickname/name typed, when source != "address"


def resolve_recipient_input(db: Session, raw_input: str, network: str) -> ResolvedRecipient | None:
    """Returns None if raw_input can't be resolved to any address at all —
    callers must treat that as "not found," never fall back to guessing."""
    raw_input = (raw_input or "").strip()
    if not raw_input:
        return None

    if Web3.is_address(raw_input):
        return ResolvedRecipient(address=Web3.to_checksum_address(raw_input), source="address")

    from app.db.models import AddressBook
    entry = db.query(AddressBook).filter(AddressBook.nickname == raw_input.lower(), AddressBook.chain == "evm").first()
    if entry:
        return ResolvedRecipient(address=entry.address, source="address_book", input_label=raw_input)

    from app.tools.names import eip712_records, sara_names
    name = raw_input.lower()
    if sara_names.is_configured() and sara_names.validate_name(name) is None:
        result = sara_names.resolve(name)
        if result:
            from app.db.models import SaraNameRecordCache
            node = result.get("node") or sara_names.node_hex(name)
            cached = db.query(SaraNameRecordCache).filter(SaraNameRecordCache.node == node).first()
            if cached:
                try:
                    import json
                    fields = json.loads(cached.signed_payload)
                    record = eip712_records.NameRecord(
                        node=fields["node"], record_epoch=int(fields["recordEpoch"]),
                        sequence=int(fields["sequence"]), issued_at=int(fields["issuedAt"]),
                        expires_at=int(fields["expiresAt"]), addresses=fields["addresses"],
                        preferred_network=fields.get("preferredNetwork", ""),
                        preferred_token=fields.get("preferredToken", ""),
                        content_hash=fields["contentHash"],
                    )
                    info = sara_names.get_node(sara_names.namehash_name(name))
                    valid, _ = eip712_records.verify_record(
                        record, cached.signature, chain_id=sara_names.AMOY_CHAIN_ID,
                        registry_address=sara_names.registry_address(), expected_node=node,
                        current_owner=info["owner"], current_record_signer=info["record_signer"],
                        current_epoch=info["record_epoch"],
                    )
                    if valid:
                        caip = _EVM_CAIP.get(network.lower())
                        match = next((a["addr"] for a in record.addresses if a["network"].lower() == caip), None)
                        if match and Web3.is_address(match):
                            return ResolvedRecipient(
                                address=Web3.to_checksum_address(match), source="sara_name_record", input_label=name,
                            )
                except (KeyError, TypeError, ValueError):
                    pass  # malformed/legacy cache is never trusted; use registry owner below
            return ResolvedRecipient(address=result["owner"], source="sara_name", input_label=name)

    return None
