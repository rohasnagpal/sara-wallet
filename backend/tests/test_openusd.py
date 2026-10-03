"""Open Standard OUSD registry, balance and send support."""
import unittest
from unittest.mock import MagicMock, patch

from app.core import assets
from app.tools.wallet import tokens


class OpenUsdRegistryTests(unittest.TestCase):
    EXPECTED = {
        "ethereum": "0x9f6F3991D525015a6F8CaF062C83b62fD3AC4436",
        "base": "0xB2000000000000000000002fEb517dFeC7415344",
        "tempo": "0x20c0000000000000000000006a37DA5C996874BE",
    }

    def test_ousd_is_registered_on_its_supported_evm_networks(self):
        self.assertEqual(
            set(assets.stablecoin_networks("OUSD", enabled_only=False)),
            set(self.EXPECTED),
        )
        for network, address in self.EXPECTED.items():
            asset = assets.get_stablecoin("OUSD", network)
            self.assertEqual(asset.address, address)
            self.assertEqual(asset.name, "OpenUSD")
            self.assertEqual(asset.issuer, "Open Standard / Bridge")
            self.assertEqual(asset.decimals, 6)

    def test_ousd_capabilities_are_limited_to_wallet_functions(self):
        for network in ("ethereum", "base"):
            self.assertEqual(
                assets.get_stablecoin("OUSD", network).capabilities,
                {assets.BALANCE, assets.SEND, assets.ACTIVITY},
            )
        self.assertEqual(
            assets.get_stablecoin("OUSD", "tempo").capabilities,
            {assets.BALANCE, assets.SEND},
        )
        self.assertIsNone(
            assets.resolve_stablecoin("OUSD", "ethereum", capability=assets.INVOICE),
        )

    def test_ousd_can_be_disabled_per_network(self):
        with patch.dict("os.environ", {"SARA_OUSD_NETWORKS": "base,tempo"}):
            self.assertFalse(assets.token_enabled("OUSD", "ethereum"))
            self.assertTrue(assets.token_enabled("OUSD", "base"))
            self.assertTrue(assets.token_enabled("OUSD", "tempo"))


class OpenUsdBalanceTests(unittest.TestCase):
    def test_alchemy_balance_query_uses_registered_base_contract(self):
        with patch.dict("os.environ", {"ALCHEMY_API_KEY": "test-key"}), \
             patch("requests.post") as alchemy_post:
            response = MagicMock()
            response.json.return_value = {"result": {"tokenBalances": [{
                "contractAddress": assets.get_stablecoin("OUSD", "base").address,
                "tokenBalance": hex(7_250_000),
            }]}}
            alchemy_post.return_value = response
            result = tokens.get_erc20_balances("0x" + "22" * 20, "base")

        requested = alchemy_post.call_args.kwargs["json"]["params"][1]
        self.assertIn(assets.get_stablecoin("OUSD", "base").address, requested)
        self.assertIn(
            {"symbol": "OUSD", "name": "OpenUSD", "balance": 7.25, "network": "base"},
            result,
        )


class OpenUsdChatSendTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base, Wallet

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(Wallet(
            name="Main", chain="evm", address="0x" + "11" * 20,
            encrypted_key="x",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_send_ousd_on_tempo_resolves_official_contract(self):
        from app.routers.chat import _detect_intent

        kind, payload = _detect_intent(
            "send 10 OUSD to 0x" + "33" * 20 + " on tempo", self.db,
        )
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["network"], "tempo")
        self.assertEqual(
            payload["token_address"], assets.get_stablecoin("OUSD", "tempo").address,
        )
        self.assertEqual(payload["token_decimals"], 6)


if __name__ == "__main__":
    unittest.main()
