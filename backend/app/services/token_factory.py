"""ERC-20 deployment and management for Sara's token creator
(CLAUDE_STAGES_3_TO_7.md Stage 5.1/5.2).

Templates are compiled from pinned Solidity source under contracts/ — never
arbitrary user-supplied Solidity — with ABI/bytecode copied into
app/tools/tokens/templates/*.json by contracts/scripts/export_artifacts.py
(a reproducible script, never hand-edited). See contracts/README.md.

Every mint/burn call re-checks on-chain state (owner(), balance, allowance)
before signing — Sara never trusts a locally cached role or balance for an
authority decision (Stage 5.2: "Detect current on-chain role/owner rather
than trusting local records").
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from functools import lru_cache
import json
import shlex
import subprocess
from pathlib import Path

from web3 import Web3

from app.core.audit import append_audit
from app.core.events import publish
from app.core.resource_paths import backend_path
from app.db.models import TokenDeployment, Wallet

# __file__-relative would break under a frozen (PyInstaller) build — see
# app/core/resource_paths.py.
_TEMPLATES_DIR = backend_path("app", "tools", "tokens", "templates")

# (source file under contracts/src, contract name) per template - used only
# for source verification, kept in sync with contracts/scripts/export_artifacts.py's TEMPLATES.
_TEMPLATE_SOURCES = {
    "fixed_supply": ("FixedSupplyToken.sol", "FixedSupplyToken"),
    "mintable_burnable_capped": ("MintableBurnableCappedToken.sol", "MintableBurnableCappedToken"),
}
# Not bundled in a frozen build (source-verification is best-effort and
# already no-ops when this path doesn't exist, see its usage below).
_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

_TEMPLATE_DESCRIPTIONS = {
    "fixed_supply": {
        "label": "Fixed supply",
        "description": "The entire supply mints once, to a chosen owner, at deployment. No mint, burn-by-owner, "
                        "pause or upgrade capability exists afterwards.",
        "centralisation_risks": [],
    },
    "mintable_burnable_capped": {
        "label": "Owner-mintable, capped, burnable",
        "description": "The owner may mint new supply up to a fixed, immutable cap. Any holder may burn their own "
                        "balance (the owner cannot force-burn someone else's).",
        "centralisation_risks": [
            "The owner address can mint new tokens at any time, up to the cap — this dilutes every other holder.",
            "If the owner's wallet is compromised, an attacker can mint up to the remaining cap.",
        ],
    },
}


class TokenFactoryError(Exception):
    pass


@lru_cache(maxsize=8)
def _load_template_cached(template_id: str) -> dict:
    path = _TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise TokenFactoryError(f"unknown token template: {template_id}")
    return json.loads(path.read_text())


def load_template(template_id: str) -> dict:
    return _load_template_cached(template_id)


def list_templates() -> list[dict]:
    return [
        {"template_id": tid, **_TEMPLATE_DESCRIPTIONS[tid], "compiler_version": load_template(tid)["compiler_version"]}
        for tid in _TEMPLATE_DESCRIPTIONS
    ]


def _constructor_args(template_id: str, *, name: str, symbol: str, decimals: int,
                       initial_supply_raw: int, cap_raw: int | None, owner_address: str) -> tuple:
    owner_checksum = Web3.to_checksum_address(owner_address)
    if template_id == "fixed_supply":
        return (name, symbol, decimals, initial_supply_raw, owner_checksum)
    if template_id == "mintable_burnable_capped":
        if not cap_raw:
            raise TokenFactoryError("cap is required for the mintable_burnable_capped template")
        return (name, symbol, decimals, initial_supply_raw, cap_raw, owner_checksum)
    raise TokenFactoryError(f"unknown token template: {template_id}")


def _wallet_or_raise(db, wallet_id: int) -> Wallet:
    wallet = db.query(Wallet).filter(Wallet.id == wallet_id, Wallet.chain == "evm").first()
    if not wallet:
        raise TokenFactoryError("wallet not found")
    return wallet


def preview_deployment(db, *, wallet_id: int, network: str, template_id: str, name: str, symbol: str,
                        decimals: int, initial_supply_raw: int, cap_raw: int | None, owner_address: str) -> dict:
    from app.chains.evm import get_web3

    template = load_template(template_id)
    wallet = _wallet_or_raise(db, wallet_id)
    w3 = get_web3(network)
    args = _constructor_args(template_id, name=name, symbol=symbol, decimals=decimals,
                              initial_supply_raw=initial_supply_raw, cap_raw=cap_raw, owner_address=owner_address)
    contract = w3.eth.contract(abi=template["abi"], bytecode=template["bytecode"])
    checksum_address = Web3.to_checksum_address(wallet.address)
    gas_price = w3.eth.gas_price
    # gasPrice must be supplied up front (legacy-style tx) — otherwise
    # web3.py's automatic EIP-1559 fee default-fill fetches the latest block
    # to read baseFeePerGas, which fails against Polygon's POA-formatted
    # extraData (same reason app/chains/evm.py never uses build_transaction's
    # auto-fill path either).
    unsigned = contract.constructor(*args).build_transaction({
        "from": checksum_address, "nonce": w3.eth.get_transaction_count(checksum_address),
        "gasPrice": gas_price, "chainId": w3.eth.chain_id,
    })
    gas_estimate = w3.eth.estimate_gas(unsigned)
    gas_limit = int(gas_estimate * 1.2)
    fee_wei = gas_limit * gas_price
    return {
        "template_id": template_id, "network": network,
        "name": name, "symbol": symbol, "decimals": decimals,
        "initial_supply_raw": str(initial_supply_raw), "cap_raw": str(cap_raw) if cap_raw else None,
        "owner_address": Web3.to_checksum_address(owner_address),
        "estimated_gas": gas_limit, "gas_price_wei": gas_price, "estimated_fee_native": str(w3.from_wei(fee_wei, "ether")),
        "compiler_version": template["compiler_version"], "source_sha256": template["source_sha256"],
        **_TEMPLATE_DESCRIPTIONS[template_id],
    }


def deploy_token(db, *, wallet_id: int, private_key: str, network: str, template_id: str, name: str, symbol: str,
                  decimals: int, initial_supply_raw: int, cap_raw: int | None, owner_address: str) -> TokenDeployment:
    from app.chains.evm import get_web3, _CHAIN_IDS

    template = load_template(template_id)
    wallet = _wallet_or_raise(db, wallet_id)
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    if account.address.lower() != wallet.address.lower():
        raise TokenFactoryError("wallet key does not match the selected wallet")

    args = _constructor_args(template_id, name=name, symbol=symbol, decimals=decimals,
                              initial_supply_raw=initial_supply_raw, cap_raw=cap_raw, owner_address=owner_address)
    contract = w3.eth.contract(abi=template["abi"], bytecode=template["bytecode"])
    tx = contract.constructor(*args).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": w3.eth.gas_price,
        "chainId": _CHAIN_IDS.get(network.lower(), w3.eth.chain_id),
    })
    gas_estimate = w3.eth.estimate_gas(tx)
    tx["gas"] = int(gas_estimate * 1.2)
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()

    row = TokenDeployment(
        template_id=template_id, wallet_id=wallet.id, network=network.lower(),
        owner_address=Web3.to_checksum_address(owner_address), name=name, symbol=symbol, decimals=decimals,
        initial_supply_raw=str(initial_supply_raw), cap_raw=str(cap_raw) if cap_raw else None,
        compiler_version=template["compiler_version"], source_sha256=template["source_sha256"],
        deployment_tx_hash=tx_hash, status="submitted",
    )
    db.add(row)
    db.flush()
    publish(db, "token.deployment_submitted", {"tx_hash": tx_hash, "network": network, "template_id": template_id},
            aggregate_type="token_deployment", aggregate_id=str(row.id), event_key=f"token_deploy:{network}:{tx_hash}")
    append_audit(db, "token.deployment_submitted", "token_deployment", resource_id=str(row.id),
                 details={"tx_hash": tx_hash, "network": network, "template_id": template_id, "name": name, "symbol": symbol})
    db.commit()

    # Best-effort short wait for the address — falls back to the background
    # monitor (check_pending_deployments) if it isn't mined in time.
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        _apply_receipt(db, row, receipt)
    except Exception:
        pass
    return row


def _apply_receipt(db, row: TokenDeployment, receipt) -> None:
    if receipt.status == 1 and receipt.contractAddress:
        row.contract_address = Web3.to_checksum_address(receipt.contractAddress)
        row.status = "confirmed"
        row.confirmed_at = datetime.utcnow()
        publish(db, "token.deployment_confirmed",
                {"contract_address": row.contract_address, "network": row.network},
                aggregate_type="token_deployment", aggregate_id=str(row.id),
                event_key=f"token_deploy_confirmed:{row.network}:{row.deployment_tx_hash}")
        append_audit(db, "token.deployment_confirmed", "token_deployment", resource_id=str(row.id),
                     details={"contract_address": row.contract_address})
        _submit_source_verification(row)
    else:
        row.status = "failed"
    db.commit()


def _forge_binary() -> str | None:
    import shutil
    found = shutil.which("forge")
    if found:
        return found
    candidate = Path.home() / ".foundry" / "bin" / "forge"
    return str(candidate) if candidate.exists() else None


def _encode_constructor_args(template_id: str, *, name: str, symbol: str, decimals: int,
                              initial_supply_raw: int, cap_raw: int | None, owner_address: str) -> str | None:
    from eth_abi import encode

    owner_checksum = Web3.to_checksum_address(owner_address)
    if template_id == "fixed_supply":
        encoded = encode(["string", "string", "uint8", "uint256", "address"],
                          [name, symbol, decimals, initial_supply_raw, owner_checksum])
    elif template_id == "mintable_burnable_capped":
        if cap_raw is None:
            return None
        encoded = encode(["string", "string", "uint8", "uint256", "uint256", "address"],
                          [name, symbol, decimals, initial_supply_raw, cap_raw, owner_checksum])
    else:
        return None
    return encoded.hex()


def _submit_source_verification(row: TokenDeployment) -> None:
    """Best-effort, fire-and-forget PolygonScan/Etherscan source
    verification, submitted as a detached OS process so it survives this
    backend process reloading/restarting. Silently does nothing if
    POLYGONSCAN_API_KEY isn't set or `forge` isn't installed - verification
    is a nice-to-have, it must never block or fail a deployment."""
    try:
        from app.core.config import settings
        if not settings.POLYGONSCAN_API_KEY:
            return
        forge = _forge_binary()
        if not forge:
            return
        source_file, contract_name = _TEMPLATE_SOURCES.get(row.template_id, (None, None))
        if not source_file:
            return
        from app.chains.evm import _CHAIN_IDS
        chain_id = _CHAIN_IDS.get(row.network)
        if not chain_id or not _CONTRACTS_DIR.exists():
            return
        constructor_args = _encode_constructor_args(
            row.template_id, name=row.name, symbol=row.symbol, decimals=row.decimals,
            initial_supply_raw=int(row.initial_supply_raw), cap_raw=int(row.cap_raw) if row.cap_raw else None,
            owner_address=row.owner_address,
        )
        if constructor_args is None:
            return
        log_path = _CONTRACTS_DIR / "verification.log"
        forge_cmd = " ".join(shlex.quote(part) for part in [
            forge, "verify-contract", row.contract_address, f"src/{source_file}:{contract_name}",
            "--chain", str(chain_id), "--etherscan-api-key", settings.POLYGONSCAN_API_KEY,
            "--constructor-args", "0x" + constructor_args, "--watch",
        ])
        # A short delay before submitting - PolygonScan's own indexer needs
        # a moment to pick up the just-mined contract, or verification fails
        # with "contract not found" even though it's already on-chain.
        subprocess.Popen(
            ["sh", "-c", f"sleep 20 && {forge_cmd} >> {shlex.quote(str(log_path))} 2>&1"],
            cwd=_CONTRACTS_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception:
        pass


def check_pending_deployments(db) -> int:
    from app.chains.evm import get_web3

    checked = 0
    for row in db.query(TokenDeployment).filter(TokenDeployment.status == "submitted").all():
        try:
            w3 = get_web3(row.network)
            receipt = w3.eth.get_transaction_receipt(row.deployment_tx_hash)
        except Exception:
            continue
        if receipt is None:
            continue
        _apply_receipt(db, row, receipt)
        checked += 1
    return checked


def _deployment_or_raise(db, deployment_id: int) -> TokenDeployment:
    row = db.query(TokenDeployment).filter(TokenDeployment.id == deployment_id).first()
    if not row:
        raise TokenFactoryError("token deployment not found")
    if row.status != "confirmed" or not row.contract_address:
        raise TokenFactoryError("token deployment has not confirmed on-chain yet")
    return row


def _live_contract(deployment: TokenDeployment):
    from app.chains.evm import get_web3

    template = load_template(deployment.template_id)
    w3 = get_web3(deployment.network)
    contract = w3.eth.contract(address=Web3.to_checksum_address(deployment.contract_address), abi=template["abi"])
    return w3, contract


def token_supply(deployment: TokenDeployment) -> dict:
    w3, contract = _live_contract(deployment)
    total_supply = contract.functions.totalSupply().call()
    result = {
        "contract_address": deployment.contract_address,
        "total_supply": format(Decimal(total_supply) / (Decimal(10) ** deployment.decimals), "f"),
        "total_supply_raw": str(total_supply),
    }
    if deployment.cap_raw:
        result["cap_raw"] = deployment.cap_raw
        result["cap"] = format(Decimal(deployment.cap_raw) / (Decimal(10) ** deployment.decimals), "f")
    if deployment.template_id == "mintable_burnable_capped":
        result["on_chain_owner"] = contract.functions.owner().call()
    return result


def mint_token(db, *, deployment: TokenDeployment, wallet: Wallet, private_key: str, to_address: str, amount_raw: int) -> str:
    if deployment.template_id != "mintable_burnable_capped":
        raise TokenFactoryError(f"the {deployment.template_id} template has no mint capability")
    w3, contract = _live_contract(deployment)
    on_chain_owner = contract.functions.owner().call()
    if on_chain_owner.lower() != wallet.address.lower():
        raise TokenFactoryError(
            f"wallet {wallet.address} is not the on-chain owner ({on_chain_owner}); mint refused"
        )
    account = w3.eth.account.from_key(private_key)
    tx = contract.functions.mint(Web3.to_checksum_address(to_address), amount_raw).build_transaction({
        "from": account.address, "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": w3.eth.gas_price, "chainId": w3.eth.chain_id,
    })
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    from app.routers.chat import _record_submitted_transaction
    _record_submitted_transaction(
        db, wallet_id=wallet.id, network=deployment.network, tx_hash=tx_hash,
        from_address=wallet.address, to_address=to_address, amount=float(Decimal(amount_raw) / (Decimal(10) ** deployment.decimals)),
        amount_raw=amount_raw, decimals=deployment.decimals, token=deployment.symbol, category="token_mint",
    )
    return tx_hash


def burn_token(db, *, deployment: TokenDeployment, wallet: Wallet, private_key: str, amount_raw: int,
               from_address: str | None = None) -> str:
    w3, contract = _live_contract(deployment)
    account = w3.eth.account.from_key(private_key)
    target = Web3.to_checksum_address(from_address) if from_address else account.address

    if target.lower() == account.address.lower():
        balance = contract.functions.balanceOf(account.address).call()
        if balance < amount_raw:
            raise TokenFactoryError(f"insufficient balance to burn: {balance} available, {amount_raw} requested")
        fn = contract.functions.burn(amount_raw)
    else:
        allowance = contract.functions.allowance(target, account.address).call()
        if allowance < amount_raw:
            raise TokenFactoryError(
                f"burnFrom refused: {account.address} has only {allowance} allowance from {target}, needs {amount_raw}"
            )
        fn = contract.functions.burnFrom(target, amount_raw)

    tx = fn.build_transaction({
        "from": account.address, "nonce": w3.eth.get_transaction_count(account.address),
        "gasPrice": w3.eth.gas_price, "chainId": w3.eth.chain_id,
    })
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    from app.routers.chat import _record_submitted_transaction
    _record_submitted_transaction(
        db, wallet_id=wallet.id, network=deployment.network, tx_hash=tx_hash,
        from_address=target, to_address=deployment.contract_address,
        amount=float(Decimal(amount_raw) / (Decimal(10) ** deployment.decimals)),
        amount_raw=amount_raw, decimals=deployment.decimals, token=deployment.symbol, category="token_burn",
    )
    return tx_hash
