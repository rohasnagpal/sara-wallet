"""Tests for the x402 seller/paywall PHP code generator (the "paste this
into your PHP site" feature). generate_php() is the only thing that ends up
running on a stranger's server, so its output correctness matters directly:
a wrong asset address, amount, or facilitator URL would either fail to
collect any money at all, or (far worse) collect the wrong asset/amount
silently. These tests check the generated *source*, plus (in
test_generated_php_actually_lints, opt-in) that a real PHP interpreter
accepts it as syntactically valid.
"""
import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.tools.payments import x402_paywall_codegen as codegen

WALLET = "0x1234567890123456789012345678901234567890"


def _serve_and_get_402(code: str):
    """Actually serves generated PHP with PHP's built-in server and fetches
    it — returns (status, headers_dict, body_text) from the real response,
    not a guess about what the source would do."""
    import http.client
    import socket
    import time

    php = shutil.which("php")
    tmp_dir = tempfile.mkdtemp()
    (Path(tmp_dir) / "index.php").write_text(code)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [php, "-S", f"127.0.0.1:{port}", "-t", tmp_dir],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        resp = None
        for _ in range(50):
            time.sleep(0.1)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/")
                resp = conn.getresponse()
                break
            except (ConnectionRefusedError, OSError):
                resp = None
        if resp is None:
            raise RuntimeError("PHP built-in server never came up")
        body = resp.read().decode()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        shutil.rmtree(tmp_dir, ignore_errors=True)


class AmountRawTests(unittest.TestCase):
    def test_dollar_price_converts_to_six_decimal_usdc_raw_units(self):
        self.assertEqual(codegen.amount_raw_for("0.05"), 50000)
        self.assertEqual(codegen.amount_raw_for("1"), 1000000)
        self.assertEqual(codegen.amount_raw_for("1.5"), 1500000)

    def test_rejects_zero_or_negative_price(self):
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.amount_raw_for("0")
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.amount_raw_for("-1")

    def test_rejects_non_numeric_price(self):
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.amount_raw_for("free")


class NetworkAssetTests(unittest.TestCase):
    def test_test_mode_network_is_base_sepolia_with_x402_registry_usdc(self):
        asset = codegen.network_asset("base-sepolia")
        self.assertEqual(asset["caip2"], "eip155:84532")
        self.assertEqual(asset["usdc"], "0x036CbD53842c5426634e7929541eC2318f3dCF7e")

    def test_live_mode_networks_use_saras_own_trusted_usdc_registry(self):
        from app.core.assets import NETWORKS
        for network in codegen.LIVE_NETWORKS:
            asset = codegen.network_asset(network)
            self.assertEqual(asset["usdc"], NETWORKS[network]["usdc"])
            self.assertEqual(asset["caip2"], f"eip155:{NETWORKS[network]['chain_id']}")

    def test_unsupported_network_is_rejected(self):
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.network_asset("ethereum")  # not settleable by either facilitator


