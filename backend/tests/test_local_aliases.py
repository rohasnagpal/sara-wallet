import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AddressBook, Base, Wallet
from app.core import assets
from app.tools.names.resolver import resolve_recipient_input
from app.routers.chat import _detect_intent, _format_send_confirmation


ADDRESS = "0x" + "33" * 20


class LocalAliasResolverTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = session()
        self.db.add(Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_raw_evm_address_resolves_directly(self):
        resolved = resolve_recipient_input(self.db, ADDRESS, "polygon")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.source, "address")

    def test_exact_local_handle_resolves_from_directory(self):
        self.db.add(AddressBook(nickname="amit-singh", display_name="Amit Singh", address=ADDRESS, chain="evm"))
        self.db.commit()

        resolved = resolve_recipient_input(self.db, "AMIT-SINGH", "base")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.source, "address_book")
        self.assertEqual(resolved.address, ADDRESS)
        self.assertEqual(resolved.input_label, "AMIT-SINGH")

    def test_unknown_name_is_not_guessed_or_resolved_onchain(self):
        self.assertIsNone(resolve_recipient_input(self.db, "unknown.sara", "ethereum"))
        self.assertIsNone(resolve_recipient_input(self.db, "unknown", "ethereum"))

    def test_send_confirmation_shows_handle_address_and_name(self):
        text = _format_send_confirmation({
            "amount": 10, "token": "USDC", "to_nickname": "rohasnagpal",
            "to_name": "Rohas Nagpal", "to": ADDRESS, "wallet_name": "Main",
        }, "USDC", "Base", "Balance: **20 USDC**\n", "")
        self.assertIn("Send **10 USDC** to `rohasnagpal`", text)
        self.assertIn(f"Address: `{ADDRESS}`", text)
        self.assertIn("Name: Rohas Nagpal", text)

    def test_chat_send_uses_exact_handle_and_carries_display_name(self):
        self.db.add(AddressBook(
            nickname="rohasnagpal", display_name="Rohas Nagpal", address=ADDRESS, chain="evm",
        ))
        self.db.commit()
        tool, args = _detect_intent("send 10 USDC to rohasnagpal", self.db)
        self.assertEqual(tool, "send_crypto")
        self.assertEqual(args["to"], ADDRESS)
        self.assertEqual(args["to_nickname"], "rohasnagpal")
        self.assertEqual(args["to_name"], "Rohas Nagpal")

    def test_bare_eurc_send_to_handle_defaults_to_ethereum(self):
        self.db.add(AddressBook(
            nickname="rohasnagpal", display_name="Rohas Nagpal",
            address=ADDRESS, chain="evm",
        ))
        self.db.commit()

        tool, args = _detect_intent("send 10 EURC to rohasnagpal", self.db)

        self.assertEqual(tool, "send_crypto", args.get("message"))
        self.assertEqual(args["network"], "ethereum")
        self.assertEqual(args["token"], "EURC")
        self.assertEqual(
            args["token_address"],
            assets.get_stablecoin("EURC", "ethereum").address,
        )
        self.assertEqual(args["to"], ADDRESS)
        self.assertEqual(args["to_nickname"], "rohasnagpal")
        self.assertEqual(args["to_name"], "Rohas Nagpal")

        confirmation = _format_send_confirmation(
            args, args["token"], "Ethereum", "", "",
        )
        self.assertIn("Send **10 EURC** to `rohasnagpal`", confirmation)
        self.assertIn(f"Address: `{ADDRESS}`", confirmation)
        self.assertIn("Name: Rohas Nagpal", confirmation)

    def test_every_registered_stablecoin_send_pair_parses_to_exact_contract(self):
        """The documented support matrix and chat send parser stay aligned."""
        self.db.add(AddressBook(
            nickname="rohasnagpal", display_name="Rohas Nagpal",
            address=ADDRESS, chain="evm",
        ))
        self.db.commit()

        for (network, symbol), asset in assets.STABLECOINS.items():
            if not asset.supports(assets.SEND):
                continue
            with self.subTest(network=network, symbol=symbol):
                tool, args = _detect_intent(
                    f"send 10 {symbol} to rohasnagpal on {network}", self.db,
                )
                self.assertEqual(tool, "send_crypto", args.get("message"))
                self.assertEqual(args["network"], network)
                self.assertEqual(args["token"], symbol)
                # Arc USDC is the network's native gas asset, so the send
                # path deliberately uses a native transfer, not its ERC-20
                # compatibility precompile.
                expected_address = None if (network, symbol) == ("arc", "USDC") else asset.address
                self.assertEqual(args["token_address"], expected_address)
                self.assertEqual(args["to"], ADDRESS)


if __name__ == "__main__":
    unittest.main()
