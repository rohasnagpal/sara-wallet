"""USDT registry, balance and plain-send support.

USDT is intentionally not granted invoice, swap, bridge or x402
capabilities. Those integrations require separate protocol-specific support.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.core import assets
from app.tools.wallet import tokens


class UsdtRegistryTests(unittest.TestCase):
    EXPECTED = {
        "ethereum": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "arbitrum": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "optimism": "0x01bFF41798a0BcF287b996046Ca68b395DbC1071",
        "polygon": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "tempo": "0x20C00000000000000000000014f22CA97301EB73",
    }

    def test_usdt_is_registered_on_exactly_the_verified_supported_networks(self):
        self.assertEqual(
            set(assets.stablecoin_networks("USDT", enabled_only=False)),
            set(self.EXPECTED),
        )
        for network, address in self.EXPECTED.items():
            asset = assets.get_stablecoin("USDT", network)
            self.assertEqual(asset.address, address)
            self.assertEqual(asset.decimals, 6)

    def test_usdt_is_not_registered_on_base_or_arc(self):
        for network in ("base", "arc"):
            self.assertIsNone(assets.get_stablecoin("USDT", network))
            self.assertFalse(assets.token_enabled("USDT", network))

    def test_usdt_support_is_limited_to_wallet_capabilities(self):
        for network in set(self.EXPECTED) - {"tempo"}:
            asset = assets.get_stablecoin("USDT", network)
            self.assertEqual(asset.capabilities, {
                assets.BALANCE, assets.SEND, assets.ACTIVITY,
            })
        self.assertEqual(
            assets.get_stablecoin("USDT", "tempo").capabilities,
            {assets.BALANCE, assets.SEND},
        )

    def test_usdt_can_be_disabled_per_network(self):
        with patch.dict("os.environ", {"SARA_USDT_NETWORKS": "ethereum,polygon"}):
            self.assertTrue(assets.token_enabled("USDT", "ethereum"))
            self.assertTrue(assets.token_enabled("USDT", "polygon"))
            self.assertFalse(assets.token_enabled("USDT", "arbitrum"))
            self.assertFalse(assets.token_enabled("USDT", "optimism"))

    def test_usdt_is_sendable_but_not_invoice_enabled(self):
        self.assertIn("USDT", assets.sendable_symbols("ethereum"))
        self.assertIsNotNone(assets.resolve_stablecoin("USDT", "ethereum", capability=assets.SEND))
        self.assertIsNone(assets.resolve_stablecoin("USDT", "ethereum", capability=assets.INVOICE))


class UsdtBalanceTests(unittest.TestCase):
    def test_alchemy_balance_query_uses_the_registered_usdt_contract(self):
        with patch.dict("os.environ", {"ALCHEMY_API_KEY": "test-key"}), \
             patch("requests.post") as alchemy_post:
            response = MagicMock()
            response.json.return_value = {"result": {"tokenBalances": [
                {
                    "contractAddress": assets.get_stablecoin("USDT", "arbitrum").address,
                    "tokenBalance": hex(12_500_000),
                },
            ]}}
            alchemy_post.return_value = response
            result = tokens.get_erc20_balances("0x" + "22" * 20, "arbitrum")

        requested = alchemy_post.call_args.kwargs["json"]["params"][1]
        self.assertIn(assets.get_stablecoin("USDT", "arbitrum").address, requested)
        self.assertIn(
            {"symbol": "USDT", "name": "Tether USD", "balance": 12.5, "network": "arbitrum"},
            result,
        )


class UsdtChatSendTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base, Wallet

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.db.add(Wallet(
            name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_send_usdt_resolves_the_verified_polygon_contract(self):
        from app.routers.chat import _detect_intent

        kind, payload = _detect_intent(
            "send 10 USDT to 0x" + "33" * 20 + " on polygon", self.db,
        )
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["network"], "polygon")
        self.assertEqual(
            payload["token_address"], assets.get_stablecoin("USDT", "polygon").address,
        )
        self.assertEqual(payload["token_decimals"], 6)

    def test_send_usdt_on_base_is_rejected(self):
        from app.routers.chat import _detect_intent

        kind, _ = _detect_intent(
            "send 10 USDT to 0x" + "33" * 20 + " on base", self.db,
        )
        self.assertEqual(kind, "send_rejected")


if __name__ == "__main__":
    unittest.main()
