from datetime import datetime
from app.core.events import publish
from app.db.models import BalanceMonitor, Wallet


def check_balance_monitors(db) -> int:
    from app.chains.evm import get_balance, get_erc20_balance
    checked = 0
    for monitor in db.query(BalanceMonitor).filter(BalanceMonitor.enabled.is_(True)).all():
        wallet = db.query(Wallet).filter(Wallet.id == monitor.wallet_id).first()
        if not wallet or wallet.chain != "evm":
            continue
        try:
            if monitor.token_address:
                value = get_erc20_balance(monitor.token_address, monitor.decimals, wallet.address, monitor.network)
            else:
                value = get_balance(wallet.address, monitor.network)["balance"]
            raw = int(round(value * (10 ** monitor.decimals)))
            threshold = int(monitor.threshold_raw)
            triggered = raw < threshold if monitor.condition == "below" else raw > threshold
            if triggered and not monitor.triggered:
                publish(
                    db, "balance.threshold_reached",
                    {"monitor_id": monitor.id, "wallet": wallet.name, "network": monitor.network,
                     "token": monitor.token, "balance_raw": str(raw), "threshold_raw": monitor.threshold_raw,
                     "decimals": monitor.decimals, "condition": monitor.condition},
                    aggregate_type="balance_monitor", aggregate_id=str(monitor.id),
                )
            monitor.triggered = triggered
            monitor.last_value_raw = str(raw)
            monitor.checked_at = datetime.utcnow()
            db.commit()
            checked += 1
        except Exception:
            db.rollback()
    return checked
