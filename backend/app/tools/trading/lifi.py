"""Cross-chain swaps via LI.FI (https://li.quest) — bridges + DEXs aggregated
behind one API. This is a separate, independent integration from
paraswap.py (same-chain EVM swaps) and jupiter.py (same-chain Solana swaps);
it doesn't share code with either and doesn't touch them.

LI.FI only supports EVM chains — no Solana route exists here.
"""
import requests
from web3 import Web3

_BASE = "https://li.quest/v1"

# Same numeric chain IDs as backend/app/chains/evm.py — these are standard,
# public EVM chain IDs, not something LI.FI-specific.
CHAIN_IDS = {
    "ethereum": 1, "polygon": 137, "arbitrum": 42161,
    "base": 8453, "optimism": 10, "bsc": 56, "avalanche": 43114,
}

# LI.FI represents the native asset as the zero address — confirmed against
# the live API, and different from Paraswap's 0xEeee... convention, so this
# can't be blindly shared between the two integrations.
_NATIVE = "0x0000000000000000000000000000000000000000"

# Official production deployments from LI.FI's lifinance/contracts
# deployment manifests (verified 2026-07-15). The Diamond is deployed at
# the same CREATE2 address on every supported EVM chain; approval proxies
# differ on some networks.
_LIFI_DIAMOND = "0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE"
_PERMIT2_PROXY = "0x89c6340B1a1f4b25D36cd8B063D49045caF3f818"
_ERC20_PROXIES = {
    "ethereum": "0x68E1Acfa805dcA813116Ed6507E01c38D44318f0",
    "polygon": "0x5741A7FfE7c39Ca175546a54985fA79211290b51",
    "arbitrum": "0x5741A7FfE7c39Ca175546a54985fA79211290b51",
    "base": "0x74a55CaDb12501A3707E9F3C5dfd8b563C6A5940",
    "optimism": "0x314bE5fcf0A204837896e6028C47A9e1FC2919c7",
    "bsc": "0x5741A7FfE7c39Ca175546a54985fA79211290b51",
    "avalanche": "0x5741A7FfE7c39Ca175546a54985fA79211290b51",
}
_MAX_BRIDGE_GAS_LIMIT = 1_500_000
_MAX_BRIDGE_FEE_WEI = 50_000_000_000_000_000  # 0.05 native asset

_CALLDATA_VERIFIER_ABI = [{
    "name": "extractMainParameters", "type": "function", "stateMutability": "pure",
    "inputs": [{"name": "data", "type": "bytes"}],
    "outputs": [
        {"name": "bridge", "type": "string"},
        {"name": "sendingAssetId", "type": "address"},
        {"name": "receiver", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "destinationChainId", "type": "uint256"},
        {"name": "hasSourceSwaps", "type": "bool"},
        {"name": "hasDestinationCall", "type": "bool"},
    ],
}]


def resolve_token(symbol: str, network: str) -> tuple[str, int] | None:
    """Native asset uses LI.FI's zero-address convention; ERC-20 tokens reuse
    the already-verified address list in paraswap.py (contract addresses
    aren't aggregator-specific, unlike the native-asset placeholder)."""
    from app.chains.evm import _NATIVE_TOKEN
    native_symbol = _NATIVE_TOKEN.get(network.lower())
    if native_symbol and symbol.upper() == native_symbol:
        return (_NATIVE, 18)
    from app.tools.market.paraswap import resolve_token as _paraswap_resolve
    return _paraswap_resolve(symbol, network)


def trusted_symbols(network: str) -> list[str]:
    from app.chains.evm import _NATIVE_TOKEN
    from app.tools.market.paraswap import trusted_symbols as _paraswap_trusted
    native = _NATIVE_TOKEN.get(network.lower())
    symbols = _paraswap_trusted(network)
    if native and native not in symbols:
        symbols = [native] + symbols
    return symbols


def resolve_token_with_correction(symbol: str, network: str) -> tuple[tuple[str, int] | None, str | None]:
    """Like resolve_token, but typo-corrects against this network's trusted
    symbols (native + paraswap's verified ERC-20 list). Returns
    (result, corrected_symbol) — corrected_symbol is None unless a
    correction was actually applied."""
    result = resolve_token(symbol, network)
    if result:
        return result, None
    from app.tools.wallet.token_trust import fuzzy_correct
    corrected = fuzzy_correct(symbol, trusted_symbols(network))
    if corrected:
        return resolve_token(corrected, network), corrected
    return None, None


