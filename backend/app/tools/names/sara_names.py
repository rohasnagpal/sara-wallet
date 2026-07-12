import os
import re
import requests

# Allowed suffixes for bNames. Add new endings here — resolution, send-flow
# detection, and registration parsing all derive from this single list.
SUFFIXES = (".sara", ".bname")
DEFAULT_SUFFIX = SUFFIXES[0]

CALLDATA_PREFIX = "SARA1:"
_POLYGONSCAN_URL = "https://api.polygonscan.com/api"

# DNS-label-style rule for the part before the suffix: lowercase letters,
# digits, and internal hyphens only; must start/end alphanumeric; 2-63 chars.
# This blocks characters that would break the "SARA1:name:address" calldata
# format (e.g. a colon) and blocks Unicode lookalike/homoglyph spoofing
# (e.g. a Cyrillic "а" impersonating a Latin "a") by restricting to ASCII.
_PREFIX_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')


def validate_name(name: str) -> str | None:
    """Returns an error message if the name is invalid, or None if it's fine.
    Auto-appends the default suffix if the name has no recognized ending.
    """
    name = (name or "").strip().lower()
    if not name:
        return "Please provide a name."
    if not name.endswith(SUFFIXES):
        name += DEFAULT_SUFFIX
    suffix = next(s for s in SUFFIXES if name.endswith(s))
    prefix = name[: -len(suffix)]
    if not _PREFIX_RE.match(prefix):
        return (
            "Names can only contain lowercase letters, numbers, and hyphens "
            "(no leading/trailing hyphen), and must be 2-63 characters before "
            f"the `{suffix}` ending."
        )
    return None


def normalize_name(name: str) -> str:
    """Lowercase + auto-append the default suffix if none was given.
    Call validate_name() first to check it's actually valid."""
    name = (name or "").strip().lower()
    if not name.endswith(SUFFIXES):
        name += DEFAULT_SUFFIX
    return name


def _decode_calldata(input_hex: str) -> str | None:
    try:
        raw = input_hex[2:] if input_hex.startswith("0x") else input_hex
        text = bytes.fromhex(raw).decode("utf-8")
        return text if text.startswith(CALLDATA_PREFIX) else None
    except Exception:
        return None


def resolve(name: str) -> str | None:
    """Resolve a bName to a wallet address by scanning the registrar's
    on-chain transaction history directly — no local cache, no server call.
    """
    if not name.lower().endswith(SUFFIXES):
        return None
    log_address = os.getenv("SARA_NAME_LOG_ADDRESS", "")
    registrar_address = os.getenv("SARA_NAME_REGISTRAR_ADDRESS", "")
    if not log_address or not registrar_address:
        return None
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": log_address,
            "startblock": 0,
            "endblock": 99999999,
            "sort": "asc",
        }
        api_key = os.getenv("POLYGONSCAN_API_KEY")
        if api_key:
            params["apikey"] = api_key
        r = requests.get(_POLYGONSCAN_URL, params=params, timeout=15)
        if not r.ok:
            return None
        data = r.json()
        target = name.strip().lower()
        result = None
        for tx in data.get("result", []) or []:
            if (tx.get("from") or "").lower() != registrar_address.lower():
                continue
            if tx.get("isError") not in ("0", None):
                continue
            decoded = _decode_calldata(tx.get("input", ""))
            if not decoded:
                continue
            parts = decoded.split(":")
            if len(parts) != 3:
                continue
            _, tx_name, tx_address = parts
            if tx_name.strip().lower() == target:
                result = tx_address.strip()  # last match wins (ascending order)
        return result
    except Exception:
        return None


def is_available(name: str) -> bool:
    return resolve(name) is None


def get_price() -> float:
    return float(os.getenv("SARA_NAME_REGISTRATION_FEE", "10"))


def submit_registration(name: str, target_address: str, payment_tx_hash: str) -> dict:
    """Ask the registrar service to verify the payment and write the
    on-chain registration. Raises on network failure; returns the service's
    JSON response (including error detail) otherwise.
    """
    service_url = os.getenv("SARA_NAME_SERVICE_URL", "").rstrip("/")
    if not service_url:
        return {"status": "error", "detail": "SARA_NAME_SERVICE_URL is not configured."}
    try:
        r = requests.post(
            f"{service_url}/register",
            json={"name": name, "target_address": target_address, "payment_tx_hash": payment_tx_hash},
            timeout=30,
        )
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.ok:
            return {"status": "registered", **body}
        return {"status": "error", "detail": body.get("detail") or f"HTTP {r.status_code}"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
