"""Tests for adding Arc (Circle's own L1) as a supported network.

Deliberately narrow scope: wallet creation, balance display, and plain
sends only. Swap (Paraswap), bridge (LI.FI), CCTP, Aave and the x402
facilitators are NOT wired in - none of them have a confirmed Arc
integration, and test_security_regressions.py's
test_only_usdc_and_native_assets_are_trusted already asserts Paraswap
correctly treats Arc as unsupported.

Arc's one real wrinkle: it pays gas in USDC itself, so its native balance
and its USDC (ERC-20) balance are the same money in two decimal
representations (18 vs 6), not two separate assets - these tests guard
against showing or summing that as two separate holdings.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.core import assets
from app.chains import evm
from app.tools.wallet import tokens


class LiveNetworkVerificationTests(unittest.TestCase):
    """Reproduces, as a permanent regression check, the live verification
    done before adding this network: Arc's RPC actually reports chain id
    5042, and the claimed USDC precompile actually behaves as a standard
    ERC-20 with the expected symbol/decimals. Skips automatically without
    network access."""

    def test_arc_rpc_reports_the_correct_chain_id(self):
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(evm._RPC["arc"]))
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            self.skipTest("no network access to verify Arc's live chain id")
        self.assertEqual(chain_id, 5042)
        self.assertEqual(evm._CHAIN_IDS["arc"], 5042)

    def test_arcs_usdc_precompile_behaves_as_a_standard_erc20(self):
        from web3 import Web3
        abi = [
            {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
            {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
        ]
        w3 = Web3(Web3.HTTPProvider(evm._RPC["arc"]))
        try:
            w3.eth.get_block_number()
        except Exception:
            self.skipTest("no network access to verify Arc's live USDC precompile")
        contract = w3.eth.contract(address=Web3.to_checksum_address(assets.NETWORKS["arc"]["usdc"]), abi=abi)
        self.assertEqual(contract.functions.decimals().call(), 6)
        self.assertEqual(contract.functions.symbol().call(), "USDC")


class AssetRegistryTests(unittest.TestCase):
    def test_arc_is_registered_with_usdc_as_its_native_asset(self):
        self.assertIn("arc", assets.NETWORKS)
        self.assertEqual(assets.NETWORKS["arc"]["native"], "USDC")
        self.assertEqual(assets.NETWORKS["arc"]["chain_id"], 5042)

    def test_usdc_cannot_be_hidden_on_arc_since_its_also_the_gas_token(self):
        with patch.dict("os.environ", {"SARA_ENABLED_NETWORKS": "arc", "SARA_USDC_NETWORKS": ""}):
            # Even with Arc excluded from SARA_USDC_NETWORKS, USDC is still
            # trusted there - it's unconditionally required for gas, same
            # as ETH/POL can't be "hidden" on any other network.
            self.assertTrue(assets.token_enabled("USDC", "arc"))


class Erc20BalancesFallbackTests(unittest.TestCase):
    """The real bug this addition would otherwise have exposed: any
    network absent from tokens.py's Alchemy-slug map silently defaulted
    to querying Ethereum mainnet's Alchemy endpoint - wrong chain
    entirely - rather than either failing cleanly or using a working
    fallback."""

    def test_a_network_alchemy_doesnt_cover_uses_a_direct_rpc_call_not_the_wrong_chain(self):
        with patch("app.chains.evm.get_erc20_balance", return_value=12.5) as direct_call, \
             patch("requests.post") as alchemy_post:
            result = tokens.get_erc20_balances("0x" + "11" * 20, "arc")
        alchemy_post.assert_not_called()
        # Two calls now, not one: Arc trusts both USDC (native) and EURC
        # (see test_eurc.py) for balance display, each checked via its own
        # direct RPC balanceOf() call.
        self.assertEqual(direct_call.call_count, 2)
        direct_call.assert_any_call(assets.NETWORKS["arc"]["usdc"], 6, "0x" + "11" * 20, "arc")
        direct_call.assert_any_call(assets.EURC_ADDRESSES["arc"], assets.EURC_DECIMALS, "0x" + "11" * 20, "arc")
        self.assertCountEqual(result, [
            {"symbol": "USDC", "name": "USD Coin", "balance": 12.5, "network": "arc"},
            {"symbol": "EURC", "name": "EURC", "balance": 12.5, "network": "arc"},
        ])

    def test_a_zero_balance_returns_no_holdings(self):
        with patch("app.chains.evm.get_erc20_balance", return_value=0.0):
            result = tokens.get_erc20_balances("0x" + "11" * 20, "arc")
        self.assertEqual(result, [])

    def test_an_rpc_failure_returns_empty_not_an_exception(self):
        with patch("app.chains.evm.get_erc20_balance", side_effect=Exception("rpc down")):
            result = tokens.get_erc20_balances("0x" + "11" * 20, "arc")
        self.assertEqual(result, [])

    def test_a_network_alchemy_does_cover_is_unaffected(self):
        """Regression guard: the fallback path must not change existing,
        working behavior for a network Alchemy already supports."""
        with patch.dict("os.environ", {"ALCHEMY_API_KEY": "test-key"}), \
             patch("requests.post") as alchemy_post, \
             patch("app.chains.evm.get_erc20_balance") as direct_call:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": {"tokenBalances": []}}
            alchemy_post.return_value = mock_resp
            tokens.get_erc20_balances("0x" + "11" * 20, "ethereum")
        alchemy_post.assert_called_once()
        direct_call.assert_not_called()


class PortfolioDoubleCountingTests(unittest.TestCase):
    """portfolio.py combines a per-network native-gas fetch with a
    per-network USDC fetch into one summed total - on every other network
    those are genuinely different assets, but on Arc they're the same
    money, so the native fetch must be skipped there entirely."""

    def test_arc_is_excluded_from_the_native_balance_fetch(self):
        from app.routers import portfolio
        self.assertNotIn("arc", portfolio.NATIVE_SYMBOLS)


class ListWalletsDoesNotDoubleShowArcTests(unittest.TestCase):
    """Exercises the real chat "list_wallets" command end to end (not just
    a re-derived list comprehension) - same double-counting risk as
    portfolio.py, in its own separate code path."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base, Wallet
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)()
        self.wallet = Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_arcs_native_balance_is_never_fetched_only_its_usdc_balance(self):
        from app.routers.chat import _handle_tool_call

        def fake_native_balance(addr, net):
            self.assertNotEqual(net, "arc", "list_wallets must never fetch Arc's native balance")
            return {"balance": 0, "unit": "ETH", "network": net}

        with patch("app.core.assets.enabled_networks", return_value=("arc",)), \
             patch("app.chains.evm.get_balance", side_effect=fake_native_balance), \
             patch("app.tools.wallet.tokens.get_erc20_balances",
                   return_value=[{"symbol": "USDC", "name": "USD Coin", "balance": 42.0, "network": "arc"}]):
            result = _handle_tool_call("list_wallets", {}, self.db)

        # Exactly one balance line for Arc - not a native line and a
        # separate ERC-20 line both claiming to be the same USDC.
        self.assertEqual(result.count("**USDC**"), 1)
        self.assertIn("42.000000", result)


if __name__ == "__main__":
    unittest.main()
