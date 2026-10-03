"""Resolve a recipient from a raw EVM address or a local Directory alias."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session
from web3 import Web3

@dataclass
class ResolvedRecipient:
    address: str
    source: str  # "address" | "address_book"
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

    return None
