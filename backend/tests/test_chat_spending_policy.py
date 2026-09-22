"""Spending policies must gate ad-hoc chat sends, swaps and bridges - at preview
(so the user is told before CONFIRM) and again at execution - and a denied
swap/bridge must never reach the on-chain approval transaction."""
import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AddressBook, Base, SpendingPolicy, Transaction, Wallet
from app.routers import chat

ME = "0x" + "11" * 20
BOB = "0x" + "22" * 20
USDC = "0x" + "aa" * 20
WETH = "0x" + "bb" * 20


def body_text(response) -> str:
    """Join the token chunks of a server-sent-events chat response into one string."""
    async def collect():
        return [chunk async for chunk in response.body_iterator]
    frames = "".join(asyncio.run(collect())).split("\n\n")
    return "".join(json.loads(f[len("data: "):])["token"] for f in frames if f.startswith("data: "))


class ChatSpendingPolicyTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()
        self.wallet = Wallet(name="Main", chain="evm", address=ME, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def cap(self, **kw):
        kw.setdefault("name", "cap")
        kw.setdefault("active", True)
        self.db.add(SpendingPolicy(**kw))
        self.db.commit()

    # -- shared helper ---------------------------------------------------
    def test_helper_blocks_over_cap_and_allows_otherwise(self):
        self.cap(network="polygon", token="USDC", max_amount_raw="1000000")
        args = dict(wallet_id=self.wallet.id, network="polygon", token="USDC", destination=BOB)
        self.assertIn("caps a single payment", chat._spending_policy_denial(db=self.db, amount_raw=2_000_000, **args))
        self.assertIsNone(chat._spending_policy_denial(db=self.db, amount_raw=500_000, **args))
        self.assertIsNone(chat._spending_policy_denial(
            db=self.db, amount_raw=9_000_000, wallet_id=self.wallet.id, network="base", token="USDC", destination=BOB))

    def test_a_vendor_scoped_cap_blocks_a_plain_chat_send_to_that_vendor(self):
        # chat._spending_policy_denial has no counterparty_id parameter at
        # all — it always calls evaluate() with counterparty_id=None — so a
        # policy scoped to a Directory entry only ever protects that vendor
        # if evaluate() itself resolves the counterparty from the address.
        vendor = AddressBook(nickname="vendor.sara", address=BOB, chain="evm", type="vendor")
        self.db.add(vendor)
        self.cap(counterparty_id=vendor.id, max_amount_raw="1000000")
        denial = chat._spending_policy_denial(
            db=self.db, wallet_id=self.wallet.id, network="polygon", token="USDC",
            destination=BOB, amount_raw=5_000_000,
        )
        self.assertIsNotNone(denial)
        self.assertIn("caps a single payment", denial)

    def test_prior_chat_swap_counts_toward_period_limit(self):
        self.cap(network="polygon", token="USDC", period="day", period_limit_raw="1000000")
        self.db.add(Transaction(
            wallet_id=self.wallet.id, chain="evm", network="polygon", tx_hash="0xprior", from_address=ME,
            to_address=BOB, amount=0.9, amount_raw="900000", decimals=6, token="USDC",
            status="confirmed", direction="outgoing", category="swap"))
        self.db.commit()
        denial = chat._spending_policy_denial(
            self.db, wallet_id=self.wallet.id, network="polygon", token="USDC", destination=ME, amount_raw=200_000)
        self.assertIn("cumulative spend", denial)

    # -- send ------------------------------------------------------------
    def send_pending(self, amount=5):
        return {"type": "send", "wallet_id": self.wallet.id, "wallet_name": "Main", "wallet_chain": "evm",
                "wallet_address": ME, "wallet_encrypted_key": "x", "network": "polygon", "to": BOB,
                "amount": amount, "token": "USDC", "token_address": USDC, "token_decimals": 6}

    def test_send_preview_is_blocked_before_any_balance_lookup(self):
        self.cap(network="polygon", token="USDC", max_amount_raw="1000000")
        with patch("app.chains.evm.get_erc20_transfer_preview") as lookup:
            text = body_text(chat._preview_pending_send(self.send_pending(), self.db, "s1"))
        lookup.assert_not_called()
        self.assertIn("Blocked by your spending policy", text)
        self.assertNotIn("CONFIRM", text)

    def test_send_execution_is_blocked_before_broadcast(self):
        self.cap(network="polygon", token="USDC", max_amount_raw="1000000")
        with patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch.object(chat, "_is_valid_recipient", return_value=True), \
             patch("app.chains.evm.send_erc20_tx") as send:
            text = body_text(chat._stream_send(self.send_pending(), self.db, "s1"))
        send.assert_not_called()
        self.assertIn("caps a single payment", text)

    # -- swap ------------------------------------------------------------
    def swap_pending(self, amount_wei=5_000_000):
        return {"type": "swap", "wallet_id": self.wallet.id, "wallet_encrypted_key": "x", "wallet_address": ME,
                "network": "polygon", "src_addr": USDC, "dst_addr": WETH, "src_dec": 6, "dst_dec": 18,
                "amount_wei": amount_wei, "src_amount": str(amount_wei), "dest_amount": "1",
                "from_token": "USDC", "to_token": "WETH", "amount": "5"}

    def test_swap_preview_is_blocked_before_quoting(self):
        self.cap(network="polygon", token="USDC", max_amount_raw="1000000")
        quote = MagicMock()
        with patch.object(chat, "_resolve_wallet", return_value=self.wallet), \
             patch("app.tools.market.paraswap.resolve_token_with_correction",
                   side_effect=lambda sym, net: (((USDC, 6) if sym == "USDC" else (WETH, 18)), None)), \
             patch("app.tools.market.paraswap.get_quote", quote):
            pending, text = chat._build_swap_pending(
                {"wallet_name": "Main", "network": "polygon", "from_token": "USDC", "to_token": "WETH", "amount": "5"},
                self.db)
        self.assertIsNone(pending)
        self.assertIn("Blocked by your spending policy", text)
        quote.assert_not_called()

    def test_denied_swap_never_sends_the_approval_transaction(self):
        self.cap(network="polygon", token="USDC", max_amount_raw="1000000")
        approve, quote = MagicMock(), MagicMock()
        with patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch("app.tools.market.paraswap.ensure_allowance", approve), \
             patch("app.tools.market.paraswap.get_quote", quote):
            text = body_text(chat._stream_swap(self.swap_pending(), self.db, "s1"))
        approve.assert_not_called()
        quote.assert_not_called()
        self.assertIn("caps a single payment", text)

    # -- bridge ----------------------------------------------------------
    def test_denied_bridge_is_stopped_before_quote_and_approval(self):
        self.cap(network="polygon", token="USDC", max_amount_raw="1000000")
        pending = {"type": "bridge", "wallet_id": self.wallet.id, "wallet_encrypted_key": "x",
                   "from_network": "polygon", "to_network": "base", "src_addr": USDC, "dst_addr": USDC,
                   "src_dec": 6, "dst_dec": 6, "amount_wei": 5_000_000, "from_token": "USDC",
                   "to_token": "USDC", "amount": "5"}
        web3 = MagicMock()
        web3.eth.account.from_key.return_value.address = ME
        quote, approve = MagicMock(), MagicMock()
        with patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch("app.chains.evm.get_web3", return_value=web3), \
             patch("app.tools.trading.lifi.get_quote", quote), \
             patch("app.tools.trading.lifi.ensure_allowance", approve):
            text = body_text(chat._stream_bridge(pending, self.db, "s1"))
        quote.assert_not_called()
        approve.assert_not_called()
        self.assertIn("caps a single payment", text)


if __name__ == "__main__":
    unittest.main()
