import requests

_PROXY = "https://sns-sdk-proxy.bonfida.workers.dev"


def resolve(name: str) -> str | None:
    """Resolve a Solana Name Service domain (*.sol) to a base58 public key."""
    if not name.lower().endswith(".sol"):
        return None
    try:
        r = requests.get(f"{_PROXY}/resolve/{name}", timeout=8)
        if r.ok:
            data = r.json()
            if data.get("s") == "ok":
                return data.get("result")
        return None
    except Exception:
        return None


def reverse_resolve(address: str) -> str | None:
    """Reverse-resolve a Solana address to its SNS domain."""
    try:
        r = requests.get(f"{_PROXY}/reverse-lookup/{address}", timeout=8)
        if r.ok:
            data = r.json()
            if data.get("s") == "ok":
                return data.get("result")
        return None
    except Exception:
        return None
