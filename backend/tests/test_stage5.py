import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Base, RiskScreening, Wallet
from app.routers import risk
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