_ERC20_ABI = [
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]


def get_quote(from_network: str, to_network: str, from_token: str, to_token: str,
              amount_wei: int, from_address: str, slippage: float = 0.005) -> dict | None:
    from_chain = CHAIN_IDS.get(from_network.lower())
    to_chain = CHAIN_IDS.get(to_network.lower())
    if not from_chain or not to_chain:
        return None
    try:
        r = requests.get(f"{_BASE}/quote", params={
            "fromChain": from_chain, "toChain": to_chain,
            "fromToken": from_token, "toToken": to_token,
            "fromAmount": str(amount_wei),
            "fromAddress": from_address,
            "toAddress": from_address,  # self-bridge only, never a different recipient
            "slippage": slippage,
        }, timeout=15)
        return r.json() if r.ok else None
    except Exception:
        return None


def _send_approve(w3, chain_id, account, private_key, contract, spender_addr, value):
    gas_price = int(w3.eth.gas_price)
    if 100_000 * gas_price > _MAX_BRIDGE_FEE_WEI:
        raise ValueError("Refusing approval: estimated approval fee exceeds 0.05 native asset.")
    tx = contract.functions.approve(spender_addr, value).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": 100000,
        "gasPrice": gas_price,
        "chainId": chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash


def _require_contract(w3, address: str, label: str):
    """Defense-in-depth check after the official address allowlist: a
    supported deployment must also still contain bytecode on this chain."""
    checksummed = Web3.to_checksum_address(address)
    if w3.eth.get_code(checksummed) in (b"", b"\x00"):
        raise ValueError(
            f"Refusing to sign: LI.FI's {label} address ({checksummed}) has no contract code — "
            f"a real bridge/DEX route never points here. This could indicate a compromised or "
            f"malformed API response."
        )


def _allowed_approval_spenders(network: str) -> set[str]:
    values = {_LIFI_DIAMOND.lower(), _PERMIT2_PROXY.lower()}
    proxy = _ERC20_PROXIES.get(network.lower())
    if proxy:
        values.add(proxy.lower())
    return values


def max_total_network_fee_wei(source_token: str | None) -> int:
    """Hard confirmation ceiling across every transaction this bridge can
    submit. ERC-20 routes may need reset-approval + exact approval + bridge;
    native routes submit only the bridge transaction."""
    return _MAX_BRIDGE_FEE_WEI * (1 if not source_token else 3)


def _extract_main_parameters(w3, tx_data: str) -> tuple:
    """Ask LI.FI's audited on-chain CalldataVerificationFacet to decode the
    exact bytes Sara is considering signing. This supports every installed
    bridge facet without maintaining a fragile local selector/ABI list."""
    if not isinstance(tx_data, str) or not tx_data.startswith("0x") or len(tx_data) < 10:
        raise ValueError("Refusing to sign: LI.FI returned malformed calldata.")
    try:
        raw = bytes.fromhex(tx_data[2:])
        verifier = w3.eth.contract(
            address=Web3.to_checksum_address(_LIFI_DIAMOND),
            abi=_CALLDATA_VERIFIER_ABI,
        )
        return tuple(verifier.functions.extractMainParameters(raw).call())
    except Exception as exc:
        raise ValueError(
            f"Refusing to sign: LI.FI's on-chain calldata verifier could not decode this route: {exc}"
        ) from exc


def _validate_main_parameters(extracted: tuple, *, wallet_address: str,
                              expected_src_token: str | None, expected_src_amount: int,
                              expected_destination_chain_id: int) -> None:
    if len(extracted) != 7:
        raise ValueError("Refusing to sign: LI.FI calldata verifier returned an unexpected result.")
    _, sending_asset, receiver, amount, destination_chain_id, _, has_destination_call = extracted
    expected_asset = expected_src_token or _NATIVE
    if Web3.to_checksum_address(receiver).lower() != Web3.to_checksum_address(wallet_address).lower():
        raise ValueError(
            f"Refusing to sign: bridge calldata sends destination funds to {receiver}, "
            f"not this wallet ({wallet_address})."
        )
    if int(destination_chain_id) != int(expected_destination_chain_id):
        raise ValueError(
            f"Refusing to sign: bridge calldata targets chain {destination_chain_id}, "
            f"not confirmed chain {expected_destination_chain_id}."
        )
    if Web3.to_checksum_address(sending_asset).lower() != Web3.to_checksum_address(expected_asset).lower():
        raise ValueError(
            f"Refusing to sign: bridge calldata spends {sending_asset}, not the confirmed source asset."
        )
    if int(amount) != int(expected_src_amount):
        raise ValueError(
            f"Refusing to sign: bridge calldata spends {amount}, not the exact confirmed amount "
            f"({expected_src_amount})."
        )
    if bool(has_destination_call):
        raise ValueError(
            "Refusing to sign: this self-bridge contains an unconfirmed destination-chain call."
        )


