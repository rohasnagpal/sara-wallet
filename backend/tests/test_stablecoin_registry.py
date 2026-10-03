"""Invariants for the canonical chain-aware stablecoin registry."""
import os
import unittest
from unittest.mock import patch

from app.core import assets


class CanonicalStablecoinRegistryTests(unittest.TestCase):
    def test_network_metadata_contains_no_token_contracts(self):
        for network in assets.NETWORKS.values():
            self.assertNotIn("usdc", network)
            self.assertNotIn("eurc", network)

    def test_registry_keys_match_immutable_entry_identity(self):
        for (network, symbol), asset in assets.STABLECOINS.items():
            self.assertEqual((network, symbol), (asset.network, asset.symbol))
            self.assertEqual(symbol, symbol.upper())
            self.assertGreater(asset.decimals, 0)
            self.assertTrue(asset.address.startswith("0x"))

    def test_registry_has_no_duplicate_contract_on_a_chain(self):
        for network in assets.ALL_NETWORKS:
            addresses = [asset.address.lower() for asset in assets.stablecoins_on(network)]
            self.assertEqual(len(addresses), len(set(addresses)))

    def test_capabilities_are_chain_specific(self):
        self.assertTrue(assets.get_stablecoin("USDC", "base").supports(assets.X402))
        self.assertFalse(assets.get_stablecoin("USDC", "optimism").supports(assets.X402))
        self.assertFalse(assets.get_stablecoin("USDC", "arc").supports(assets.INVOICE))
        self.assertFalse(assets.get_stablecoin("EURC", "base").supports(assets.SWAP))

    def test_all_registered_stablecoins_are_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            for (network, symbol) in assets.STABLECOINS:
                self.assertTrue(assets.token_enabled(symbol, network))

    def test_symbol_settings_are_generic(self):
        with patch.dict(os.environ, {
            "SARA_ENABLED_NETWORKS": "ethereum,base,arc",
            "SARA_USDC_NETWORKS": "ethereum",
            "SARA_EURC_NETWORKS": "base",
        }, clear=True):
            self.assertEqual(assets.configured_stablecoin_networks("USDC"), ("ethereum",))
            self.assertEqual(assets.configured_stablecoin_networks("EURC"), ("base",))
            # Arc's native gas asset cannot be disabled with a token toggle.
            self.assertTrue(assets.token_enabled("USDC", "arc"))

    def test_preferences_are_registry_driven(self):
        payload = assets.serialize_preferences()
        self.assertEqual(payload["stablecoin_symbols"], ["USDC", "EURC", "USDT"])
        ethereum = next(network for network in payload["networks"] if network["id"] == "ethereum")
        self.assertNotIn("usdc_enabled", ethereum)
        self.assertEqual({asset["symbol"] for asset in ethereum["stablecoins"]}, {"USDC", "EURC", "USDT"})
        usdc = next(asset for asset in ethereum["stablecoins"] if asset["symbol"] == "USDC")
        self.assertIn(assets.INVOICE, usdc["capabilities"])

    def test_resolution_requires_the_requested_capability(self):
        self.assertIsNotNone(assets.resolve_stablecoin("USDC", "base", capability=assets.SWAP))
        self.assertIsNone(assets.resolve_stablecoin("EURC", "base", capability=assets.SWAP))
        self.assertIsNone(assets.resolve_stablecoin("USDC", "arc", capability=assets.RECONCILE))

    def test_capabilities_match_current_integration_networks(self):
        from app.tools.lending import aave
        from app.tools.market import paraswap
        from app.tools.payments import x402_client
        from app.tools.trading import cctp, lifi

        self.assertEqual(
            set(assets.stablecoin_networks("USDC", capability=assets.SWAP, enabled_only=False)),
            set(paraswap.CHAIN_IDS),
        )
        self.assertEqual(
            set(assets.stablecoin_networks("USDC", capability=assets.BRIDGE, enabled_only=False)),
            set(lifi.CHAIN_IDS),
        )
        self.assertEqual(
            set(assets.stablecoin_networks("USDC", capability=assets.CCTP, enabled_only=False)),
            set(cctp.SUPPORTED_NETWORKS),
        )
        self.assertEqual(
            set(assets.stablecoin_networks("USDC", capability=assets.AAVE, enabled_only=False)),
            set(aave.SUPPORTED_NETWORKS),
        )
        self.assertEqual(
            set(assets.stablecoin_networks("USDC", capability=assets.X402, enabled_only=False)),
            set(x402_client.SUPPORTED_NETWORKS) - set(x402_client.TESTNET_NETWORKS),
        )


class AssetSettingsTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_settings_accept_a_generic_symbol_to_network_matrix(self):
        from app.routers.settings import AssetSettingsBody, save_asset_settings

        body = AssetSettingsBody(
            enabled_networks=["ethereum", "base", "arc"],
            stablecoin_networks={"USDC": ["ethereum"], "EURC": ["base"], "USDT": ["ethereum"]},
        )
        with patch.dict(os.environ, {}, clear=True):
            payload = save_asset_settings(body, self.db)
            networks = {network["id"]: network for network in payload["networks"]}
            ethereum = {asset["symbol"]: asset for asset in networks["ethereum"]["stablecoins"]}
            base = {asset["symbol"]: asset for asset in networks["base"]["stablecoins"]}
            arc = {asset["symbol"]: asset for asset in networks["arc"]["stablecoins"]}
            self.assertTrue(ethereum["USDC"]["enabled"])
            self.assertFalse(base["USDC"]["enabled"])
            self.assertTrue(base["EURC"]["enabled"])
            self.assertTrue(arc["USDC"]["enabled"])
            self.assertTrue(arc["USDC"]["required_for_gas"])

    def test_legacy_usdc_only_request_shape_is_rejected(self):
        from pydantic import ValidationError
        from app.routers.settings import AssetSettingsBody

        with self.assertRaises(ValidationError):
            AssetSettingsBody(
                enabled_networks=["ethereum"],
                usdc_networks=["ethereum"],
            )


if __name__ == "__main__":
    unittest.main()
