import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from web3 import Web3

from app.core.session_auth import require_session
from app.db.models import AlertDestination, BalanceMonitor, Wallet
from app.db.session import get_db

router = APIRouter(prefix="/safety", tags=["safety"], dependencies=[Depends(require_session)])

ERC20_APPROVAL_ABI = [{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]


def _known_spenders(network: str, w3) -> list[tuple[str, str]]:
    from app.tools.market.paraswap import CHAIN_IDS, _AUGUSTUS_ADDRESSES, _PARASWAP_SPENDER_ABI
    from app.tools.trading.lifi import _ERC20_PROXIES, _LIFI_DIAMOND, _PERMIT2_PROXY
    result = [("LI.FI Diamond", _LIFI_DIAMOND), ("LI.FI Permit2 proxy", _PERMIT2_PROXY)]
    if _ERC20_PROXIES.get(network):
        result.append(("LI.FI ERC-20 proxy", _ERC20_PROXIES[network]))
    router = _AUGUSTUS_ADDRESSES.get(CHAIN_IDS.get(network))
    if router:
        try:
            proxy = w3.eth.contract(address=Web3.to_checksum_address(router), abi=_PARASWAP_SPENDER_ABI).functions.getTokenTransferProxy().call()
            result.append(("ParaSwap token proxy", proxy))
        except Exception:
            pass
    return list(dict((address.lower(), (label, address)) for label, address in result).values())


@router.get("/allowances/{wallet_id}")
def allowances(wallet_id: int, network: str = "polygon", db: Session = Depends(get_db)):
    from app.chains.evm import get_web3
    from app.tools.market.paraswap import resolve_token
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id).first()
    if not wallet or wallet.chain != "evm":
        raise HTTPException(404, "EVM wallet not found")
    token = resolve_token("USDC", network)
    if not token:
        raise HTTPException(400, "USDC is disabled or unsupported on this network")
    token_address, decimals = token
    w3 = get_web3(network)
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_APPROVAL_ABI)
    rows = []
    for label, spender in _known_spenders(network, w3):
        raw = int(contract.functions.allowance(wallet.address, spender).call())
        if raw:
            rows.append({"network": network, "token": "USDC", "token_address": token_address,
                         "spender": spender, "spender_label": label, "allowance_raw": str(raw),
                         "decimals": decimals, "allowance": str(raw / (10 ** decimals))})
    return {"allowances": rows, "scope": "Sara-supported ParaSwap and LI.FI spenders"}


class RevokeBody(BaseModel):
    wallet_id: int
    network: str
    token: str = "USDC"
    spender: str
    passphrase: str


@router.post("/allowances/revoke")
def revoke_allowance(body: RevokeBody, db: Session = Depends(get_db)):
    from app.chains.evm import get_web3
    from app.tools.market.paraswap import resolve_token
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase
    from app.routers.chat import _record_submitted_transaction
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    resolved = resolve_token(body.token, body.network)
    if not resolved or resolved[0].lower().startswith("0xeeee"):
        raise HTTPException(400, "Only enabled ERC-20 token allowances can be revoked")
    token_address, decimals = resolved
    key = decrypt_key(wallet.encrypted_key)
    try:
        w3 = get_web3(body.network)
        account = w3.eth.account.from_key(key)
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_APPROVAL_ABI)
        gas_price = int(w3.eth.gas_price)
        tx = contract.functions.approve(Web3.to_checksum_address(body.spender), 0).build_transaction({
            "from": account.address, "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 100000, "gasPrice": gas_price, "chainId": w3.eth.chain_id,
        })
        if tx["gas"] * gas_price > 10_000_000_000_000_000:
            raise HTTPException(400, "Estimated revocation fee exceeds 0.01 native asset")
        signed = w3.eth.account.sign_transaction(tx, key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        _record_submitted_transaction(
            db, wallet_id=wallet.id, network=body.network, tx_hash=tx_hash,
            from_address=wallet.address, to_address=token_address, amount=0,
            amount_raw=0, decimals=decimals, token=body.token.upper(), category="allowance_revocation",
        )
        return {"status": "submitted", "tx_hash": tx_hash}
    finally:
        key = None


class SimulationBody(BaseModel):
    network: str
    from_address: str
    to_address: str
    data: str = Field("0x", max_length=200000)
    value_wei: int = Field(0, ge=0)


@router.post("/simulate")
def simulate(body: SimulationBody):
    from app.tools.market.tx_simulate import SimulationUnavailable, _simulate
    if not Web3.is_address(body.from_address) or not Web3.is_address(body.to_address):
        raise HTTPException(400, "Invalid EVM address")
    try:
        changes = _simulate(body.network, body.from_address, body.to_address, body.data, body.value_wei)
    except SimulationUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"safe_to_review": True, "changes": changes, "summary": [
        f"{c.get('changeType', 'CHANGE')} {c.get('rawAmount', '')} {c.get('symbol') or c.get('assetType', 'asset')} from {c.get('from', '?')} to {c.get('to', '?')}"
        for c in changes
    ]}


