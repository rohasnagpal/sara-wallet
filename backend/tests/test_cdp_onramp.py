"""Tests for Coinbase's CDP Onramp integration (buy USDC with a card,
directly into a Sara wallet) - see cdp_onramp.py's module docstring for
why this is Coinbase's product, not Circle's undocumented "Onramp Kit".
"""
import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

from app.tools.payments import cdp_onramp


def _fake_cdp_key():
    """Builds a real Ed25519 keypair in CDP's documented on-wire format
    (base64 of the 32-byte seed concatenated with the 32-byte public key)
    - the same shape a real CDP-issued key secret has."""
    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )
    key_secret_b64 = base64.b64encode(seed + public_key).decode()
    return private_key, key_secret_b64


class BuildJwtTests(unittest.TestCase):
    def test_produces_a_jwt_whose_signature_actually_verifies(self):
        """The strongest possible check: reproduce the exact
        header.claims signing input this function builds, and confirm the
        signature it produced actually verifies against the real public
        key - not just that the output looks shaped like a JWT."""
        private_key, key_secret_b64 = _fake_cdp_key()
        jwt = cdp_onramp.build_jwt("orgs/x/apiKeys/y", key_secret_b64, "POST", "/platform/v2/onramp/sessions")

        header_b64, claims_b64, sig_b64 = jwt.split(".")

        def _b64url_decode(s):
            padded = s + "=" * (-len(s) % 4)
            return base64.urlsafe_b64decode(padded)

        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(claims_b64))
        signature = _b64url_decode(sig_b64)

        self.assertEqual(header["alg"], "EdDSA")
        self.assertEqual(header["kid"], "orgs/x/apiKeys/y")
        self.assertEqual(claims["iss"], "cdp")
        self.assertEqual(claims["sub"], "orgs/x/apiKeys/y")
        self.assertEqual(claims["aud"], ["cdp_service"])
        self.assertEqual(claims["uri"], "POST api.cdp.coinbase.com/platform/v2/onramp/sessions")
        self.assertLessEqual(claims["exp"] - claims["nbf"], 120)

        signing_input = f"{header_b64}.{claims_b64}".encode()
        # Raises if invalid - this is the actual cryptographic proof.
        private_key.public_key().verify(signature, signing_input)

    def test_two_calls_use_different_nonces(self):
        _, key_secret_b64 = _fake_cdp_key()
        jwt1 = cdp_onramp.build_jwt("k", key_secret_b64, "POST", "/x")
        jwt2 = cdp_onramp.build_jwt("k", key_secret_b64, "POST", "/x")
        self.assertNotEqual(jwt1, jwt2)

    def test_rejects_a_secret_that_isnt_64_bytes(self):
        with self.assertRaises(cdp_onramp.OnrampError) as ctx:
            cdp_onramp.build_jwt("k", base64.b64encode(b"too short").decode(), "POST", "/x")
        self.assertIn("64 bytes", str(ctx.exception))

    def test_rejects_invalid_base64(self):
        with self.assertRaises(cdp_onramp.OnrampError):
            cdp_onramp.build_jwt("k", "not valid base64!!!", "POST", "/x")

    def test_rejects_missing_key_id_or_secret(self):
        with self.assertRaises(cdp_onramp.OnrampError):
            cdp_onramp.build_jwt("", "somesecret", "POST", "/x")
        with self.assertRaises(cdp_onramp.OnrampError):
            cdp_onramp.build_jwt("k", "", "POST", "/x")


class CreateSessionTests(unittest.TestCase):
    def setUp(self):
        _, self.key_secret_b64 = _fake_cdp_key()

    def test_successful_session_returns_the_onramp_url(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session": {"onrampUrl": "https://pay.coinbase.com/buy?sessionToken=abc123"},
            "quote": {"paymentTotal": "100.75", "purchaseAmount": "100.000000"},
        }
        with patch("httpx.post", return_value=mock_response) as post:
            result = cdp_onramp.create_session(
                "k", self.key_secret_b64, destination_address="0x" + "11" * 20,
                network="base", payment_amount_usd="100",
            )
        self.assertEqual(result["onramp_url"], "https://pay.coinbase.com/buy?sessionToken=abc123")
        self.assertEqual(result["quote"]["purchaseAmount"], "100.000000")

        body = post.call_args.kwargs["json"]
        self.assertEqual(body["destinationAddress"], "0x" + "11" * 20)
        self.assertEqual(body["destinationNetwork"], "base")
        self.assertEqual(body["purchaseCurrency"], "USDC")
        self.assertEqual(body["paymentAmount"], "100")
        self.assertEqual(body["paymentCurrency"], "USD")
        headers = post.call_args.kwargs["headers"]
        self.assertTrue(headers["Authorization"].startswith("Bearer "))

    def test_a_rejected_network_surfaces_coinbases_own_error_not_a_guess(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "unsupported destinationNetwork"}'
        with patch("httpx.post", return_value=mock_response):
            with self.assertRaises(cdp_onramp.OnrampError) as ctx:
                cdp_onramp.create_session(
                    "k", self.key_secret_b64, destination_address="0x" + "11" * 20, network="not-a-real-network",
                )
        self.assertIn("unsupported destinationNetwork", str(ctx.exception))

    def test_a_missing_checkout_url_in_a_200_response_is_an_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"session": {}}
        with patch("httpx.post", return_value=mock_response):
            with self.assertRaises(cdp_onramp.OnrampError):
                cdp_onramp.create_session(
                    "k", self.key_secret_b64, destination_address="0x" + "11" * 20, network="base",
                )

    def test_a_network_failure_is_a_clean_onramp_error(self):
        with patch("httpx.post", side_effect=Exception("connection refused")):
            with self.assertRaises(cdp_onramp.OnrampError) as ctx:
                cdp_onramp.create_session(
                    "k", self.key_secret_b64, destination_address="0x" + "11" * 20, network="base",
                )
        self.assertIn("Could not reach Coinbase", str(ctx.exception))

    def test_optional_fields_omitted_when_not_provided(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"session": {"onrampUrl": "https://pay.coinbase.com/buy?x"}}
        with patch("httpx.post", return_value=mock_response) as post:
            cdp_onramp.create_session(
                "k", self.key_secret_b64, destination_address="0x" + "11" * 20, network="base",
            )
        body = post.call_args.kwargs["json"]
        self.assertNotIn("paymentAmount", body)
        self.assertNotIn("country", body)


if __name__ == "__main__":
    unittest.main()
