from fastapi import APIRouter, Depends, HTTPException
from decimal import Decimal
from pydantic import BaseModel
from web3 import Web3

from app.core.session_auth import require_session
from app.db.models import Wallet
from app.db.session import get_db
from sqlalchemy.orm import Session
from app.tools.contracts import interaction

router = APIRouter(prefix="/contracts", tags=["contracts"], dependencies=[Depends(require_session)])


class ReadBody(BaseModel):
    address: str
    network: str
    method: str
    args: list = []


@router.post("/read")
def read(body: ReadBody):
    if not Web3.is_address(body.address):
        raise HTTPException(400, "Invalid contract address")
    try:
        return interaction.read_contract(body.address, body.network, body.method, body.args)
    except interaction.ContractAssistantError as exc:
        raise HTTPException(400, str(exc))


class PrepareBody(BaseModel):
    address: str
    network: str
    from_address: str
    method: str
    args: list = []
    value_wei: int = 0


@router.post("/prepare")
def prepare(body: PrepareBody):
    if not Web3.is_address(body.address) or not Web3.is_address(body.from_address):
        raise HTTPException(400, "Invalid EVM address")
    try:
        return interaction.prepare_call(
            body.address, body.network, body.from_address, body.method, body.args, body.value_wei,
        )
    except interaction.ContractAssistantError as exc:
        raise HTTPException(400, str(exc))


class SimulateBody(BaseModel):
    network: str
    from_address: str
    to_address: str
    data: str = "0x"
    value_wei: int = 0


@router.post("/simulate")
def simulate(body: SimulateBody):
    if not Web3.is_address(body.from_address) or not Web3.is_address(body.to_address):
        raise HTTPException(400, "Invalid EVM address")
    try:
        return interaction.simulate_and_explain(
            body.network, body.from_address, body.to_address, body.data, body.value_wei,
        )
    except interaction.ContractAssistantError as exc:
        raise HTTPException(400, str(exc))


class ExecuteCallBody(BaseModel):
    wallet_id: int
    address: str
    network: str
    method: str
    args: list = []
    value_wei: int = 0
    confirmation_token: str
    passphrase: str


@router.post("/execute")
def execute(body: ExecuteCallBody, db: Session = Depends(get_db)):
    from app.core import spending_policy
    from app.tools.risk.screening import enforce_mandatory_screening
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase
    from app.routers.chat import _record_submitted_transaction

    if not Web3.is_address(body.address):
        raise HTTPException(400, "Invalid contract address")
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    try:
        risk_targets = [body.address]
        if body.method in ("approve", "transfer") and body.args:
            risk_targets.append(str(body.args[0]))
        elif body.method == "transferFrom" and len(body.args) >= 2:
            risk_targets.append(str(body.args[1]))
        for target in risk_targets:
            if not Web3.is_address(target):
                raise ValueError(f"invalid address argument for {body.method}")
            enforce_mandatory_screening(db, target, body.network)
        policy_destination = risk_targets[-1]
        policy_token = "CONTRACT_CALL"
        policy_amount = body.value_wei
        policy_decimals = 18
        if body.method in ("transfer", "transferFrom"):
            amount_index = 1 if body.method == "transfer" else 2
            if len(body.args) <= amount_index:
                raise ValueError(f"missing amount argument for {body.method}")
            policy_amount = int(str(body.args[amount_index]), 0)
            from app.tools.market.paraswap import resolve_token, trusted_symbols
            for symbol in trusted_symbols(body.network):
                resolved = resolve_token(symbol, body.network)
                if resolved and resolved[0].lower() == body.address.lower():
                    policy_token = symbol
                    policy_decimals = resolved[1]
                    break
        decision = spending_policy.evaluate(
            db, wallet_id=wallet.id, network=body.network, token=policy_token,
            counterparty_id=None, destination_address=policy_destination,
            amount_raw=policy_amount, principal_id="local-owner",
        )
        if not decision.allowed:
            raise ValueError("; ".join(decision.denial_reasons))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    key = decrypt_key(wallet.encrypted_key)
    try:
        result = interaction.execute_reviewed_call(
            key, body.address, body.network, body.method, body.args,
            body.value_wei, body.confirmation_token,
        )
        _record_submitted_transaction(
            db, wallet_id=wallet.id, network=body.network, tx_hash=result["tx_hash"],
            from_address=wallet.address, to_address=body.address,
            amount=float(Decimal(policy_amount) / (Decimal(10) ** policy_decimals)),
            amount_raw=policy_amount, decimals=policy_decimals, token=policy_token,
            category="contract_interaction", reference=body.method,
        )
        return result
    except (interaction.ContractAssistantError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        key = None
