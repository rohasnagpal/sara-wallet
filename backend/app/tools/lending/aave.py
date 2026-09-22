"""Aave v3 USDC supply/withdraw — Sara's first DeFi yield integration.

Deliberately supply/withdraw only: no borrowing, no collateral or health-
factor tracking, no liquidation risk. A user deposits their own USDC into
Aave's v3 lending pool and earns the pool's variable supply rate; Aave
mints aUSDC 1:1 as a rebasing receipt token that accrues yield on its own
— Sara never needs to claim or compound anything separately. Withdrawing
simply burns aUSDC back for USDC (plus whatever it accrued) straight from
the pool; no prior approval is needed to withdraw one's own supplied funds
(only supplying requires approving the Pool to pull USDC first).

Every address below was independently verified, not copied from memory or
a single doc page:
  - Pool addresses come from Aave's own canonical address-book
    (github.com/bgd-labs/aave-address-book — the exact source Aave's own
    docs at aave.com/docs/resources/addresses point integrators to).
  - Each aToken address was cross-checked two more ways: (1) calling the
    aToken contract's own UNDERLYING_ASSET_ADDRESS() live over each
    network's RPC and confirming it returns exactly Sara's own trusted
    USDC contract (app.core.assets.NETWORKS) for that network, and
    (2) calling the Pool contract's own getReserveData(usdc_address) live
    and confirming its returned aTokenAddress field matches. All five
    checks passed; see the module-level test file for the same
    cross-checks reproduced against a live RPC.
  - Arbitrum, Optimism and Polygon each have *two* separate USDC reserves
    on Aave: a legacy bridged "USDC" (USDC.e, not what Sara trusts) and a
    native "USDCn" reserve. This module always uses the USDCn aToken on
    those three networks — using the wrong one would supply against a
    token Sara's wallets don't actually hold. Ethereum and Base only ever
    had one native USDC reserve, so no such split applies there.

Referral code is always 0 — Aave's referral program has been inactive
(fee is always zero) for a long time; there's no reason to plumb a nonzero
value through here.
"""
from __future__ import annotations

from web3 import Web3

from app.core.assets import NETWORKS

_MAX_GAS_LIMIT = 800_000
_MAX_FEE_WEI = 50_000_000_000_000_000  # 0.05 native asset — matches paraswap.py's cap
_REFERRAL_CODE = 0
_SECONDS_PER_YEAR = 31_536_000
_RAY = 10**27

# Pool contract per network — see module docstring for how these were verified.
POOL_ADDRESSES = {
    "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "base":     "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "optimism": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "polygon":  "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
}

# aUSDC (or aUSDCn — see module docstring) per network — always the reserve
# whose underlying is Sara's own trusted USDC contract for that network.
A_TOKEN_ADDRESSES = {
    "ethereum": "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
    "base":     "0x4e65fE4DbA92790696d040ac24Aa414708F5c0AB",
    "arbitrum": "0x724dc807b04555b71ed48a6896b6F41593b8C637",
    "optimism": "0x38d693cE1dF5AaDF7bC62595A37D667aD57922e5",
    "polygon":  "0xA4D94019934D8333Ef880ABFFbF2FDd611C762BD",
}

SUPPORTED_NETWORKS = tuple(POOL_ADDRESSES)
_USDC_DECIMALS = 6
WITHDRAW_ALL = 2**256 - 1  # Aave's own documented sentinel for "withdraw my full balance"

_ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

