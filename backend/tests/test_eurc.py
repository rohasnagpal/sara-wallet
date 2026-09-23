"""Tests for adding EURC (Circle's euro-backed stablecoin) as a recognized
asset - deliberately narrow scope, same pattern as Arc's own rollout:
wallet balance display and plain sends only, on the three networks EURC is
actually live on among Sara's six (Ethereum, Base, Arc - NOT Arbitrum,
Optimism or Polygon, which Circle has never deployed EURC to). Not wired
into swap (Paraswap), bridge (LI.FI), CCTP (Circle itself says CCTP-for-
EURC is "planned", not live), Aave, x402 or onramp - each of those is a
separate, larger decision.

While building this, two real pre-existing bugs surfaced and are fixed
here too (both blocked Arc regardless of EURC):
  - GET /tokens/trusted 500'd for every network the instant Arc was
    enabled (which is the default) - app.tools.market.paraswap.CHAIN_IDS
    has no "arc" entry, and the endpoint indexed it with [] instead of
    .get().
  - Sending Arc's own native USDC via the chat "send" command was
    unreachable - Arc was simply absent from chat.py's
    _TOKEN_TO_NETWORK/_NETWORK_NATIVE_TOKEN native-token resolution, so it
    fell through to "not recognized" no matter what.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.core import assets
from app.tools.wallet import tokens


class LiveContractVerificationTests(unittest.TestCase):
    """Reproduces, as a permanent regression check, the live verification
    done before adding EURC: each claimed contract actually behaves as a
    standard 6-decimal ERC-20 named/symbol EURC on its real chain id.
    Skips automatically without network access."""

    _ABI = [
        {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
        {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    ]

    def _verify(self, network: str, expected_chain_id: int):
        from web3 import Web3
        from app.chains.evm import _RPC
        w3 = Web3(Web3.HTTPProvider(_RPC[network]))
        try:
            chain_id = w3.eth.chain_id
        except Exception:
            self.skipTest(f"no network access to verify {network}'s live EURC contract")
        self.assertEqual(chain_id, expected_chain_id)
        contract = w3.eth.contract(address=Web3.to_checksum_address(assets.EURC_ADDRESSES[network]), abi=self._ABI)
        self.assertEqual(contract.functions.decimals().call(), 6)
        self.assertEqual(contract.functions.symbol().call(), "EURC")

    def test_ethereum_eurc_contract_is_correct(self):
        self._verify("ethereum", 1)

    def test_base_eurc_contract_is_correct(self):
        self._verify("base", 8453)

    def test_arc_eurc_contract_is_correct(self):
        self._verify("arc", 5042)


class AssetRegistryTests(unittest.TestCase):
    def test_eurc_is_registered_on_exactly_ethereum_base_and_arc(self):
        self.assertEqual(set(assets.EURC_ADDRESSES), {"ethereum", "base", "arc"})

    def test_eurc_is_not_registered_on_arbitrum_optimism_or_polygon(self):
        for network in ("arbitrum", "optimism", "polygon"):
            self.assertNotIn(network, assets.EURC_ADDRESSES)
            self.assertFalse(assets.token_enabled("EURC", network))

    def test_eurc_is_enabled_by_default_on_its_three_networks(self):
        for network in ("ethereum", "base", "arc"):
            self.assertTrue(assets.token_enabled("EURC", network))

    def test_eurc_can_be_hidden_via_env_var_like_usdc(self):
        with patch.dict("os.environ", {"SARA_EURC_NETWORKS": "base"}):
            self.assertTrue(assets.token_enabled("EURC", "base"))
            self.assertFalse(assets.token_enabled("EURC", "ethereum"))
            self.assertFalse(assets.token_enabled("EURC", "arc"))

    def test_resolve_extra_token_returns_address_and_decimals(self):
        result = assets.resolve_extra_token("eurc", "base")
        self.assertEqual(result, (assets.EURC_ADDRESSES["base"], 6))

    def test_resolve_extra_token_is_none_where_eurc_is_not_live(self):
        self.assertIsNone(assets.resolve_extra_token("EURC", "arbitrum"))

    def test_resolve_extra_token_ignores_unrelated_symbols(self):
        self.assertIsNone(assets.resolve_extra_token("USDC", "base"))

    def test_sendable_symbols_includes_eurc_where_live(self):
        self.assertIn("EURC", assets.sendable_symbols("ethereum"))
        self.assertIn("EURC", assets.sendable_symbols("base"))
        self.assertNotIn("EURC", assets.sendable_symbols("arbitrum"))

    def test_sendable_symbols_includes_arcs_native_usdc(self):
        # Regression guard for the real bug this work found: Paraswap's own
        # trusted_symbols("arc") returns [] outright (no Paraswap chain-id
        # entry for Arc at all), which used to silently drop Arc's native
        # USDC from this list too.
        symbols = assets.sendable_symbols("arc")
        self.assertIn("USDC", symbols)
        self.assertIn("EURC", symbols)


class TrustedTokensEndpointTests(unittest.TestCase):
    """GET /tokens/trusted used to raise a bare KeyError (and 500 the whole
    endpoint) the instant Arc was enabled, since Arc has no entry in
    app.tools.market.paraswap.CHAIN_IDS at all. This is a genuine
    pre-existing bug, not something EURC introduced - fixed alongside
    EURC since both touch the same code path."""

    def test_trusted_tokens_does_not_crash_with_arc_enabled(self):
        from app.routers.tokens import trusted_tokens
        result = trusted_tokens()  # must not raise
        chains = {c["chain"]: c["tokens"] for c in result["chains"]}
        self.assertIn("arc", chains)

    def test_arc_chain_lists_native_usdc_and_eurc(self):
        from app.routers.tokens import trusted_tokens
        result = trusted_tokens()
        arc_tokens = next(c["tokens"] for c in result["chains"] if c["chain"] == "arc")
        symbols = {t["symbol"] for t in arc_tokens}
        self.assertEqual(symbols, {"USDC", "EURC"})
        native_entry = next(t for t in arc_tokens if t["symbol"] == "USDC")
        self.assertTrue(native_entry["native"])
        eurc_entry = next(t for t in arc_tokens if t["symbol"] == "EURC")
        self.assertFalse(eurc_entry["native"])
        self.assertEqual(eurc_entry["address"], assets.EURC_ADDRESSES["arc"])

    def test_ethereum_and_base_gain_eurc_arbitrum_does_not(self):
        from app.routers.tokens import trusted_tokens
        result = trusted_tokens()
        chains = {c["chain"]: {t["symbol"] for t in c["tokens"]} for c in result["chains"]}
        self.assertIn("EURC", chains["ethereum"])
        self.assertIn("EURC", chains["base"])
        self.assertNotIn("EURC", chains["arbitrum"])


class BalanceFetchTests(unittest.TestCase):
    """get_erc20_balances now checks a per-network list of trusted
    contracts (USDC always, EURC where live) rather than one hardcoded
    USDC address - covers both the Alchemy path (ethereum/base) and the
    direct-RPC fallback path (arc)."""

    def test_direct_rpc_path_checks_both_usdc_and_eurc_on_arc(self):
        with patch("app.chains.evm.get_erc20_balance", return_value=5.0) as direct_call:
            result = tokens.get_erc20_balances("0x" + "22" * 20, "arc")
        self.assertEqual(direct_call.call_count, 2)
        self.assertCountEqual(result, [
            {"symbol": "USDC", "name": "USD Coin", "balance": 5.0, "network": "arc"},
            {"symbol": "EURC", "name": "EURC", "balance": 5.0, "network": "arc"},
        ])

    def test_alchemy_path_requests_both_usdc_and_eurc_on_ethereum(self):
        with patch.dict("os.environ", {"ALCHEMY_API_KEY": "test-key"}), \
             patch("requests.post") as alchemy_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": {"tokenBalances": [
                {"contractAddress": assets.NETWORKS["ethereum"]["usdc"], "tokenBalance": hex(10_000_000)},
                {"contractAddress": assets.EURC_ADDRESSES["ethereum"], "tokenBalance": hex(20_000_000)},
            ]}}
            alchemy_post.return_value = mock_resp
            result = tokens.get_erc20_balances("0x" + "22" * 20, "ethereum")
        requested_contracts = alchemy_post.call_args.kwargs["json"]["params"][1]
        self.assertIn(assets.NETWORKS["ethereum"]["usdc"], requested_contracts)
        self.assertIn(assets.EURC_ADDRESSES["ethereum"], requested_contracts)
        self.assertCountEqual(result, [
            {"symbol": "USDC", "name": "USD Coin", "balance": 10.0, "network": "ethereum"},
            {"symbol": "EURC", "name": "EURC", "balance": 20.0, "network": "ethereum"},
        ])

    def test_arbitrum_never_checks_eurc(self):
        """Regression guard: a network EURC was never deployed to must
        never have its EURC address queried at all."""
        with patch.dict("os.environ", {"ALCHEMY_API_KEY": "test-key"}), \
             patch("requests.post") as alchemy_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"result": {"tokenBalances": []}}
            alchemy_post.return_value = mock_resp
            tokens.get_erc20_balances("0x" + "22" * 20, "arbitrum")
        requested_contracts = alchemy_post.call_args.kwargs["json"]["params"][1]
        self.assertEqual(len(requested_contracts), 1)
        self.assertEqual(requested_contracts[0], assets.NETWORKS["arbitrum"]["usdc"])


class ChatSendResolutionTests(unittest.TestCase):
    """Exercises the real chat "send" parsing (app.routers.chat.
    _detect_intent) rather than re-deriving its logic - the actual gap a
    "plain sends" claim needs to hold up against."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base, Wallet
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()
        self.db.add(Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _detect(self, msg):
        from app.routers.chat import _detect_intent
        return _detect_intent(msg, self.db)

    def test_sending_eurc_on_base_resolves_the_real_contract(self):
        kind, payload = self._detect("send 10 EURC to 0x" + "33" * 20 + " on base")
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["token_address"], assets.EURC_ADDRESSES["base"])
        self.assertEqual(payload["token_decimals"], 6)
        self.assertEqual(payload["network"], "base")

    def test_sending_eurc_on_arbitrum_is_rejected_not_silently_wrong_chain(self):
        kind, payload = self._detect("send 10 EURC to 0x" + "33" * 20 + " on arbitrum")
        self.assertEqual(kind, "send_rejected")

    def test_sending_native_usdc_on_arc_is_now_reachable(self):
        """The real bug found while building this: before this fix, Arc
        was entirely absent from chat.py's native-token resolution, so
        "send USDC on arc" always fell through to "not recognized" even
        though sending it works fine end to end."""
        kind, payload = self._detect("send 10 USDC to 0x" + "33" * 20 + " on arc")
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["network"], "arc")
        self.assertIsNone(payload["token_address"])  # native path, not ERC-20

    def test_bare_send_usdc_with_no_network_still_defaults_to_ethereum(self):
        """Regression guard: teaching chat.py that USDC can be native (on
        Arc) must never change the existing default for a plain "send
        USDC" with no network named."""
        kind, payload = self._detect("send 10 USDC to 0x" + "33" * 20)
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["network"], "ethereum")

    def test_sending_eth_while_hinting_arc_is_unaffected(self):
        """Regression guard: the new elif branch must only ever fire for a
        token that's genuinely native on the hinted network - ETH hinting
        "arc" must behave exactly as before (arc's native is USDC, not
        ETH)."""
        kind, payload = self._detect("send 1 ETH to 0x" + "33" * 20 + " on arc")
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["network"], "ethereum")


if __name__ == "__main__":
    unittest.main()