class GeneratePhpTests(unittest.TestCase):
    def _config(self, code: str) -> dict:
        """Pull the literal PHP array passed to sara_x402_paywall(...) back
        out as Python data, by asking PHP itself to var_export it as JSON —
        the only way to be sure the generated *source* actually assembles
        into the array we intend, not just that individual strings look right."""
        php = shutil.which("php")
        if not php:
            self.skipTest("php interpreter not available")
        harness = code.replace(
            "sara_x402_paywall([",
            "$__sara_cfg = ([",
        ).split("function sara_x402_paywall", 1)[0]
        harness += "\necho json_encode($__sara_cfg);\n"
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False) as f:
            f.write(harness)
            path = f.name
        try:
            out = subprocess.run([php, path], capture_output=True, text=True, timeout=10)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_test_mode_config_matches_the_verified_x402_protocol_shape(self):
        code = codegen.generate_php(
            label="My Article", wallet_address=WALLET, mode="test",
            network="base-sepolia", price_usd="0.05",
        )
        cfg = self._config(code)
        self.assertEqual(cfg["network"], "eip155:84532")
        self.assertEqual(cfg["asset"], "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
        self.assertEqual(cfg["amount"], "50000")
        self.assertEqual(cfg["pay_to"], WALLET)
        self.assertEqual(cfg["facilitator_url"], "https://x402.org/facilitator")
        self.assertIsNone(cfg["auth"])

    def test_live_mode_embeds_cdp_key_and_targets_cdp_facilitator(self):
        code = codegen.generate_php(
            label="Report", wallet_address=WALLET, mode="live", network="polygon",
            price_usd="2.00", cdp_key_id="orgs/x/apiKeys/y", cdp_key_secret="c2VjcmV0",
        )
        cfg = self._config(code)
        self.assertEqual(cfg["network"], "eip155:137")
        self.assertEqual(cfg["amount"], "2000000")
        self.assertIn("api.cdp.coinbase.com", cfg["facilitator_url"])
        self.assertEqual(cfg["auth"]["key_id"], "orgs/x/apiKeys/y")
        self.assertEqual(cfg["auth"]["key_secret"], "c2VjcmV0")

    def test_live_mode_requires_a_cdp_key(self):
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.generate_php(
                label="x", wallet_address=WALLET, mode="live", network="base", price_usd="1",
            )

    def test_live_mode_rejects_a_network_the_cdp_facilitator_cannot_settle(self):
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.generate_php(
                label="x", wallet_address=WALLET, mode="live", network="ethereum",
                price_usd="1", cdp_key_id="k", cdp_key_secret="s",
            )

    def test_test_mode_is_pinned_to_base_sepolia(self):
        with self.assertRaises(codegen.PaywallCodegenError):
            codegen.generate_php(
                label="x", wallet_address=WALLET, mode="test", network="base", price_usd="1",
            )

    def test_a_label_that_tries_to_close_the_docblock_comment_is_neutralized(self):
        code = codegen.generate_php(
            label="Breakout */ <?php system('x'); ?>", wallet_address=WALLET,
            mode="test", network="base-sepolia", price_usd="0.01",
        )
        # The literal "*/" must not appear before the real end of the
        # docblock, or attacker-controlled text could terminate the comment
        # early and inject raw PHP into the generated file.
        docblock_end = code.index("*/")
        self.assertNotIn("*/", code[:docblock_end])

    def test_a_label_with_quotes_and_backslashes_stays_inert_string_data(self):
        tricky = """Quote " apostrophe ' backslash \\ done"""
        code = codegen.generate_php(
            label=tricky, wallet_address=WALLET, mode="test", network="base-sepolia", price_usd="0.01",
        )
        cfg = self._config(code)
        self.assertEqual(cfg["label"], tricky)

    def test_file_ends_with_a_marked_place_to_paste_premium_content(self):
        code = codegen.generate_php(
            label="Article", wallet_address=WALLET, mode="test", network="base-sepolia", price_usd="0.10",
        )
        self.assertIn("PREMIUM CONTENT GOES BELOW THIS LINE", code)
        # Ends with a closing PHP tag so plain HTML pasted right after it
        # renders directly, without the user needing to wrap it in echo().
        self.assertTrue(code.rstrip().endswith("?>"))
        # The marker must come after every function definition — pasting
        # content right after it must not land in the middle of one.
        self.assertGreater(code.index("PREMIUM CONTENT GOES BELOW THIS LINE"), code.rindex("function "))

    def test_without_a_preview_message_the_default_plain_text_is_used(self):
        php = shutil.which("php")
        if not php:
            self.skipTest("php interpreter not available")
        code = codegen.generate_php(
            label="Article", wallet_address=WALLET, mode="test", network="base-sepolia", price_usd="0.10",
        )
        status, headers, body = _serve_and_get_402(code)
        self.assertEqual(headers.get("content-type", "").split(";")[0].strip(), "text/plain")
        self.assertIn("402 Payment Required", body)

    def test_a_preview_message_is_shown_instead_of_the_default_and_as_html(self):
        php = shutil.which("php")
        if not php:
            self.skipTest("php interpreter not available")
        code = codegen.generate_php(
            label="Article", wallet_address=WALLET, mode="test", network="base-sepolia", price_usd="0.10",
            preview_message="<h1>Subscribe for $0.10</h1>",
        )
        status, headers, body = _serve_and_get_402(code)
        self.assertEqual(status, 402)
        self.assertEqual(headers.get("content-type", "").split(";")[0].strip(), "text/html")
        self.assertIn("<h1>Subscribe for $0.10</h1>", body)
        # The machine-readable challenge header must still be present
        # unchanged — the preview is purely for a human visitor.
        self.assertIsNotNone(headers.get("payment-required"))

    def test_a_preview_message_with_a_quote_stays_intact_php_still_lints(self):
        php = shutil.which("php")
        if not php:
            self.skipTest("php interpreter not available")
        code = codegen.generate_php(
            label="Article", wallet_address=WALLET, mode="test", network="base-sepolia", price_usd="0.10",
            preview_message="""Come on in, it's only $0.10 "great deal" \\ backslash""",
        )
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            out = subprocess.run([php, "-l", path], capture_output=True, text=True, timeout=10)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_generated_php_actually_lints(self):
        php = shutil.which("php")
        if not php:
            self.skipTest("php interpreter not available")
        code = codegen.generate_php(
            label="Lint check", wallet_address=WALLET, mode="live", network="arbitrum",
            price_usd="0.99", cdp_key_id="k", cdp_key_secret="s",
        )
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False) as f:
            f.write(code)
            path = f.name
        try:
            out = subprocess.run([php, "-l", path], capture_output=True, text=True, timeout=10)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_served_php_emits_the_exact_wire_format_captured_from_the_reference_seller(self):
        """End-to-end: actually serve the generated file with PHP's built-in
        server and fetch it, checking the real 402 response against the
        shape captured empirically from x402's own reference seller
        (examples/x402/demo_seller.py) — not just that the source looks
        plausible."""
        php = shutil.which("php")
        if not php:
            self.skipTest("php interpreter not available")
        import http.client
        import socket
        import time

        code = codegen.generate_php(
            label="E2E Article", wallet_address=WALLET, mode="test",
            network="base-sepolia", price_usd="0.25",
        )
        tmp_dir = tempfile.mkdtemp()
        (Path(tmp_dir) / "index.php").write_text(code)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        proc = subprocess.Popen(
            [php, "-S", f"127.0.0.1:{port}", "-t", tmp_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            conn = None
            for _ in range(50):
                time.sleep(0.1)
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    conn.request("GET", "/")
                    resp = conn.getresponse()
                    break
                except (ConnectionRefusedError, OSError):
                    conn = None
            self.assertIsNotNone(conn, "PHP built-in server never came up")
            self.assertEqual(resp.status, 402)
            header = resp.getheader("payment-required")
            self.assertIsNotNone(header)
            challenge = json.loads(base64.b64decode(header))
            self.assertEqual(challenge["x402Version"], 2)
            accept = challenge["accepts"][0]
            self.assertEqual(accept["scheme"], "exact")
            self.assertEqual(accept["network"], "eip155:84532")
            self.assertEqual(accept["asset"], "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
            self.assertEqual(accept["amount"], "250000")
            self.assertEqual(accept["payTo"], WALLET)
            self.assertEqual(accept["extra"], {"name": "USDC", "version": "2"})
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
