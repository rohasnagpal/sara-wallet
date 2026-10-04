"""Treasury aggregation and stablecoin routing (CLAUDE_STAGES_3_TO_7.md
Stage 5.3/5.4). Overview reuses portfolio.py's existing balance aggregation
and Stage 1's BalanceMonitor triggers rather than rebuilding either;
routing reuses app.services.stablecoin_routing, which itself reuses the
already-hardened swap/bridge execution paths. This router does not sign or
move funds.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.session_auth import require_session
from app.db.models import BalanceMonitor, Wallet
from app.db.session import get_db
from app.services import stablecoin_routing

router = APIRouter(prefix="/treasury", tags=["treasury"], dependencies=[Depends(require_session)])

@router.get("/overview")
def treasury_overview(db: Session = Depends(get_db)):
    from app.routers.portfolio import get_portfolio

    portfolio = get_portfolio(db)
    total_usd = portfolio.get("total_usd") or 0

    triggered_monitors = db.query(BalanceMonitor).filter(BalanceMonitor.triggered == True).all()  # noqa: E712
    wallet_names = {w.id: w.name for w in db.query(Wallet).all()}
    low_balance_flags = [{
        "wallet": wallet_names.get(m.wallet_id, "?"), "network": m.network, "token": m.token,
        "condition": m.condition, "threshold_raw": m.threshold_raw, "last_value_raw": m.last_value_raw,
    } for m in triggered_monitors]

    return {
        "total_usd": total_usd, "by_chain": portfolio.get("by_chain", {}), "assets": portfolio.get("assets", []),
        "low_balance_flags": low_balance_flags,
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
