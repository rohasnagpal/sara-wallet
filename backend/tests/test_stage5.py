from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Base, BalanceMonitor, RiskReview, RiskScreening, Transaction, Wallet
from app.routers import intelligence, risk, treasury
from app.services import stablecoin_routing, token_factory
from app.tools.contracts import interaction as contracts_interaction
from app.tools.risk import screening as risk_screening

NET = "polygon"


class Stage5TestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

    def tearDown(self):
        self.db.close()


class TokenFactoryTests(Stage5TestCase):
    def test_list_templates_reports_both_templates_with_risks(self):
        templates = token_factory.list_templates()
        ids = {t["template_id"] for t in templates}
        self.assertEqual(ids, {"fixed_supply", "mintable_burnable_capped"})
        fixed = next(t for t in templates if t["template_id"] == "fixed_supply")
        mintable = next(t for t in templates if t["template_id"] == "mintable_burnable_capped")
        self.assertEqual(fixed["centralisation_risks"], [])
        self.assertTrue(mintable["centralisation_risks"])

    def test_mintable_template_requires_cap(self):
        with self.assertRaises(token_factory.TokenFactoryError):
            token_factory._constructor_args(
                "mintable_burnable_capped", name="X", symbol="X", decimals=18,
                initial_supply_raw=1, cap_raw=None, owner_address=self.wallet.address,
            )

    def test_mint_refused_when_wallet_is_not_the_onchain_owner(self):
        deployment = MagicMock(template_id="mintable_burnable_capped", contract_address="0x" + "22" * 20,
                                network=NET, decimals=18, symbol="TST")
        fake_contract = MagicMock()
        fake_contract.functions.owner.return_value.call.return_value = "0x" + "99" * 20  # not our wallet
        with patch.object(token_factory, "_live_contract", return_value=(MagicMock(), fake_contract)):
            with self.assertRaises(token_factory.TokenFactoryError):
                token_factory.mint_token(
                    self.db, deployment=deployment, wallet=self.wallet, private_key="fake",
                    to_address="0x" + "33" * 20, amount_raw=1,
                )

    def test_burn_from_someone_else_requires_sufficient_allowance(self):
        deployment = MagicMock(template_id="mintable_burnable_capped", contract_address="0x" + "22" * 20,
                                network=NET, decimals=18, symbol="TST")
        fake_contract = MagicMock()
        fake_contract.functions.allowance.return_value.call.return_value = 5
        fake_w3 = MagicMock()
        fake_w3.eth.account.from_key.return_value = MagicMock(address=self.wallet.address)
        with patch.object(token_factory, "_live_contract", return_value=(fake_w3, fake_contract)):
            with self.assertRaises(token_factory.TokenFactoryError):
                token_factory.burn_token(
                    self.db, deployment=deployment, wallet=self.wallet, private_key="fake",
                    amount_raw=10, from_address="0x" + "44" * 20,
                )


class TreasuryTests(Stage5TestCase):
    def test_overview_flags_concentration_and_low_balance(self):
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

        self.assertEqual(len(result["concentration_flags"]), 4)  # 2 assets + 2 chains, all at 50%
        self.assertEqual(len(result["low_balance_flags"]), 1)
        self.assertTrue(any(p["type"] == "diversify" for p in result["proposals"]))
        self.assertTrue(any(p["type"] == "top_up" for p in result["proposals"]))
        self.assertIn("informational only", result["note"])

    def test_overview_with_no_flags_produces_no_proposals(self):
        fake_portfolio = {"total_usd": 100.0, "assets": [{"wallet": "Treasury", "chain": "polygon", "symbol": "USDC", "usd_value": 10.0}], "by_chain": {"polygon": 10.0}}
        with patch("app.routers.portfolio.get_portfolio", return_value=fake_portfolio):
            result = treasury.treasury_overview(self.db)
        self.assertEqual(result["proposals"], [])


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

    def test_unresolvable_token_returns_empty_with_warning(self):
        with patch("app.tools.trading.lifi.resolve_token", return_value=None):
            result = stablecoin_routing.compare_routes(
                from_network="polygon", to_network="polygon", from_token="NOPE", to_token="USDC",
                amount="1", from_address="0x" + "11" * 20,
            )
        self.assertEqual(result["routes"], [])
        self.assertTrue(result["warnings"])


