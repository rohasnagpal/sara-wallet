"""Tempo mainnet support and stablecoin-gas invariants."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.chains import evm
from app.core import assets


class TempoRegistryTests(unittest.TestCase):
    def test_tempo_network_metadata_matches_mainnet(self):
        self.assertEqual(assets.NETWORKS["tempo"]["chain_id"], 4217)
        self.assertIsNone(assets.NETWORKS["tempo"]["native"])
        self.assertTrue(assets.NETWORKS["tempo"]["stablecoin_gas"])
        self.assertEqual(evm._CHAIN_IDS["tempo"], 4217)
        self.assertEqual(evm._RPC["tempo"], "https://rpc.tempo.xyz")

    def test_tempo_uses_verified_live_token_variants(self):
        usdc = assets.get_stablecoin("USDC", "tempo")
        usdt = assets.get_stablecoin("USDT", "tempo")
        self.assertEqual(usdc.address, "0x20c000000000000000000000b9537d11c60e8b50")
        self.assertEqual(usdc.name, "USD Coin (USDC.e)")
        self.assertEqual(usdt.address, "0x20C00000000000000000000014f22CA97301EB73")
        self.assertEqual(usdt.name, "Tether USD (USDT0)")
        self.assertEqual(usdc.capabilities, {assets.BALANCE, assets.SEND})
        self.assertEqual(usdt.capabilities, {assets.BALANCE, assets.SEND})

    def test_tempo_has_no_fake_native_asset(self):
        self.assertEqual(assets.sendable_symbols("tempo"), ["USDC", "USDT", "OUSD"])
        with self.assertRaisesRegex(ValueError, "no native asset"):
            evm.get_balance("0x" + "11" * 20, "tempo")
        with self.assertRaisesRegex(ValueError, "no native asset"):
            evm.prepare_native_transfer_raw("0x" + "11" * 32, "0x" + "22" * 20, 1, "tempo")

    def test_preferences_mark_both_tempo_tokens_as_fee_capable(self):
        payload = assets.serialize_preferences()
        tempo = next(network for network in payload["networks"] if network["id"] == "tempo")
        self.assertIsNone(tempo["native"])
        self.assertEqual(
            {token["symbol"] for token in tempo["stablecoins"]},
            {"USDC", "USDT", "OUSD"},
        )
        self.assertTrue(all(token["can_pay_gas"] for token in tempo["stablecoins"]))
        self.assertTrue(all(not token["required_for_gas"] for token in tempo["stablecoins"]))


class TempoBalanceAndFeeTests(unittest.TestCase):
    WALLET = "0x" + "11" * 20
    RECIPIENT = "0x" + "22" * 20

    @staticmethod
    def _web3():
        w3 = MagicMock()
        w3.eth.gas_price = 1_000_000_000
        w3.eth.estimate_gas.return_value = 50_000
        return w3

    def test_transfer_fee_is_reserved_from_the_same_stablecoin(self):
        usdt = assets.get_stablecoin("USDT", "tempo")
        # 50k estimate × 1.2 gas margin × 1e9 attodollars = 60 token base units.
        with patch("app.chains.evm.get_web3", return_value=self._web3()), \
             patch("app.chains.evm._get_erc20_balance_raw", return_value=1_000_060):
            preview = evm.get_erc20_transfer_preview_raw(
                usdt.address, 6, self.WALLET, 1_000_000, self.RECIPIENT, "tempo",
            )
        self.assertTrue(preview["has_token_funds"])
        self.assertTrue(preview["has_gas_funds"])
        self.assertEqual(preview["native_unit"], "USDT")
        self.assertEqual(preview["gas_fee"], 0.00006)
        self.assertEqual(preview["native_balance"], 1.00006)

    def test_transfer_is_rejected_when_amount_leaves_nothing_for_gas(self):
        usdc = assets.get_stablecoin("USDC", "tempo")
        with patch("app.chains.evm.get_web3", return_value=self._web3()), \
             patch("app.chains.evm._get_erc20_balance_raw", return_value=1_000_059):
            preview = evm.get_erc20_transfer_preview_raw(
                usdc.address, 6, self.WALLET, 1_000_000, self.RECIPIENT, "tempo",
            )
        self.assertTrue(preview["has_token_funds"])
        self.assertFalse(preview["has_gas_funds"])

    def test_prepared_transfer_is_signed_for_tempo_chain_id(self):
        usdt = assets.get_stablecoin("USDT", "tempo")
        w3 = MagicMock()
        account = SimpleNamespace(address=self.WALLET)
        signed = SimpleNamespace(
            raw_transaction=bytes.fromhex("01"),
            hash=bytes.fromhex("22" * 32),
        )
        w3.eth.account.from_key.return_value = account
        w3.eth.account.sign_transaction.return_value = signed
        w3.eth.get_transaction_count.return_value = 7
        preview = {
            "has_token_funds": True, "has_gas_funds": True,
            "native_unit": "USDT", "gas_limit": 60_000, "gas_price": 1_000_000_000,
        }
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm.get_erc20_transfer_preview_raw", return_value=preview):
            evm.prepare_erc20_transfer_raw(
                "0x" + "11" * 32, usdt.address, 6, self.RECIPIENT,
                1_000_000, "tempo",
            )
        transaction = w3.eth.account.sign_transaction.call_args.args[0]
        self.assertEqual(transaction["chainId"], 4217)
        self.assertEqual(transaction["to"].lower(), usdt.address.lower())


class TempoEndpointTests(unittest.TestCase):
    def test_trusted_tokens_has_no_native_placeholder(self):
        from app.routers.tokens import trusted_tokens

        tempo = next(chain for chain in trusted_tokens()["chains"] if chain["chain"] == "tempo")
        self.assertEqual(
            {token["symbol"] for token in tempo["tokens"]},
            {"USDC", "USDT", "OUSD"},
        )
        self.assertTrue(all(not token["native"] for token in tempo["tokens"]))

    def test_settings_require_one_fee_paying_stablecoin(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base
        from app.routers.settings import AssetSettingsBody, save_asset_settings

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        body = AssetSettingsBody(
            enabled_networks=["tempo"],
            stablecoin_networks={"USDC": [], "EURC": [], "USDT": [], "OUSD": []},
        )
        try:
            with patch.dict("os.environ", {}, clear=True), self.assertRaises(HTTPException) as raised:
                save_asset_settings(body, db)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("fee-paying stablecoin", raised.exception.detail)
        finally:
            db.close()


class TempoBatchTests(unittest.TestCase):
    WALLET = "0x" + "11" * 20
    RECIPIENT = "0x" + "22" * 20

    def test_batch_balance_must_cover_payments_and_stablecoin_gas(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.models import Base, PaymentBatch, PaymentBatchItem, Wallet
        from app.services import batch_engine

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        wallet = Wallet(
            name="Treasury", chain="evm", address=self.WALLET,
            encrypted_key="x",
        )
        db.add(wallet)
        db.flush()
        batch = PaymentBatch(
            kind="payment", status="draft", wallet_id=wallet.id,
            network="tempo", token="USDT", created_by="owner",
        )
        db.add(batch)
        db.flush()
        for index, recipient in enumerate((self.RECIPIENT, "0x" + "33" * 20)):
            db.add(PaymentBatchItem(
                batch_id=batch.id, row_index=index, recipient_address=recipient,
                amount_raw="1000000", decimals=6,
            ))
        db.flush()

        preview = {
            "gas_fee": 0.00006, "native_balance": 2.0001,
            "native_unit": "USDT", "has_token_funds": True,
            "has_gas_funds": True,
        }
        try:
            with patch("app.chains.evm._get_erc20_balance_raw", return_value=2_000_100), \
                 patch("app.chains.evm.get_erc20_transfer_preview_raw", return_value=preview):
                result = batch_engine.validate_batch(db, batch)
            self.assertFalse(result["ok"])
            self.assertTrue(any("payments and gas" in error for error in result["batch_errors"]))
        finally:
            db.close()


class TempoChatTests(unittest.TestCase):
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

    def test_send_usdt_on_tempo_resolves_usdt0(self):
        from app.routers.chat import _detect_intent

        kind, payload = _detect_intent(
            "send 10 USDT to 0x" + "33" * 20 + " on tempo", self.db,
        )
        self.assertEqual(kind, "send_crypto", payload.get("message"))
        self.assertEqual(payload["network"], "tempo")
        self.assertEqual(
            payload["token_address"], assets.get_stablecoin("USDT", "tempo").address,
        )


if __name__ == "__main__":
    unittest.main()