def validate_bridge_transaction_static(w3, tx_request: dict, wallet_address: str, network: str,
                                       expected_value_wei: int, approval_address: str | None = None, *,
                                       expected_src_token: str | None, expected_src_amount: int,
                                       expected_destination_chain_id: int) -> tuple[str, int]:
    """Validate every aggregator-controlled field that is safe to check
    before an allowance exists. Only LI.FI's official Diamond may execute,
    and approvals may target only official periphery deployments."""
    to_addr = Web3.to_checksum_address(tx_request["to"])
    if to_addr.lower() != _LIFI_DIAMOND.lower():
        raise ValueError(
            f"Refusing to sign: LI.FI returned unrecognized executor {to_addr}; "
            f"expected the official Diamond {_LIFI_DIAMOND}."
        )
    _require_contract(w3, to_addr, "bridge execution")
    if approval_address:
        spender = Web3.to_checksum_address(approval_address)
        if spender.lower() not in _allowed_approval_spenders(network):
            raise ValueError(
                f"Refusing approval: {spender} is not an official LI.FI spender on {network}."
            )
        _require_contract(w3, spender, "approval spender")

    raw_value = tx_request.get("value", "0x0")
    tx_value = int(raw_value, 16) if isinstance(raw_value, str) else int(raw_value or 0)
    if tx_value != expected_value_wei:
        raise ValueError(
            f"Refusing to sign: transaction value ({tx_value} wei) does not match "
            f"the confirmed bridge amount ({expected_value_wei} wei)."
        )
    _validate_main_parameters(
        _extract_main_parameters(w3, tx_request.get("data", "")),
        wallet_address=wallet_address,
        expected_src_token=expected_src_token,
        expected_src_amount=expected_src_amount,
        expected_destination_chain_id=expected_destination_chain_id,
    )
    return to_addr, tx_value