class MonitorBody(BaseModel):
    wallet_id: int
    network: str = "polygon"
    token: str = "USDC"
    condition: str = "below"
    threshold: str


@router.get("/monitors")
def list_monitors(db: Session = Depends(get_db)):
    return [{"id": m.id, "wallet_id": m.wallet_id, "network": m.network, "token": m.token,
             "condition": m.condition, "threshold_raw": m.threshold_raw, "decimals": m.decimals,
             "last_value_raw": m.last_value_raw, "triggered": m.triggered, "enabled": m.enabled}
            for m in db.query(BalanceMonitor).order_by(BalanceMonitor.id).all()]


@router.post("/monitors")
def create_monitor(body: MonitorBody, db: Session = Depends(get_db)):
    from app.core.amounts import to_base_units
    from app.core.assets import NETWORKS
    from app.tools.market.paraswap import resolve_token
    if body.condition not in ("below", "above"):
        raise HTTPException(400, "condition must be below or above")
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    native = NETWORKS.get(body.network, {}).get("native")
    if body.token.upper() == native:
        address, decimals = None, 18
    else:
        resolved = resolve_token(body.token, body.network)
        if not resolved:
            raise HTTPException(400, "Token is disabled or unsupported")
        address, decimals = resolved
    monitor = BalanceMonitor(wallet_id=wallet.id, network=body.network, token=body.token.upper(),
                             token_address=address, decimals=decimals, condition=body.condition,
                             threshold_raw=str(to_base_units(body.threshold, decimals, body.token)))
    db.add(monitor); db.commit(); db.refresh(monitor)
    return {"id": monitor.id, "status": "created"}


@router.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)):
    row = db.query(BalanceMonitor).filter(BalanceMonitor.id == monitor_id).first()
    if not row: raise HTTPException(404, "Monitor not found")
    db.delete(row); db.commit(); return {"deleted": monitor_id}


class DestinationBody(BaseModel):
    kind: str
    target: str
    config: dict = {}


@router.get("/alerts")
def list_destinations(db: Session = Depends(get_db)):
    return [{"id": d.id, "kind": d.kind, "target": d.target, "enabled": d.enabled}
            for d in db.query(AlertDestination).order_by(AlertDestination.id).all()]


@router.post("/alerts")
def create_destination(body: DestinationBody, db: Session = Depends(get_db)):
    from app.services.alerts import validate_webhook_url
    if body.kind not in ("telegram", "email", "webhook"):
        raise HTTPException(400, "kind must be telegram, email or webhook")
    if body.kind == "webhook":
        try: validate_webhook_url(body.target)
        except ValueError as exc: raise HTTPException(400, str(exc))
        if not body.config.get("signing_secret"):
            raise HTTPException(400, "Webhook requires signing_secret")
    if body.kind == "email" and ("@" not in body.target or not body.config.get("smtp_host") or not body.config.get("from_address")):
        raise HTTPException(400, "Email requires recipient, smtp_host and from_address")
    if body.kind == "telegram" and not body.config.get("bot_token"):
        raise HTTPException(400, "Telegram requires bot_token")
    row = AlertDestination(kind=body.kind, target=body.target, secret=json.dumps(body.config))
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "status": "created"}


@router.delete("/alerts/{destination_id}")
def delete_destination(destination_id: int, db: Session = Depends(get_db)):
    row = db.query(AlertDestination).filter(AlertDestination.id == destination_id).first()
    if not row: raise HTTPException(404, "Alert destination not found")
    db.delete(row); db.commit(); return {"deleted": destination_id}
