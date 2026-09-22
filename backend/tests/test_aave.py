"""Tests for Sara's Aave v3 USDC supply/withdraw integration.

AddressTriangulationTests hits live public RPCs to reproduce, as a
permanent regression check, the same independent verification done before
writing this module: that every Pool/aToken address here actually agrees
with what the real, currently-deployed contracts report about themselves
(the aToken's own UNDERLYING_ASSET_ADDRESS(), and the Pool's own
getReserveData() for that asset) — not just that they look plausible.
Skipped automatically if there's no network access.

Everything else mocks web3 and exercises the actual signing/safety logic:
fee caps, exact-amount (not unlimited) approval, refusing a zero amount,
and the WITHDRAW_ALL sentinel for "withdraw everything".
"""
import unittest
from unittest.mock import MagicMock, patch

from app.tools.lending import aave


class AddressTriangulationTests(unittest.TestCase):
    """Live, opt-in cross-check — see module docstring."""

    def _skip_if_unreachable(self, w3):
        try:
            w3.eth.get_block_number()
        except Exception:
            self.skipTest("no network access to verify live contract state")

    def test_every_networks_atoken_and_pool_agree_on_the_same_usdc(self):
        from web3 import Web3
        from app.chains.evm import _RPC

        atoken_abi = [{"inputs": [], "name": "UNDERLYING_ASSET_ADDRESS",
                       "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}]

        for network in aave.SUPPORTED_NETWORKS:
            with self.subTest(network=network):
                w3 = Web3(Web3.HTTPProvider(_RPC[network]))
                self._skip_if_unreachable(w3)
                expected_usdc = Web3.to_checksum_address(aave.usdc_address(network))

                atoken = w3.eth.contract(
                    address=Web3.to_checksum_address(aave.A_TOKEN_ADDRESSES[network]), abi=atoken_abi,
                )
                self.assertEqual(
                    atoken.functions.UNDERLYING_ASSET_ADDRESS().call(), expected_usdc,
                    f"{network}: aToken's own underlying doesn't match Sara's trusted USDC",
                )

                pool = w3.eth.contract(
                    address=Web3.to_checksum_address(aave.POOL_ADDRESSES[network]), abi=aave._POOL_ABI,
                )
                reserve = pool.functions.getReserveData(expected_usdc).call()
                self.assertEqual(
                    reserve[8], Web3.to_checksum_address(aave.A_TOKEN_ADDRESSES[network]),
                    f"{network}: Pool's own getReserveData disagrees with the configured aToken address",
                )


class UnsupportedNetworkTests(unittest.TestCase):
    def test_rejects_a_network_aave_isnt_configured_for(self):
        with self.assertRaises(aave.AaveError):
            aave.usdc_address("solana")

    def test_get_position_rejects_unsupported_network(self):
        with self.assertRaises(aave.AaveError):
            aave.get_position("0x" + "11" * 20, "ethereum-classic")


class _FakeAccount:
    def __init__(self, address, key):
        self.address = address
        self.key = key


def _make_fake_w3(*, allowance=0, chain_id=1):
    """A MagicMock standing in for web3.py's whole Web3 instance, wired
    just enough for execute_supply/execute_withdraw to run start to finish
    without ever touching a network."""
    w3 = MagicMock()
    account = _FakeAccount("0x" + "22" * 20, b"key")
    w3.eth.account.from_key.return_value = account
    w3.eth.get_transaction_count.return_value = 1
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.gas_price = 10 * 10**9  # 10 gwei
    w3.eth.wait_for_transaction_receipt.return_value = {"status": 1}

    sent_hash = MagicMock()
    sent_hash.hex.return_value = "0xhash"
    w3.eth.send_raw_transaction.return_value = sent_hash
    signed = MagicMock()
    signed.raw_transaction = b"raw"
    w3.eth.account.sign_transaction.return_value = signed

    erc20 = MagicMock()
    erc20.functions.allowance.return_value.call.return_value = allowance
    approve_tx = MagicMock()
    approve_tx.functions.approve.return_value.build_transaction.return_value = {"data": "0xapprove"}

    pool_contract = MagicMock()
    pool_contract.functions.supply.return_value.build_transaction.return_value = {"data": "0xsupply"}
    pool_contract.functions.withdraw.return_value.build_transaction.return_value = {"data": "0xwithdraw"}

    def contract(address, abi):
        return erc20 if abi is aave._ERC20_ABI else pool_contract
    w3.eth.contract.side_effect = contract
    return w3, account, erc20, pool_contract


class ExecuteSupplyTests(unittest.TestCase):
    def test_rejects_zero_amount(self):
        with self.assertRaises(aave.AaveError):
            aave.execute_supply("0xkey", "ethereum", 0)

    def test_approves_exactly_the_supplied_amount_when_allowance_is_insufficient(self):
        w3, account, erc20, pool_contract = _make_fake_w3(allowance=0)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"ethereum": 1}):
            tx_hash = aave.execute_supply("0xkey", "ethereum", 1_000_000)
        erc20.functions.approve.assert_called_once()
        approved_spender, approved_amount = erc20.functions.approve.call_args[0]
        self.assertEqual(approved_amount, 1_000_000)  # exact amount, never unlimited (2**256-1)
        pool_contract.functions.supply.assert_called_once()
        args = pool_contract.functions.supply.call_args[0]
        self.assertEqual(args[1], 1_000_000)
        self.assertEqual(args[3], 0)  # referral code always 0
        self.assertEqual(tx_hash, "0xhash")

    def test_skips_approval_when_allowance_already_sufficient(self):
        w3, account, erc20, pool_contract = _make_fake_w3(allowance=5_000_000)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"ethereum": 1}):
            aave.execute_supply("0xkey", "ethereum", 1_000_000)
        erc20.functions.approve.assert_not_called()

    def test_refuses_when_locally_estimated_fee_is_excessive(self):
        w3, account, erc20, pool_contract = _make_fake_w3(allowance=5_000_000)
        w3.eth.gas_price = 10**18  # absurdly high, forces the fee cap to trip
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"ethereum": 1}):
            with self.assertRaises(aave.AaveError):
                aave.execute_supply("0xkey", "ethereum", 1_000_000)