def _local_fee_fields(w3, call: dict) -> tuple[int, int]:
    estimate = int(w3.eth.estimate_gas(call))
    gas = max(21_000, (estimate * 120 + 99) // 100)
    if gas > _MAX_BRIDGE_GAS_LIMIT:
        raise ValueError(f"Refusing to sign: locally estimated bridge gas limit {gas} is excessive.")
    gas_price = int(w3.eth.gas_price * 1.2)
    if gas * gas_price > _MAX_BRIDGE_FEE_WEI:
        raise ValueError("Refusing to sign: locally estimated bridge fee exceeds 0.05 native asset.")
    return gas, gas_price


def ensure_allowance(private_key: str, token_addr: str, spender: str, amount_wei: int, network: str) -> str | None:
    if int(token_addr, 16) == 0:  # native asset, no approval needed
        return None
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    chain_id = _CHAIN_IDS.get(network.lower(), 1)
    account = w3.eth.account.from_key(private_key)
    if Web3.to_checksum_address(spender).lower() not in _allowed_approval_spenders(network):
        raise ValueError(f"Refusing approval: {spender} is not an official LI.FI spender on {network}.")
    _require_contract(w3, spender, "approval spender")
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    spender_addr = Web3.to_checksum_address(spender)
    current = contract.functions.allowance(account.address, spender_addr).call()
    if current == amount_wei:
        return None
    # Some tokens (Tether's original contract, and several bridged/L2 USDT
    # deployments that copy its behavior) revert if you approve a new
    # non-zero value while the existing allowance is already non-zero — you
    # have to reset to 0 first. Also approve the exact amount needed rather
    # than an unlimited amount: safer for the user, and avoids a class of
    # contracts that reject max-uint256 approvals outright.
    last_hash = None
    if current > 0:
        last_hash = _send_approve(w3, chain_id, account, private_key, contract, spender_addr, 0)
    last_hash = _send_approve(w3, chain_id, account, private_key, contract, spender_addr, amount_wei)
    return last_hash.hex()


def validate_bridge_transaction(w3, tx_request: dict, wallet_address: str, network: str,
                                 expected_value_wei: int, *, approval_address: str | None = None,
                                 expected_src_token: str | None, expected_dst_token: str | None,
                                 expected_src_amount: int, expected_min_dst_amount: int,
                                 expected_destination_chain_id: int,
                                 gas: int | None = None, gas_price: int | None = None) -> tuple[str, int]:
    """Repeat official executor/spender/value checks and, where Alchemy is
    available, simulate the exact locally-bounded source-chain transaction.
    Destination receipt is intentionally not required here: cross-chain
    settlement occurs asynchronously on the destination network."""
    from app.tools.market.tx_simulate import verify_swap_effect
    to_addr, tx_value = validate_bridge_transaction_static(
        w3, tx_request, wallet_address, network, expected_value_wei, approval_address,
        expected_src_token=expected_src_token,
        expected_src_amount=expected_src_amount,
        expected_destination_chain_id=expected_destination_chain_id,
    )

    # Asset-change simulation is defense in depth after official deployment,
    # value, allowance, gas and fee bounds. It is unavailable on BSC/AVAX and
    # for installations without Alchemy, where those hard bounds still cap
    # the transaction to exactly the confirmed source amount.
    import os
    from app.chains.evm import ALCHEMY_NETWORK_SLUGS
    if os.getenv("ALCHEMY_API_KEY", "").strip() and network.lower() in ALCHEMY_NETWORK_SLUGS:
        verify_swap_effect(
            network, wallet_address, to_addr, tx_request["data"], tx_value,
            wallet_address=wallet_address,
            expected_src_token=expected_src_token, expected_dst_token=expected_dst_token,
            expected_src_amount=expected_src_amount, expected_min_dst_amount=expected_min_dst_amount,
            verify_destination=False, gas=gas, gas_price=gas_price,
        )
    return to_addr, tx_value


def execute_bridge(private_key: str, tx_request: dict, network: str, expected_value_wei: int = 0, *,
                    expected_recipient: str, approval_address: str | None = None,
                    expected_src_token: str | None, expected_dst_token: str | None,
                    expected_src_amount: int, expected_destination_chain_id: int,
                    expected_min_dst_amount: int = 0) -> str:
    """Signs and broadcasts a LI.FI-provided bridge transaction — re-running
    validate_bridge_transaction here too (even though callers are expected
    to have already run it before granting any allowance) so this function
    is safe to call on its own, not just as part of the
    validate-then-approve-then-execute sequence."""
    from app.chains.evm import get_web3, _CHAIN_IDS
    w3 = get_web3(network)
    account = w3.eth.account.from_key(private_key)
    if account.address.lower() != expected_recipient.lower():
        raise ValueError("Refusing to sign: bridge recipient does not match the signing wallet.")

    to_addr, tx_value = validate_bridge_transaction_static(
        w3, tx_request, account.address, network, expected_value_wei, approval_address,
        expected_src_token=expected_src_token,
        expected_src_amount=expected_src_amount,
        expected_destination_chain_id=expected_destination_chain_id,
    )
    call = {
        "from": account.address, "to": to_addr, "data": tx_request["data"], "value": tx_value,
    }
    gas, gas_price = _local_fee_fields(w3, call)
    validate_bridge_transaction(
        w3, tx_request, account.address, network, expected_value_wei,
        approval_address=approval_address,
        expected_src_token=expected_src_token, expected_dst_token=expected_dst_token,
        expected_src_amount=expected_src_amount, expected_min_dst_amount=expected_min_dst_amount,
        expected_destination_chain_id=expected_destination_chain_id,
        gas=gas, gas_price=gas_price,
    )

    tx = {
        "from":     account.address,
        "to":       to_addr,
        "data":     tx_request["data"],
        "value":    tx_value,
        "gas":      gas,
        "gasPrice": gas_price,
        "nonce":    w3.eth.get_transaction_count(account.address),
        "chainId":  _CHAIN_IDS.get(network.lower(), 1),
    }
    signed = w3.eth.account.sign_transaction(tx, private_key)
    return w3.eth.send_raw_transaction(signed.raw_transaction).hex()


def get_status(tx_hash: str) -> dict | None:
    try:
        r = requests.get(f"{_BASE}/status", params={"txHash": tx_hash}, timeout=15)
        return r.json() if r.ok else None
    except Exception:
        return None
