"""Address risk screening.

Out of the box Sara checks the free, public Chainalysis sanctions oracle: an
on-chain contract, deployed on several networks, whose `isSanctioned(address)`
answers whether an address is on a sanctions list. It needs no account or API
key, and because a sanctioned address is sanctioned everywhere, another
network's copy of the oracle answers for a network that has none (Base).
This is a *sanctions* check only, not a scam/hack/mixer risk score.

An operator who wants broader coverage can configure a provider-neutral
JSON adapter instead (RISK_SCREENING_PROVIDER, RISK_SCREENING_API_URL,
RISK_SCREENING_API_KEY), which takes priority. When neither can be reached
the result is "unavailable" with an honest reason, never a fabricated
"clear". Whether "unavailable" blocks a send is controlled by
RISK_SCREENING_MANDATORY; the default is to record the attempt and let the
caller proceed with a visible warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import requests

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import RiskScreening


@dataclass
class ScreeningResult:
    address: str
    network: str
    provider: str
    result: str  # clear | flagged | unavailable
    evidence: list[dict] = field(default_factory=list)
    reason: str | None = None
    checked_at: datetime | None = None
    expires_at: datetime | None = None


def _provider_configured() -> bool:
    return bool(settings.RISK_SCREENING_PROVIDER and settings.RISK_SCREENING_API_KEY)


BUILTIN_PROVIDER = "Chainalysis sanctions oracle"
_SANCTIONS_ORACLE = "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
_SANCTIONS_ORACLE_ABI = [{
    "inputs": [{"internalType": "address", "name": "addr", "type": "address"}], "name": "isSanctioned",
    "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function",
}]
# The same list is published on each chain; ask the requested network first,
# then fall back to others that do have the contract.
_ORACLE_FALLBACK_NETWORKS = ("ethereum", "polygon")


def _check_sanctions_oracle(address: str, network: str) -> str:
    """"flagged" or "clear". Raises ConnectionError if no network could answer."""
    from web3 import Web3
    from app.chains.evm import get_web3

    checksum = Web3.to_checksum_address(address)
    for net in dict.fromkeys([network, *_ORACLE_FALLBACK_NETWORKS]):
        try:
            oracle = get_web3(net).eth.contract(
                address=Web3.to_checksum_address(_SANCTIONS_ORACLE), abi=_SANCTIONS_ORACLE_ABI)
            return "flagged" if oracle.functions.isSanctioned(checksum).call() else "clear"
        except Exception:
            continue  # no contract on this network, or it couldn't be reached
    raise ConnectionError("couldn't reach a network to check the sanctions list - try again in a moment")


def _call_provider(address: str, network: str) -> tuple[str, list[dict]]:
    """Call the documented provider-neutral JSON adapter.

    The configured endpoint receives ``address`` and ``network`` and must
    return ``{"result":"clear|flagged","evidence":[...]}``. Evidence is
    reduced to identifiers so unverified allegation text is never stored.
    """
    from app.services.alerts import validate_webhook_url
    validate_webhook_url(settings.RISK_SCREENING_API_URL)
    response = requests.post(
        settings.RISK_SCREENING_API_URL,
        json={"address": address, "network": network},
        headers={"Authorization": f"Bearer {settings.RISK_SCREENING_API_KEY}"},
        timeout=15, allow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result")
    if result not in ("clear", "flagged"):
        raise ValueError("risk provider returned an invalid result")
    raw_evidence = payload.get("evidence") or []
    if not isinstance(raw_evidence, list):
        raise ValueError("risk provider returned invalid evidence")
    evidence: list[dict] = []
    for item in raw_evidence[:50]:
        if isinstance(item, str):
            evidence.append({"id": item[:200]})
        elif isinstance(item, dict) and item.get("id") is not None:
            evidence.append({"id": str(item["id"])[:200]})
    return result, evidence


def screen_address(db: Session, address: str, network: str, *, use_cache: bool = True) -> ScreeningResult:
    address = address.lower()
    network = network.lower()

    if use_cache:
        cached = (
            db.query(RiskScreening)
            .filter(RiskScreening.address == address, RiskScreening.network == network)
            .order_by(RiskScreening.checked_at.desc())
            .first()
        )
        if cached and cached.expires_at and cached.expires_at > datetime.utcnow():
            return ScreeningResult(
                address=address, network=network, provider=cached.provider, result=cached.result,
                evidence=json.loads(cached.evidence or "[]"), reason=cached.reason,
                checked_at=cached.checked_at, expires_at=cached.expires_at,
            )

    now = datetime.utcnow()
    if not _provider_configured():
        provider = BUILTIN_PROVIDER
        try:
            result = _check_sanctions_oracle(address, network)
            outcome = ScreeningResult(
                address=address, network=network, provider=provider, result=result,
                evidence=[{"id": "listed as sanctioned"}] if result == "flagged" else [],
                checked_at=now, expires_at=now + timedelta(hours=settings.RISK_SCREENING_TTL_HOURS),
            )
        except Exception as exc:
            outcome = ScreeningResult(
                address=address, network=network, provider=provider, result="unavailable",
                reason=str(exc), checked_at=now,
            )
    else:
        try:
            result, evidence = _call_provider(address, network)
            outcome = ScreeningResult(
                address=address, network=network, provider=settings.RISK_SCREENING_PROVIDER, result=result,
                evidence=evidence, checked_at=now,
                expires_at=now + timedelta(hours=settings.RISK_SCREENING_TTL_HOURS),
            )
        except Exception as exc:
            outcome = ScreeningResult(
                address=address, network=network, provider=settings.RISK_SCREENING_PROVIDER, result="unavailable",
                reason=str(exc), checked_at=now,
            )

    row = RiskScreening(
        address=outcome.address, network=outcome.network, provider=outcome.provider, result=outcome.result,
        evidence=json.dumps(outcome.evidence), reason=outcome.reason,
        checked_at=outcome.checked_at, expires_at=outcome.expires_at,
    )
    db.add(row)
    db.commit()
    return outcome


def enforce_mandatory_screening(db: Session, address: str, network: str) -> None:
    """Raises ValueError when screening is mandatory and the address is
    flagged, or screening couldn't be performed at all — fail-closed, per
    Stage 5.6. A no-op when RISK_SCREENING_MANDATORY is off (the default),
    so configuring nothing never blocks an existing send flow."""
    if not settings.RISK_SCREENING_MANDATORY:
        return
    outcome = screen_address(db, address, network)
    if outcome.result == "flagged":
        raise ValueError(f"address {address} is flagged by risk screening ({outcome.provider}) — blocked")
    if outcome.result == "unavailable":
        raise ValueError(
            f"mandatory risk screening is unavailable for {address} ({outcome.reason}) — refusing to proceed"
        )