class ExecuteWithdrawTests(unittest.TestCase):
    def test_none_amount_uses_aaves_withdraw_all_sentinel(self):
        w3, account, erc20, pool_contract = _make_fake_w3()
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"ethereum": 1}):
            aave.execute_withdraw("0xkey", "ethereum", None)
        args = pool_contract.functions.withdraw.call_args[0]
        self.assertEqual(args[1], aave.WITHDRAW_ALL)
        self.assertEqual(args[1], 2**256 - 1)

    def test_specific_amount_is_passed_through_unchanged(self):
        w3, account, erc20, pool_contract = _make_fake_w3()
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"ethereum": 1}):
            aave.execute_withdraw("0xkey", "ethereum", 250_000)
        args = pool_contract.functions.withdraw.call_args[0]
        self.assertEqual(args[1], 250_000)

    def test_rejects_zero_amount(self):
        with self.assertRaises(aave.AaveError):
            aave.execute_withdraw("0xkey", "ethereum", 0)

    def test_withdraw_never_touches_the_erc20_approval_path(self):
        """Withdrawing one's own supplied funds needs no ERC-20 approval —
        the Pool has its own mint/burn rights over aTokens it issued."""
        w3, account, erc20, pool_contract = _make_fake_w3()
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"ethereum": 1}):
            aave.execute_withdraw("0xkey", "ethereum", 250_000)
        erc20.functions.approve.assert_not_called()
        erc20.functions.allowance.assert_not_called()


class GetSupplyApyTests(unittest.TestCase):
    def test_computes_apy_from_the_live_liquidity_rate(self):
        w3 = MagicMock()
        pool_contract = MagicMock()
        # currentLiquidityRate is field index 2 of the 15-field ReserveData
        # tuple; a value of 0.05 * RAY is a flat 5% APR, compounded per
        # second into a slightly higher APY.
        reserve_data = [0] * 15
        reserve_data[2] = int(0.05 * aave._RAY)
        pool_contract.functions.getReserveData.return_value.call.return_value = reserve_data
        w3.eth.contract.return_value = pool_contract
        with patch("app.chains.evm.get_web3", return_value=w3):
            apy = aave.get_supply_apy("ethereum")
        self.assertAlmostEqual(apy, 5.127, places=2)  # (1+0.05/n)^n - 1, n=seconds/year

    def test_rejects_unsupported_network(self):
        with self.assertRaises(aave.AaveError):
            aave.get_supply_apy("solana")


if __name__ == "__main__":
    unittest.main()