_POOL_ABI = [
    {"inputs": [{"name": "asset", "type": "address"}, {"name": "amount", "type": "uint256"},
                {"name": "onBehalfOf", "type": "address"}, {"name": "referralCode", "type": "uint16"}],
     "name": "supply", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "asset", "type": "address"}, {"name": "amount", "type": "uint256"},
                {"name": "to", "type": "address"}],
     "name": "withdraw", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "asset", "type": "address"}], "name": "getReserveData",
     "outputs": [{"components": [
        {"name": "configuration", "type": "tuple", "components": [{"name": "data", "type": "uint256"}]},
        {"name": "liquidityIndex", "type": "uint128"},
        {"name": "currentLiquidityRate", "type": "uint128"},
        {"name": "variableBorrowIndex", "type": "uint128"},
        {"name": "currentVariableBorrowRate", "type": "uint128"},
        {"name": "currentStableBorrowRate", "type": "uint128"},
        {"name": "lastUpdateTimestamp", "type": "uint40"},
        {"name": "id", "type": "uint16"},
        {"name": "aTokenAddress", "type": "address"},
        {"name": "stableDebtTokenAddress", "type": "address"},
        {"name": "variableDebtTokenAddress", "type": "address"},
        {"name": "interestRateStrategyAddress", "type": "address"},
        {"name": "accruedToTreasury", "type": "uint128"},
        {"name": "unbacked", "type": "uint128"},
        {"name": "isolationModeTotalDebt", "type": "uint128"},
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
]


class AaveError(Exception):
    pass


def _require_network(network: str) -> str:
    network = network.lower()
    if network not in SUPPORTED_NETWORKS:
        raise AaveError(f"Aave USDC yield is only available on: {', '.join(SUPPORTED_NETWORKS)}")
    return network


def usdc_address(network: str) -> str:
    """Always Sara's own trusted USDC contract — never derived from Aave's
    reserve list or any other external input, matching the app's existing
    'token symbols only ever resolve to a hardcoded, developer-verified
    contract list' principle (see paraswap.trusted_symbols)."""
    network = _require_network(network)
    return NETWORKS[network]["usdc"]


def get_position(wallet_address: str, network: str) -> float:
    """The wallet's current aUSDC balance — its USDC principal plus
    whatever it has earned so far, in one number (aUSDC rebases 1:1 with
    the underlying, unlike, say, an ERC-4626 vault share)."""
    from app.chains.evm import get_erc20_balance
    network = _require_network(network)
    return get_erc20_balance(A_TOKEN_ADDRESSES[network], _USDC_DECIMALS, wallet_address, network)


def get_supply_apy(network: str) -> float:
    """Current variable supply APY, computed from the Pool's own live
    currentLiquidityRate (a per-second rate in 'ray' units, i.e. scaled by
    1e27) — not a cached or third-party number."""
    from app.chains.evm import get_web3
    network = _require_network(network)
    w3 = get_web3(network)
    pool = w3.eth.contract(address=Web3.to_checksum_address(POOL_ADDRESSES[network]), abi=_POOL_ABI)
    data = pool.functions.getReserveData(Web3.to_checksum_address(usdc_address(network))).call()
    liquidity_rate = data[2]  # currentLiquidityRate — see _POOL_ABI field order
    apr = liquidity_rate / _RAY
    apy = (1 + apr / _SECONDS_PER_YEAR) ** _SECONDS_PER_YEAR - 1
    return apy * 100


def _local_fee_fields(w3, call: dict) -> tuple[int, int]:
    estimate = int(w3.eth.estimate_gas(call))
    gas = max(21_000, (estimate * 120 + 99) // 100)
    if gas > _MAX_GAS_LIMIT:
        raise AaveError(f"Refusing to sign: locally estimated gas limit {gas} is excessive.")
    gas_price = int(w3.eth.gas_price * 1.2)
    if gas * gas_price > _MAX_FEE_WEI:
        raise AaveError("Refusing to sign: locally estimated network fee exceeds 0.05 native asset.")
    return gas, gas_price


def _ensure_allowance(w3, account, token_addr: str, spender: str, amount_raw: int, chain_id: int) -> str | None:
    contract = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=_ERC20_ABI)
    current = contract.functions.allowance(account.address, Web3.to_checksum_address(spender)).call()
    if current >= amount_raw:
        return None
    # Approve exactly the amount being supplied — no standing/unlimited
    # allowance left behind afterwards, same principle the audit review
    # applied to every other approval flow in this app.
    call = contract.functions.approve(Web3.to_checksum_address(spender), amount_raw).build_transaction({
        "from": account.address, "nonce": w3.eth.get_transaction_count(account.address),
    })
    gas, gas_price = _local_fee_fields(w3, {"from": account.address, "to": token_addr, "data": call["data"]})
    call.update({"gas": gas, "gasPrice": gas_price, "chainId": chain_id})
    signed = w3.eth.account.sign_transaction(call, account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex()


def execute_supply(private_key: str, network: str, amount_raw: int) -> str:
    """Approves the Pool for exactly amount_raw (if not already allowed),
    then supplies that much USDC on the caller's own behalf. Returns the
    supply transaction's hash (the approval, if any, is a separate,
    already-confirmed transaction — its hash isn't otherwise surfaced)."""
    from app.chains.evm import get_web3, _CHAIN_IDS
    network = _require_network(network)
    if amount_raw <= 0:
        raise AaveError("Amount must be greater than zero")
    w3 = get_web3(network)
    chain_id = _CHAIN_IDS[network]
    account = w3.eth.account.from_key(private_key)
    pool_address = POOL_ADDRESSES[network]
    asset = usdc_address(network)

    _ensure_allowance(w3, account, asset, pool_address, amount_raw, chain_id)

    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=_POOL_ABI)
    call = pool.functions.supply(
        Web3.to_checksum_address(asset), amount_raw, account.address, _REFERRAL_CODE,
    ).build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address)})
    gas, gas_price = _local_fee_fields(w3, {"from": account.address, "to": pool_address, "data": call["data"]})
    call.update({"gas": gas, "gasPrice": gas_price, "chainId": chain_id})
    signed = w3.eth.account.sign_transaction(call, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex()


def execute_withdraw(private_key: str, network: str, amount_raw: int | None) -> str:
    """amount_raw=None withdraws the wallet's entire aUSDC balance (Aave's
    own documented WITHDRAW_ALL sentinel) — the exact amount actually
    withdrawn can differ slightly from a balance read moments earlier
    since interest accrues every block; letting the Pool resolve "all"
    itself avoids ever leaving dust behind or reverting on a stale amount."""
    from app.chains.evm import get_web3, _CHAIN_IDS
    network = _require_network(network)
    if amount_raw is not None and amount_raw <= 0:
        raise AaveError("Amount must be greater than zero")
    w3 = get_web3(network)
    chain_id = _CHAIN_IDS[network]
    account = w3.eth.account.from_key(private_key)
    pool_address = POOL_ADDRESSES[network]
    asset = usdc_address(network)

    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=_POOL_ABI)
    call = pool.functions.withdraw(
        Web3.to_checksum_address(asset), WITHDRAW_ALL if amount_raw is None else amount_raw, account.address,
    ).build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address)})
    gas, gas_price = _local_fee_fields(w3, {"from": account.address, "to": pool_address, "data": call["data"]})
    call.update({"gas": gas, "gasPrice": gas_price, "chainId": chain_id})
    signed = w3.eth.account.sign_transaction(call, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex()
