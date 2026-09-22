"""x402 pays for a resource through two independent request/402 round
trips: probe() (used to check a spending policy or ask for a passphrase)
and pay_and_fetch() (which actually pays). Nothing previously stopped a
resource from answering the second round trip with a different amount or
recipient than the first — still in Sara's trusted USDC contract, so the
asset-only check didn't catch it — letting a policy-approved probe price
turn into a larger, unapproved payment with no passphrase at all."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, SpendingPolicy, Wallet
from app.tools.payments import x402_client

POLYGON_USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
PAY_TO = "0x" + "22" * 20
CAIP2 = "eip155:137"


def requirement(amount, pay_to=PAY_TO, network=CAIP2, asset=POLYGON_USDC):
    return SimpleNamespace(network=network, asset=asset, amount=amount, pay_to=pay_to)


class SelectRequirementToPayTests(unittest.TestCase):
    """The actual security decision, tested directly without needing to
    drive the real x402/httpx transport."""

    def select(self, requirements, **expected):
        return x402_client.select_requirement_to_pay(
            requirements, caip2=CAIP2, trusted_usdc=POLYGON_USDC, network="polygon", **expected,
        )

    def test_matching_amount_and_payee_is_accepted(self):
        req = self.select([requirement("10000")], expected_amount_raw="10000", expected_pay_to=PAY_TO)
        self.assertEqual(req.amount, "10000")

    def test_a_higher_amount_than_approved_is_refused(self):
        # Exactly the reported exploit: a 0.01 USDC probe gets auto-approved,
        # then the resource asks for 0.50 USDC at actual payment time.
        with self.assertRaises(x402_client.X402Error) as ctx:
            self.select([requirement("500000")], expected_amount_raw="10000", expected_pay_to=PAY_TO)
        self.assertIn("price changed", str(ctx.exception))
        self.assertIn("10000", str(ctx.exception))
        self.assertIn("500000", str(ctx.exception))

    def test_a_lower_amount_than_approved_is_also_refused_not_silently_accepted(self):
        # Paying something *other* than what was approved is the issue,
        # not specifically paying more - a bait-and-switch lower price is
        # still not the transaction that was reviewed and approved.
        with self.assertRaises(x402_client.X402Error):
            self.select([requirement("100")], expected_amount_raw="10000", expected_pay_to=PAY_TO)

    def test_a_different_payee_than_approved_is_refused(self):
        other_payee = "0x" + "33" * 20
        with self.assertRaises(x402_client.X402Error) as ctx:
            self.select([requirement("10000", pay_to=other_payee)], expected_amount_raw="10000", expected_pay_to=PAY_TO)
        self.assertIn("recipient changed", str(ctx.exception))

    def test_payee_comparison_is_case_insensitive(self):
        req = self.select([requirement("10000", pay_to=PAY_TO.upper())],
                           expected_amount_raw="10000", expected_pay_to=PAY_TO.lower())
        self.assertEqual(req.pay_to, PAY_TO.upper())

    def test_an_untrusted_asset_is_still_refused_regardless_of_amount(self):
        untrusted = "0x" + "99" * 20
        with self.assertRaises(x402_client.X402Error) as ctx:
            self.select([requirement("10000", asset=untrusted)], expected_amount_raw="10000", expected_pay_to=PAY_TO)
        self.assertIn("trusted USDC", str(ctx.exception))

    def test_omitting_expected_values_keeps_the_old_asset_only_behavior(self):
        # No caller in this codebase does this today, but the parameters are
        # optional - confirm that omitting them doesn't accidentally start
        # requiring a match against None.
        req = self.select([requirement("500000")], expected_amount_raw=None, expected_pay_to=None)
        self.assertEqual(req.amount, "500000")


class FetchEndpointWiringTests(unittest.TestCase):
    """Confirms the /x402/fetch endpoint actually passes what it evaluated
    into pay_and_fetch, rather than just the fix existing in isolation."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()
        self.wallet = Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        # A policy that auto-approves small x402 payments to this payee.
        self.db.add(SpendingPolicy(name="x402-auto", network="polygon", token="USDC",
                                    destination_address=PAY_TO, max_amount_raw="1000000", active=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_probed_amount_and_payee_are_pinned_into_pay_and_fetch(self):
        import tempfile
        from pathlib import Path
        from app.routers import x402

        probed = x402_client.X402Requirement(network="polygon", asset=POLYGON_USDC, amount_raw="10000", pay_to=PAY_TO)
        paid_result = x402_client.X402Result(status_code=200, body_text="ok", content_type="text/plain", paid=True, tx_hash="0xabc")
        body = x402.X402FetchBody(wallet_id=self.wallet.id, network="polygon", url="https://example.com/resource")

        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch.object(x402_client, "probe", AsyncMock(return_value=probed)), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch.object(x402, "FETCHED_CONTENT_DIR", Path(tempfile.mkdtemp())), \
             patch.object(x402_client, "pay_and_fetch", AsyncMock(return_value=paid_result)) as pay_mock:
            asyncio.run(x402.fetch(body, self.db))

        self.assertEqual(pay_mock.call_args.kwargs["expected_amount_raw"], "10000")
        self.assertEqual(pay_mock.call_args.kwargs["expected_pay_to"], PAY_TO)

    def test_a_decrypt_failure_is_a_clean_400_not_a_raw_crash(self):
        """A wallet whose key can't be decrypted with the current session
        key (e.g. after the passphrase-change race the app now prevents,
        or an install restored onto a different master key) must surface
        a real, JSON error - not an unhandled ValueError that crashes the
        endpoint with a non-JSON 500 the frontend can't even parse."""
        from fastapi import HTTPException
        from app.routers import x402

        probed = x402_client.X402Requirement(network="polygon", asset=POLYGON_USDC, amount_raw="10000", pay_to=PAY_TO)
        body = x402.X402FetchBody(wallet_id=self.wallet.id, network="polygon", url="https://example.com/resource")

        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch.object(x402_client, "probe", AsyncMock(return_value=probed)), \
             patch("app.tools.wallet.encrypt.decrypt_key",
                   side_effect=ValueError("wallet private key cannot be decrypted with the current SARA_MASTER_KEY")):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(x402.fetch(body, self.db))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("cannot be decrypted", ctx.exception.detail)

    def test_an_unexpected_exception_from_pay_and_fetch_is_a_clean_502_not_a_raw_crash(self):
        """Only X402Error was ever caught around pay_and_fetch - any other
        exception type (a bug in a dependency, an unexpected library
        error, ...) used to propagate uncaught and crash the endpoint with
        a bare, non-JSON response the UI could only show as a generic
        'x402 fetch failed.' with no way to tell what actually happened."""
        from fastapi import HTTPException
        from app.routers import x402

        probed = x402_client.X402Requirement(network="polygon", asset=POLYGON_USDC, amount_raw="10000", pay_to=PAY_TO)
        body = x402.X402FetchBody(wallet_id=self.wallet.id, network="polygon", url="https://example.com/resource")

        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch.object(x402_client, "probe", AsyncMock(return_value=probed)), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch.object(x402_client, "pay_and_fetch", AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(x402.fetch(body, self.db))
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("boom", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
