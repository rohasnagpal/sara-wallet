import re
from urllib.parse import urlparse

from eth_utils import is_address, to_checksum_address


LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_LABEL_LEN = 63


class ValidationError(ValueError):
    pass


def normalize_name(value: str) -> str:
    return (value or "").strip().lower().rstrip(".")


def validate_label(label: str) -> str:
    label = normalize_name(label)
    if not label or len(label) > MAX_LABEL_LEN or not LABEL_RE.fullmatch(label):
        raise ValidationError("labels must be 1-63 lowercase ASCII letters, digits, or internal hyphens")
    return label


def validate_root_name(name: str) -> str:
    name = normalize_name(name)
    labels = name.split(".")
    if len(labels) != 2:
        raise ValidationError("root bNames must be exactly two labels, e.g. alice.wallet")
    return ".".join(validate_label(label) for label in labels)


def split_query_name(name: str) -> tuple[str, str]:
    name = normalize_name(name)
    labels = name.split(".")
    if len(labels) == 2:
        return validate_root_name(name), "@"
    if len(labels) == 3:
        subname = validate_label(labels[0])
        root = validate_root_name(".".join(labels[1:]))
        return root, subname
    raise ValidationError("names must be root.label or sub.root.label in v1")


def validate_subname(subname: str | None) -> str:
    if not subname or subname == "@":
        return "@"
    return validate_label(subname)


def validate_evm_address(address: str) -> str:
    if not is_address(address or ""):
        raise ValidationError("invalid EVM address")
    return to_checksum_address(address)


def validate_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("https", "http"):
        raise ValidationError("URL records must use http or https")
    if not parsed.netloc:
        raise ValidationError("URL records must include a host")
    return url


def validate_record_value(record_type: str, value: str) -> str:
    normalized_type = record_type.upper()
    if normalized_type == "WALLET":
        return validate_evm_address(value)
    if normalized_type in ("URL", "SOCIAL"):
        return validate_url(value)
    if normalized_type == "EMAIL":
        if "@" not in value or len(value) > 320:
            raise ValidationError("invalid email address")
    if normalized_type == "IPFS" and not value.startswith("ipfs://"):
        raise ValidationError("IPFS records must start with ipfs://")
    if not value or len(value) > 4096:
        raise ValidationError("record value must be 1-4096 characters")
    return value