class WalletIntelligenceTests(Stage5TestCase):
    def make_tx(self, amount_usd, counterparty, category=None, when=None):
        tx = Transaction(
            wallet_id=self.wallet.id, chain="evm", network=NET, token="USDC", direction="outgoing",
            status="confirmed", amount=1.0, amount_raw="1000000", decimals=6,
            fiat_usd_value=str(amount_usd), counterparty=counterparty, category=category,
            timestamp=when or datetime(2026, 1, 1),
        )
        self.db.add(tx)
        self.db.commit()
        return tx

    def test_top_payees_ranked_by_total_with_source_ids(self):
        self.make_tx("100", "0xvendor1")
        self.make_tx("50", "0xvendor1")
        self.make_tx("30", "0xvendor2")
        result = intelligence.top_payees(None, None, None, None, 10, self.db)
        self.assertEqual(result["payees"][0]["payee"], "0xvendor1")
        self.assertEqual(Decimal(result["payees"][0]["total_usd"]), Decimal("150"))
        self.assertEqual(len(result["payees"][0]["transaction_ids"]), 2)

    def test_spend_by_category_groups_and_sums(self):
        self.make_tx("20", "0xa", category="payroll")
        self.make_tx("5", "0xb", category="payroll")
        self.make_tx("10", "0xc", category="expense")
        result = intelligence.spend_by_category(None, None, None, None, self.db)
        by_cat = {c["category"]: c["total_usd"] for c in result["categories"]}
        self.assertEqual(Decimal(by_cat["payroll"]), Decimal("25"))
        self.assertEqual(Decimal(by_cat["expense"]), Decimal("10"))

    def test_recurring_counterparties_requires_minimum_occurrences(self):
        for _ in range(3):
            self.make_tx("1", "0xrepeat")
        self.make_tx("1", "0xonce")
        result = intelligence.recurring_counterparties(None, None, None, None, 3, self.db)
        payees = {r["payee"] for r in result["recurring_counterparties"]}
        self.assertEqual(payees, {"0xrepeat"})

    def test_unusual_activity_flags_outliers_not_normal_spend(self):
        for _ in range(5):
            self.make_tx("10", "0xnormal")
        self.make_tx("500", "0xoutlier")
        result = intelligence.unusual_activity(None, None, None, None, 2.0, self.db)
        flagged = {f["counterparty"] for f in result["unusual_transactions"]}
        self.assertIn("0xoutlier", flagged)
        self.assertNotIn("0xnormal", flagged)


class RiskScreeningTests(Stage5TestCase):
    def test_unconfigured_provider_returns_unavailable_not_fabricated_clear(self):
        result = risk_screening.screen_address(self.db, "0x" + "aa" * 20, NET)
        self.assertEqual(result.result, "unavailable")
        self.assertEqual(result.provider, "unconfigured")
        self.assertEqual(self.db.query(RiskScreening).count(), 1)

    def test_mandatory_enforcement_is_noop_when_not_mandatory(self):
        self.assertFalse(settings.RISK_SCREENING_MANDATORY)
        risk_screening.enforce_mandatory_screening(self.db, "0x" + "aa" * 20, NET)  # must not raise

    def test_mandatory_enforcement_fails_closed_when_unavailable(self):
        with patch.object(settings, "RISK_SCREENING_MANDATORY", True):
            with self.assertRaises(ValueError):
                risk_screening.enforce_mandatory_screening(self.db, "0x" + "aa" * 20, NET)

    def test_cached_result_is_reused_within_ttl(self):
        with patch.object(settings, "RISK_SCREENING_PROVIDER", "testprovider"), \
             patch.object(settings, "RISK_SCREENING_API_KEY", "key"), \
             patch.object(risk_screening, "_call_provider", return_value=("clear", [])) as mocked:
            risk_screening.screen_address(self.db, "0x" + "aa" * 20, NET)
            risk_screening.screen_address(self.db, "0x" + "aa" * 20, NET)
        mocked.assert_called_once()  # second call served from cache

    def test_manual_review_is_recorded_and_audited(self):
        row = risk.create_review(
            risk.ReviewBody(address="0x" + "aa" * 20, network=NET, decision="approved",
                             actor="owner", reason="known counterparty, verified off-chain"),
            self.db,
        )
        self.assertIn("id", row)
        self.assertEqual(self.db.query(RiskReview).count(), 1)


class ContractInteractionTests(unittest.TestCase):
    def test_read_method_not_on_allowlist_is_refused(self):
        with self.assertRaises(contracts_interaction.ContractAssistantError):
            contracts_interaction.read_contract("0x" + "11" * 20, NET, "selfdestruct", [])

    def test_proxy_contract_is_refused(self):
        with self.assertRaises(contracts_interaction.ContractAssistantError):
            contracts_interaction._refuse_if_proxy({"address": "0x" + "11" * 20, "is_proxy": True})
        contracts_interaction._refuse_if_proxy({"address": "0x" + "11" * 20, "is_proxy": False})  # does not raise

    def test_unverified_contract_is_refused(self):
        with patch("os.getenv", return_value="fake-key"), \
             patch("requests.get") as mocked_get:
            mocked_get.return_value.json.return_value = {"result": [{"ABI": "Contract source code not verified"}]}
            mocked_get.return_value.raise_for_status = lambda: None
            with self.assertRaises(contracts_interaction.ContractAssistantError):
                contracts_interaction.fetch_verified_contract("0x" + "11" * 20, NET)

    def test_unlimited_approval_is_refused(self):
        fake_contract_info = {"address": "0x" + "11" * 20, "is_proxy": False, "abi": [], "contract_name": "X"}
        with patch.object(contracts_interaction, "fetch_verified_contract", return_value=fake_contract_info):
            with self.assertRaises(contracts_interaction.ContractAssistantError):
                contracts_interaction.prepare_call(
                    "0x" + "11" * 20, NET, "0x" + "22" * 20, "approve",
                    ["0x" + "33" * 20, 2 ** 256 - 1], 0,
                )


if __name__ == "__main__":
    unittest.main()
