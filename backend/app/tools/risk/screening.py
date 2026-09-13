"""Provider-neutral address risk screening adapter (CLAUDE_STAGES_3_TO_7.md
Stage 5.6).

No sanctions/scam-list provider ships configured by default — Sara never
bundles a specific vendor's API key. Until RISK_SCREENING_PROVIDER and
RISK_SCREENING_API_KEY are both set, every screen() call returns
"unavailable" with an honest reason, never a fabricated "clear" result.
Whether "unavailable" blocks a send is controlled by
RISK_SCREENING_MANDATORY — fail-closed only once an operator has actually
opted into requiring screening; the default is to record the attempt and
let the caller proceed with a visible warning.
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
        outcome = ScreeningResult(
            address=address, network=network, provider="unconfigured", result="unavailable",
            reason="no risk-screening provider is configured (provider, API URL and API key are required)",
            checked_at=now,
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
