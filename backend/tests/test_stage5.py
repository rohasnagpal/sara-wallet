import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Base, BalanceMonitor, RiskScreening, Wallet
from app.routers import risk, treasury
from app.services import stablecoin_routing
from app.tools.risk import screening as risk_screening

NET = "polygon"


class Stage5TestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

    def tearDown(self):
        self.db.close()


class TreasuryTests(Stage5TestCase):
    def test_overview_returns_balances_and_low_balance_flags(self):
        fake_portfolio = {
            "total_usd": 1000.0,
            "assets": [
                {"wallet": "Treasury", "chain": "polygon", "symbol": "USDC", "usd_value": 500.0},
                {"wallet": "Treasury", "chain": "ethereum", "symbol": "ETH", "usd_value": 500.0},
            ],
            "by_chain": {"polygon": 500.0, "ethereum": 500.0},
        }
        monitor = BalanceMonitor(wallet_id=self.wallet.id, network="polygon", token="POL",
                                  threshold_raw="1000000000000000000", triggered=True)
        self.db.add(monitor)
        self.db.commit()

        with patch("app.routers.portfolio.get_portfolio", return_value=fake_portfolio):
            result = treasury.treasury_overview(self.db)

        self.assertEqual(result["assets"], fake_portfolio["assets"])
        self.assertEqual(result["by_chain"], fake_portfolio["by_chain"])
        self.assertEqual(len(result["low_balance_flags"]), 1)
        self.assertNotIn("concentration_flags", result)
        self.assertNotIn("proposals", result)

    def test_overview_with_no_triggered_monitors_has_no_low_balance_flags(self):
        fake_portfolio = {"total_usd": 100.0, "assets": [{"wallet": "Treasury", "chain": "polygon", "symbol": "USDC", "usd_value": 10.0}], "by_chain": {"polygon": 10.0}}
        with patch("app.routers.portfolio.get_portfolio", return_value=fake_portfolio):
            result = treasury.treasury_overview(self.db)
        self.assertEqual(result["low_balance_flags"], [])


