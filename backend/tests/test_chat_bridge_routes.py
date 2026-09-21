"""The chat bridge flow lists LI.FI's cheapest and fastest routes when they
differ and lets the user choose; the chosen bridge is pinned on the re-quote
made right before signing so a different one can't be swapped in."""
import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Wallet
from app.routers import chat

ME = "0x" + "11" * 20
USDC = "0x" + "aa" * 20


def body_text(response) -> str:
    async def collect():
        return [chunk async for chunk in response.body_iterator]
    frames = "".join(asyncio.run(collect())).split("\n\n")
    return "".join(json.loads(f[len("data: "):])["token"] for f in frames if f.startswith("data: "))


def quote(tool, name, to_amount, seconds, fee, gas):
    return {"tool": tool, "toolDetails": {"name": name}, "transactionRequest": {"to": "0x" + "cc" * 20},
            "estimate": {"toAmount": str(to_amount), "executionDuration": seconds, "approvalAddress": None,
                         "feeCosts": [{"amountUSD": fee}], "gasCosts": [{"amountUSD": gas}]}}


CHEAP = quote("polymerStandard", "Polymer (Standard)", 99_750_000, 1080, "0.2499", "0.0212")
FAST = quote("across", "AcrossV4", 99_731_500, 1, "0.2684", "0.0167")
ARGS = {"wallet_name": "Main", "from_network": "polygon", "to_network": "arbitrum",
        "from_token": "USDC", "to_token": "USDC", "amount": "100"}


class BridgeRouteChoiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False)()
        self.wallet = Wallet(name="Main", chain="evm", address=ME, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()
        chat._pending.clear()
        self.addCleanup(chat._pending.clear)

    def tearDown(self):
        self.db.close()

    def build(self, by_order):
        with patch.object(chat, "_resolve_wallet", return_value=self.wallet), \
             patch("app.tools.trading.lifi.resolve_token_with_correction", return_value=((USDC, 6), None)), \
             patch("app.tools.trading.lifi.max_total_network_fee_wei", return_value=10 ** 16), \
             patch("app.tools.trading.lifi.get_quote", side_effect=lambda *a, order=None, **k: by_order[order]):
            return chat._build_bridge_pending(dict(ARGS), self.db)

    def say(self, message, session="s1"):
        req = chat.ChatRequest(message=message, session_id=session, history=[])
        return body_text(asyncio.run(chat.chat(req, self.db)))

    def offer(self):
        pending, text = self.build({"CHEAPEST": CHEAP, "FASTEST": FAST})
        chat._pending["s1"] = pending
        return pending, text

    def test_different_cheapest_and_fastest_routes_are_offered_as_a_choice(self):
        pending, text = self.build({"CHEAPEST": CHEAP, "FASTEST": FAST})
        self.assertEqual(pending["type"], "choose_bridge_route")
        self.assertEqual(len(pending["options"]), 2)
        self.assertIn("Polymer (Standard)", text)
        self.assertIn("**2. Across**", text)
        self.assertIn("under a minute", text)
        self.assertIn("Reply **1** or **2**", text)
        self.assertEqual([o["most"] for o in pending["options"]], [True, False])
        self.assertEqual([o["fastest"] for o in pending["options"]], [False, True])

    def test_a_single_distinct_route_goes_straight_to_confirm(self):
        pending, text = self.build({"CHEAPEST": CHEAP, "FASTEST": CHEAP})
        self.assertEqual(pending["type"], "bridge")
        self.assertIn("Type **CONFIRM**", text)
        self.assertEqual(pending["route_tool"], "polymerStandard")

    def test_choosing_a_number_prepares_that_route_for_confirmation(self):
        self.offer()
        reply = self.say("2")
        chosen = chat._pending["s1"]
        self.assertEqual((chosen["type"], chosen["route_tool"], chosen["route_order"]), ("bridge", "across", "FASTEST"))
        self.assertIn("Type **CONFIRM**", reply)
        self.assertIn("Across", reply)

    def test_words_like_fastest_and_cheapest_also_work(self):
        self.offer()
        self.say("fastest")
        self.assertEqual(chat._pending["s1"]["route_tool"], "across")
        self.offer()
        self.say("cheapest")
        self.assertEqual(chat._pending["s1"]["route_tool"], "polymerStandard")

    def test_a_bad_reply_asks_again_and_keeps_the_choice_open(self):
        self.offer()
        for bad in ("banana", "3", "0", "CONFIRM"):
            with self.subTest(reply=bad):
                self.assertIn("Reply with a number from 1 to 2", self.say(bad))
                self.assertEqual(chat._pending["s1"]["type"], "choose_bridge_route")

    def test_cancel_abandons_the_choice(self):
        self.offer()
        self.assertIn("Bridge cancelled", self.say("cancel"))
        self.assertNotIn("s1", chat._pending)

    def test_the_re_quote_before_signing_is_pinned_to_the_chosen_bridge(self):
        self.offer()
        self.say("2")
        pending = chat._pending["s1"]
        web3 = MagicMock()
        web3.eth.account.from_key.return_value.address = ME
        requote = MagicMock(return_value=None)  # stop right after the re-quote
        with patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch("app.chains.evm.get_web3", return_value=web3), \
             patch("app.tools.trading.lifi.get_quote", requote):
            body_text(chat._stream_bridge(pending, self.db, "s1"))
        self.assertEqual(requote.call_args.kwargs["bridges"], ["across"])
        self.assertEqual(requote.call_args.kwargs["order"], "FASTEST")


if __name__ == "__main__":
    unittest.main()
