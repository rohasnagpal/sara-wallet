"""Treasury aggregation and stablecoin routing (CLAUDE_STAGES_3_TO_7.md
Stage 5.3/5.4). Overview reuses portfolio.py's existing balance aggregation
and Stage 1's BalanceMonitor triggers rather than rebuilding either;
routing reuses app.services.stablecoin_routing, which itself reuses the
already-hardened swap/bridge execution paths. This router only ever
*proposes* — no endpoint here signs or moves funds.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.session_auth import require_session
from app.db.models import BalanceMonitor, Wallet
from app.db.session import get_db
from app.services import stablecoin_routing

router = APIRouter(prefix="/treasury", tags=["treasury"], dependencies=[Depends(require_session)])

_CONCENTRATION_THRESHOLD_PCT = 40.0


@router.get("/overview")
def treasury_overview(db: Session = Depends(get_db)):
    from app.routers.portfolio import get_portfolio

    portfolio = get_portfolio(db)
    total_usd = portfolio.get("total_usd") or 0

    concentration_flags = []
    if total_usd:
        for asset in portfolio.get("assets", []):
            pct = round((asset["usd_value"] / total_usd) * 100, 2) if total_usd else 0
            if pct >= _CONCENTRATION_THRESHOLD_PCT:
                concentration_flags.append({
                    "wallet": asset["wallet"], "chain": asset["chain"], "symbol": asset["symbol"],
                    "usd_value": round(asset["usd_value"], 2), "pct_of_treasury": pct,
                })
        for chain, value in portfolio.get("by_chain", {}).items():
            pct = round((value / total_usd) * 100, 2) if total_usd else 0
            if pct >= _CONCENTRATION_THRESHOLD_PCT:
                concentration_flags.append({"wallet": None, "chain": chain, "symbol": None, "usd_value": value, "pct_of_treasury": pct})

    triggered_monitors = db.query(BalanceMonitor).filter(BalanceMonitor.triggered == True).all()  # noqa: E712
    wallet_names = {w.id: w.name for w in db.query(Wallet).all()}
    low_balance_flags = [{
        "wallet": wallet_names.get(m.wallet_id, "?"), "network": m.network, "token": m.token,
        "condition": m.condition, "threshold_raw": m.threshold_raw, "last_value_raw": m.last_value_raw,
    } for m in triggered_monitors]

    proposals = []
    for flag in low_balance_flags:
        proposals.append({
            "type": "top_up",
            "description": f"{flag['wallet']}'s {flag['token']} balance on {flag['network']} is "
                            f"{flag['condition']} its configured threshold — consider moving funds in.",
        })
    for flag in concentration_flags:
        target = flag["wallet"] or flag["chain"]
        proposals.append({
            "type": "diversify",
            "description": f"{target} holds {flag['pct_of_treasury']}% of total treasury value "
                            f"(${flag['usd_value']}) — consider spreading exposure across more wallets/networks.",
        })

    return {
        "total_usd": total_usd, "by_chain": portfolio.get("by_chain", {}), "assets": portfolio.get("assets", []),
        "concentration_flags": concentration_flags, "low_balance_flags": low_balance_flags,
        "proposals": proposals,
        "note": "Proposals are informational only — nothing here executes a transfer. "
                "Act on a proposal via a manual send, a Stage 3 batch payment, or a recurring schedule.",
    }


# LI.FI's quote API insists on an address but returns the same quote for any
# of them, and this endpoint only compares prices - nothing is signed or
# sent - so there's no reason to tell a third party which wallet you own.
_QUOTE_ONLY_ADDRESS = "0x000000000000000000000000000000000000dEaD"


@router.get("/routes")
def treasury_routes(
    from_network: str, to_network: str, from_token: str, to_token: str, amount: str,
    from_address: str = _QUOTE_ONLY_ADDRESS,
):
    try:
        return stablecoin_routing.compare_routes(
            from_network=from_network, to_network=to_network, from_token=from_token, to_token=to_token,
            amount=amount, from_address=from_address,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))
