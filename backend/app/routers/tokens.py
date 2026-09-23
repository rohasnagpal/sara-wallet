from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from web3 import Web3

from app.chains.evm import _NATIVE_TOKEN
from app.tools.market.paraswap import CHAIN_IDS, NATIVE_SYMBOLS, _TOKENS, _NATIVE
from app.core.amounts import to_base_units
from app.core.assets import EURC_ADDRESSES, EURC_DECIMALS, enabled_networks, token_enabled
from app.core.audit import append_audit
from app.core.session_auth import require_session
from app.db.models import TokenDeployment, Wallet
from app.db.session import get_db
from app.services import token_factory

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("/trusted")
def trusted_tokens():
    """Every contract Sara will ever resolve a symbol to. Nothing outside
    this list is reachable from a send/swap/bridge command."""
    chains = []
    for network in enabled_networks():
        # .get(), not [] — Arc has no Paraswap chain-id entry at all (it
        # isn't swap-integrated by design), and indexing with [] here used
        # to raise a bare KeyError and 500 this entire endpoint for every
        # network the instant Arc was enabled, which it is by default.
        chain_id = CHAIN_IDS.get(network)
        native_symbol = NATIVE_SYMBOLS.get(network, _NATIVE_TOKEN.get(network, "ETH"))
        tokens = [{"symbol": native_symbol, "address": _NATIVE, "decimals": 18, "native": True}]
        for symbol, (address, decimals) in (_TOKENS.get(chain_id, {}) if chain_id else {}).items():
            if token_enabled(symbol, network):
                tokens.append({"symbol": symbol, "address": address, "decimals": decimals, "native": False})
        if network in EURC_ADDRESSES and token_enabled("EURC", network):
            tokens.append({"symbol": "EURC", "address": EURC_ADDRESSES[network], "decimals": EURC_DECIMALS, "native": False})
        chains.append({"chain": network, "tokens": tokens})

    return {"chains": chains}


# ══════════════════════════════════════
# Token creation & management (Stage 5.1/5.2) — compiled from pinned
# templates under contracts/, never arbitrary Solidity.
# ══════════════════════════════════════

@router.get("/templates")
def token_templates():
    return {"templates": token_factory.list_templates()}


def _deployment_row(row: TokenDeployment) -> dict:
    return {
        "id": row.id, "template_id": row.template_id, "wallet_id": row.wallet_id, "network": row.network,
        "contract_address": row.contract_address, "owner_address": row.owner_address,
        "name": row.name, "symbol": row.symbol, "decimals": row.decimals,
        "initial_supply_raw": row.initial_supply_raw, "cap_raw": row.cap_raw,
        "compiler_version": row.compiler_version, "source_sha256": row.source_sha256,
        "deployment_tx_hash": row.deployment_tx_hash, "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
    }


class DeploymentBody(BaseModel):
    wallet_id: int
    network: str
    template_id: str
    name: str = Field(..., min_length=1, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=16)
    decimals: int = Field(18, ge=0, le=36)
    initial_supply: str
    cap: str | None = None
    owner_address: str | None = None  # defaults to the deploying wallet


def _resolve_owner(wallet: Wallet, owner_address: str | None) -> str:
    if owner_address:
        if not Web3.is_address(owner_address):
            raise HTTPException(400, "Invalid owner_address")
        return owner_address
    return wallet.address


@router.post("/deployments/prepare", dependencies=[Depends(require_session)])
def prepare_deployment(body: DeploymentBody, db: Session = Depends(get_db)):
    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    owner_address = _resolve_owner(wallet, body.owner_address)
    try:
        initial_supply_raw = to_base_units(body.initial_supply, body.decimals, body.symbol)
        cap_raw = to_base_units(body.cap, body.decimals, body.symbol) if body.cap else None
        return token_factory.preview_deployment(
            db, wallet_id=wallet.id, network=body.network, template_id=body.template_id,
            name=body.name, symbol=body.symbol, decimals=body.decimals,
            initial_supply_raw=initial_supply_raw, cap_raw=cap_raw, owner_address=owner_address,
        )
    except (ValueError, token_factory.TokenFactoryError) as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, f"Could not reach the {body.network.capitalize()} network - try again in a moment.")


class DeployBody(DeploymentBody):
    passphrase: str


@router.post("/deployments", dependencies=[Depends(require_session)])
def create_deployment(body: DeployBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    wallet = db.query(Wallet).filter(Wallet.id == body.wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise HTTPException(404, "EVM wallet not found")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    owner_address = _resolve_owner(wallet, body.owner_address)
    try:
        initial_supply_raw = to_base_units(body.initial_supply, body.decimals, body.symbol)
        cap_raw = to_base_units(body.cap, body.decimals, body.symbol) if body.cap else None
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    key = decrypt_key(wallet.encrypted_key)
    try:
        row = token_factory.deploy_token(
            db, wallet_id=wallet.id, private_key=key, network=body.network, template_id=body.template_id,
            name=body.name, symbol=body.symbol, decimals=body.decimals,
            initial_supply_raw=initial_supply_raw, cap_raw=cap_raw, owner_address=owner_address,
        )
    except token_factory.TokenFactoryError as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, f"Could not reach the {body.network.capitalize()} network - try again in a moment.")
    finally:
        key = None
    return _deployment_row(row)


@router.get("/deployments", dependencies=[Depends(require_session)])
def list_deployments(wallet_id: int | None = None, db: Session = Depends(get_db)):
    query = db.query(TokenDeployment)
    if wallet_id is not None:
        query = query.filter(TokenDeployment.wallet_id == wallet_id)
    return {"deployments": [_deployment_row(r) for r in query.order_by(TokenDeployment.id.desc()).all()]}


@router.get("/deployments/{deployment_id}/supply", dependencies=[Depends(require_session)])
def deployment_supply(deployment_id: int, db: Session = Depends(get_db)):
    try:
        row = token_factory._deployment_or_raise(db, deployment_id)
        return token_factory.token_supply(row)
    except token_factory.TokenFactoryError as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, "Could not reach the network - try again in a moment.")