class StablecoinRoutingTests(unittest.TestCase):
    def test_compare_routes_ranks_by_delivered_amount_and_flags_recommendation(self):
        with patch("app.tools.market.paraswap.resolve_token", return_value=("0xusdc", 6)), \
             patch("app.tools.trading.lifi.resolve_token", return_value=("0xusdc", 6)), \
             patch("app.tools.market.paraswap.get_quote", return_value={"priceRoute": {"destAmount": "990000", "gasCostUSD": "0.5"}}), \
             patch("app.tools.trading.lifi.get_quote", return_value={"estimate": {
                 "toAmount": "995000", "toAmountMin": "990000", "executionDuration": 30,
                 "feeCosts": [{"amountUSD": "1.0"}], "gasCosts": [{"amountUSD": "0.2"}],
             }}):
            result = stablecoin_routing.compare_routes(
                from_network="polygon", to_network="polygon", from_token="USDC", to_token="USDC",
                amount="1", from_address="0x" + "11" * 20,
            )
        self.assertEqual(len(result["routes"]), 2)
        self.assertTrue(result["routes"][0]["recommended"])
        self.assertEqual(result["routes"][0]["provider"], "paraswap")  # higher net after fees and gas
        self.assertTrue(result["recommendation_reasons"])

    def _lifi_quotes(self):
        cheap = {"tool": "polymerStandard", "toolDetails": {"name": "Polymer (Standard)"}, "estimate": {
            "toAmount": "99750000", "executionDuration": 1080,
            "feeCosts": [{"amountUSD": "0.2499"}], "gasCosts": [{"amountUSD": "0.0212"}]}}
        fast = {"tool": "across", "toolDetails": {"name": "Across"}, "estimate": {
            "toAmount": "99731500", "executionDuration": 1,
            "feeCosts": [{"amountUSD": "0.2684"}], "gasCosts": [{"amountUSD": "0.0167"}]}}
        return cheap, fast

    def _compare_bridge(self, quote_for_order):
        with patch("app.tools.trading.lifi.resolve_token", return_value=("0xusdc", 6)), \
             patch("app.tools.trading.lifi.get_quote", side_effect=lambda *a, order=None, **k: quote_for_order[order]):
            return stablecoin_routing.compare_routes(
                from_network="polygon", to_network="arbitrum", from_token="USDC", to_token="USDC",
                amount="100", from_address="0x" + "11" * 20,
            )

    def test_bridge_shows_both_the_cheapest_and_the_fastest_route_when_they_differ(self):
        cheap, fast = self._lifi_quotes()
        result = self._compare_bridge({"CHEAPEST": cheap, "FASTEST": fast})
        self.assertEqual([r["tool"] for r in result["routes"]], ["Polymer (Standard)", "Across"])
        self.assertTrue(result["routes"][0]["recommended"])           # more USDC in hand
        self.assertEqual(result["routes"][1]["estimated_seconds"], 1)  # but the other arrives in seconds

    def test_identical_cheapest_and_fastest_routes_are_shown_once(self):
        cheap, _ = self._lifi_quotes()
        result = self._compare_bridge({"CHEAPEST": cheap, "FASTEST": cheap})
        self.assertEqual(len(result["routes"]), 1)

    def test_routes_endpoint_needs_no_wallet_and_does_not_share_one(self):
        from app.routers import treasury
        with patch("app.services.stablecoin_routing.compare_routes", return_value={"routes": []}) as compare:
            treasury.treasury_routes("polygon", "arbitrum", "USDC", "USDC", "100")
        self.assertEqual(compare.call_args.kwargs["from_address"], treasury._QUOTE_ONLY_ADDRESS)

    def test_unsupported_token_says_what_is_supported(self):
        with patch("app.tools.trading.lifi.resolve_token", side_effect=lambda sym, net: None if sym == "ETH" else ("0xusdc", 6)):
            result = stablecoin_routing.compare_routes(
                from_network="polygon", to_network="polygon", from_token="USDC", to_token="ETH",
                amount="100", from_address="0x" + "11" * 20,
            )
        self.assertEqual(result["routes"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("ETH isn't available on Polygon PoS", result["warnings"][0])
        self.assertIn("POL", result["warnings"][0])
        self.assertIn("USDC", result["warnings"][0])

    def test_unresolvable_token_returns_empty_with_warning(self):
        with patch("app.tools.trading.lifi.resolve_token", return_value=None):
            result = stablecoin_routing.compare_routes(
                from_network="polygon", to_network="polygon", from_token="NOPE", to_token="USDC",
                amount="1", from_address="0x" + "11" * 20,
            )
        self.assertEqual(result["routes"], [])
        self.assertTrue(result["warnings"])


class RiskScreeningTests(Stage5TestCase):
    ADDR = "0x" + "aa" * 20

    def test_default_check_uses_the_free_sanctions_oracle(self):
        with patch.object(risk_screening, "_check_sanctions_oracle", return_value="clear") as oracle:
            result = risk_screening.screen_address(self.db, self.ADDR, NET)
        oracle.assert_called_once()
        self.assertEqual((result.result, result.provider), ("clear", risk_screening.BUILTIN_PROVIDER))
        self.assertIsNotNone(result.expires_at)

    def test_a_sanctioned_address_is_flagged_with_evidence(self):
        with patch.object(risk_screening, "_check_sanctions_oracle", return_value="flagged"):
            result = risk_screening.screen_address(self.db, self.ADDR, NET)
        self.assertEqual(result.result, "flagged")
        self.assertTrue(result.evidence)

    def test_when_the_oracle_cannot_be_reached_the_result_is_unavailable_never_clear(self):
        with patch.object(risk_screening, "_check_sanctions_oracle", side_effect=ConnectionError("network down")):
            result = risk_screening.screen_address(self.db, self.ADDR, NET)
        self.assertEqual(result.result, "unavailable")
        self.assertIn("network down", result.reason)
        self.assertEqual(self.db.query(RiskScreening).count(), 1)

    def test_oracle_check_falls_back_to_another_network_when_one_has_no_contract(self):
        from web3 import Web3
        calls = []

        def fake_web3(net):
            calls.append(net)
            w3 = MagicMock()
            if net == "base":  # the oracle isn't deployed on Base
                w3.eth.contract.return_value.functions.isSanctioned.return_value.call.side_effect = Exception("no contract")
            else:
                w3.eth.contract.return_value.functions.isSanctioned.return_value.call.return_value = True
            return w3

        with patch("app.chains.evm.get_web3", side_effect=fake_web3):
            self.assertEqual(risk_screening._check_sanctions_oracle(Web3.to_checksum_address(self.ADDR), "base"), "flagged")
        self.assertEqual(calls, ["base", "ethereum"])

    def test_a_configured_provider_takes_priority_over_the_built_in_check(self):
        with patch.object(settings, "RISK_SCREENING_PROVIDER", "vendor"), \
             patch.object(settings, "RISK_SCREENING_API_KEY", "key"), \
             patch.object(risk_screening, "_call_provider", return_value=("clear", [])), \
             patch.object(risk_screening, "_check_sanctions_oracle") as oracle:
            result = risk_screening.screen_address(self.db, self.ADDR, NET)
        oracle.assert_not_called()
        self.assertEqual(result.provider, "vendor")

    def test_mandatory_enforcement_is_noop_when_not_mandatory(self):
        self.assertFalse(settings.RISK_SCREENING_MANDATORY)
        risk_screening.enforce_mandatory_screening(self.db, self.ADDR, NET)  # must not raise

    def test_mandatory_enforcement_fails_closed_when_unavailable(self):
        with patch.object(settings, "RISK_SCREENING_MANDATORY", True), \
             patch.object(risk_screening, "_check_sanctions_oracle", side_effect=ConnectionError("down")):
            with self.assertRaises(ValueError):
                risk_screening.enforce_mandatory_screening(self.db, self.ADDR, NET)

    def test_mandatory_enforcement_blocks_a_sanctioned_address_and_allows_a_clear_one(self):
        with patch.object(settings, "RISK_SCREENING_MANDATORY", True):
            with patch.object(risk_screening, "_check_sanctions_oracle", return_value="flagged"):
                with self.assertRaises(ValueError):
                    risk_screening.enforce_mandatory_screening(self.db, self.ADDR, NET)
            with patch.object(risk_screening, "_check_sanctions_oracle", return_value="clear"):
                risk_screening.enforce_mandatory_screening(self.db, "0x" + "bb" * 20, NET)  # must not raise

    def test_cached_result_is_reused_within_ttl(self):
        with patch.object(settings, "RISK_SCREENING_PROVIDER", "testprovider"), \
             patch.object(settings, "RISK_SCREENING_API_KEY", "key"), \
             patch.object(risk_screening, "_call_provider", return_value=("clear", [])) as mocked:
            risk_screening.screen_address(self.db, "0x" + "aa" * 20, NET)
            risk_screening.screen_address(self.db, "0x" + "aa" * 20, NET)
        mocked.assert_called_once()  # second call served from cache


if __name__ == "__main__":
    unittest.main()
