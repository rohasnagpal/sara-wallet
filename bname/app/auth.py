import secrets
from datetime import UTC, datetime, timedelta

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from sqlalchemy.orm import Session

from app.models import Nonce
from app.validation import ValidationError, validate_evm_address


def create_nonce(db: Session, owner_address: str, purpose: str, minutes: int = 10) -> Nonce:
    nonce = Nonce(
        owner_address=validate_evm_address(owner_address),
        nonce=secrets.token_urlsafe(32),
        purpose=purpose,
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
    )
    db.add(nonce)
    db.commit()
    db.refresh(nonce)
    return nonce


def build_signing_message(action: str, fields: dict[str, str]) -> str:
    lines = ["Sara bName Action", f"action: {action}"]
    for key in sorted(fields):
        lines.append(f"{key}: {fields[key]}")
    return "\n".join(lines)


def recover_signer(message: str, signature: str) -> str:
    encoded = encode_defunct(text=message)
    return to_checksum_address(Account.recover_message(encoded, signature=signature))


def verify_nonce_and_signature(
    db: Session,
    expected_owner: str,
    purpose: str,
    nonce_value: str,
    message: str,
    signature: str,
) -> str:
    expected_owner = validate_evm_address(expected_owner)
    nonce = db.query(Nonce).filter(Nonce.nonce == nonce_value, Nonce.purpose == purpose).one_or_none()
    if not nonce:
        raise ValidationError("nonce not found")
    if nonce.used_at is not None:
        raise ValidationError("nonce already used")
    if nonce.expires_at < datetime.now(UTC):
        raise ValidationError("nonce expired")
    signer = recover_signer(message, signature)
    if signer.lower() != expected_owner.lower():
        raise ValidationError("signature signer is not the bName owner")
    nonce.used_at = datetime.now(UTC)
    db.add(nonce)
    return signer