class MintBody(BaseModel):
    deployment_id: int
    to_address: str
    amount: str
    passphrase: str


@router.post("/mint", dependencies=[Depends(require_session)])
def mint(body: MintBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    if not Web3.is_address(body.to_address):
        raise HTTPException(400, "Invalid to_address")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    try:
        row = token_factory._deployment_or_raise(db, body.deployment_id)
        wallet = db.query(Wallet).filter(Wallet.id == row.wallet_id).first()
        amount_raw = to_base_units(body.amount, row.decimals, row.symbol)
        key = decrypt_key(wallet.encrypted_key)
        try:
            tx_hash = token_factory.mint_token(
                db, deployment=row, wallet=wallet, private_key=key, to_address=body.to_address, amount_raw=amount_raw,
            )
        finally:
            key = None
        append_audit(db, "token.minted", "token_deployment", resource_id=str(row.id),
                     details={"to": body.to_address, "amount_raw": str(amount_raw), "tx_hash": tx_hash})
        db.commit()
        return {"tx_hash": tx_hash}
    except (ValueError, token_factory.TokenFactoryError) as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, "Could not reach the network - try again in a moment.")


class BurnBody(BaseModel):
    deployment_id: int
    amount: str
    from_address: str | None = None
    passphrase: str


@router.post("/burn", dependencies=[Depends(require_session)])
def burn(body: BurnBody, db: Session = Depends(get_db)):
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase

    if body.from_address and not Web3.is_address(body.from_address):
        raise HTTPException(400, "Invalid from_address")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    try:
        row = token_factory._deployment_or_raise(db, body.deployment_id)
        wallet = db.query(Wallet).filter(Wallet.id == row.wallet_id).first()
        amount_raw = to_base_units(body.amount, row.decimals, row.symbol)
        key = decrypt_key(wallet.encrypted_key)
        try:
            tx_hash = token_factory.burn_token(
                db, deployment=row, wallet=wallet, private_key=key, amount_raw=amount_raw, from_address=body.from_address,
            )
        finally:
            key = None
        append_audit(db, "token.burned", "token_deployment", resource_id=str(row.id),
                     details={"from": body.from_address or wallet.address, "amount_raw": str(amount_raw), "tx_hash": tx_hash})
        db.commit()
        return {"tx_hash": tx_hash}
    except (ValueError, token_factory.TokenFactoryError) as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, "Could not reach the network - try again in a moment.")


class TransferBody(BaseModel):
    deployment_id: int
    to_address: str
    amount: str
    passphrase: str


@router.post("/transfer", dependencies=[Depends(require_session)])
def transfer(body: TransferBody, db: Session = Depends(get_db)):
    from app.chains.evm import broadcast_raw_transaction, prepare_erc20_transfer_raw
    from app.core import spending_policy
    from app.tools.risk.screening import enforce_mandatory_screening
    from app.tools.wallet.encrypt import decrypt_key
    from app.tools.wallet.lock import confirm_passphrase
    from app.routers.chat import _record_submitted_transaction

    if not Web3.is_address(body.to_address):
        raise HTTPException(400, "Invalid to_address")
    if not confirm_passphrase(body.passphrase):
        raise HTTPException(401, "Incorrect passphrase")
    try:
        row = token_factory._deployment_or_raise(db, body.deployment_id)
    except token_factory.TokenFactoryError as exc:
        raise HTTPException(400, str(exc))
    wallet = db.query(Wallet).filter(Wallet.id == row.wallet_id).first()
    try:
        amount_raw = to_base_units(body.amount, row.decimals, row.symbol)
        decision = spending_policy.evaluate(
            db, wallet_id=wallet.id, network=row.network, token=row.symbol,
            counterparty_id=None, destination_address=body.to_address,
            amount_raw=amount_raw, principal_id="local-owner",
        )
        if not decision.allowed:
            raise ValueError("; ".join(decision.denial_reasons))
        enforce_mandatory_screening(db, body.to_address, row.network)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, "Could not reach the network - try again in a moment.")
    key = decrypt_key(wallet.encrypted_key)
    try:
        prepared = prepare_erc20_transfer_raw(
            key, row.contract_address, row.decimals, body.to_address, amount_raw, row.network,
        )
        tx_hash = broadcast_raw_transaction(row.network, prepared["raw_transaction"])
        amount = float(Decimal(amount_raw) / (Decimal(10) ** row.decimals))
        _record_submitted_transaction(
            db, wallet_id=wallet.id, network=row.network, tx_hash=tx_hash,
            from_address=wallet.address, to_address=body.to_address, amount=amount,
            amount_raw=amount_raw, decimals=row.decimals,
            token=row.symbol, category="token_transfer",
        )
        return {"tx_hash": tx_hash}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (ConnectionError, OSError):
        raise HTTPException(502, "Could not reach the network - try again in a moment.")
    finally:
        key = None
