import ast
import json
import os
import pathlib
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.tools.market import paraswap, tx_simulate
from app.tools.payments import reconcile
from app.tools.trading import lifi
from app.tools.wallet import encrypt, lock
from app.tools.wallet import keygen
from app.tools.proofs import blockchainproof
from app.routers import chat, wallets
from app.core import assets
from app.core.amounts import to_base_units


class TransactionValidationTests(unittest.TestCase):
    def test_evm_rejects_more_than_exact_confirmed_input(self):
        wallet = "0x1111111111111111111111111111111111111111"
        src = "0x2222222222222222222222222222222222222222"
        dst = "0x3333333333333333333333333333333333333333"
        changes = [
            {"changeType": "TRANSFER", "assetType": "ERC20", "from": wallet, "to": "0x4",
             "contractAddress": src, "rawAmount": "102"},
            {"changeType": "TRANSFER", "assetType": "ERC20", "from": "0x4", "to": wallet,
             "contractAddress": dst, "rawAmount": "90"},
        ]
        with patch.object(tx_simulate, "_simulate", return_value=changes):
            with self.assertRaisesRegex(ValueError, "confirmed input amount"):
                tx_simulate.verify_swap_effect(
                    "ethereum", wallet, "0x4", "0x", 0,
                    wallet_address=wallet, expected_src_token=src, expected_dst_token=dst,
                    expected_src_amount=100, expected_min_dst_amount=90,
                )

    def test_bridge_source_simulation_does_not_require_destination_credit(self):
        wallet = "0x1111111111111111111111111111111111111111"
        src = "0x2222222222222222222222222222222222222222"
        changes = [{
            "changeType": "TRANSFER", "assetType": "ERC20", "from": wallet, "to": "0x4",
            "contractAddress": src, "rawAmount": "100",
        }]
        with patch.object(tx_simulate, "_simulate", return_value=changes):
            tx_simulate.verify_swap_effect(
                "ethereum", wallet, "0x4", "0x", 0,
                wallet_address=wallet, expected_src_token=src,
                expected_dst_token="0x3333333333333333333333333333333333333333",
                expected_src_amount=100, expected_min_dst_amount=90,
                verify_destination=False,
            )

    def test_lifi_rejects_unofficial_executor_and_spender(self):
        w3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda address: b"\x01"))
        wallet = "0x1111111111111111111111111111111111111111"
        source = "0x2222222222222222222222222222222222222222"
        good = {"to": lifi._LIFI_DIAMOND, "value": "0x0", "data": "0x12345678"}
        extracted = ("across", source, wallet, 100, 42161, True, False)
        with patch.object(lifi, "_extract_main_parameters", return_value=extracted):
            lifi.validate_bridge_transaction_static(
                w3, good, wallet, "ethereum", 0, lifi._ERC20_PROXIES["ethereum"],
                expected_src_token=source, expected_src_amount=100,
                expected_destination_chain_id=42161,
            )
        bad = {**good, "to": "0x2222222222222222222222222222222222222222"}
        with self.assertRaisesRegex(ValueError, "unrecognized executor"):
            lifi.validate_bridge_transaction_static(
                w3, bad, wallet, "ethereum", 0,
                expected_src_token=source, expected_src_amount=100,
                expected_destination_chain_id=42161,
            )
        with self.assertRaisesRegex(ValueError, "not an official LI.FI spender"):
            lifi.validate_bridge_transaction_static(
                w3, good, wallet, "ethereum", 0,
                "0x3333333333333333333333333333333333333333",
                expected_src_token=source, expected_src_amount=100,
                expected_destination_chain_id=42161,
            )

    def test_lifi_calldata_is_bound_to_confirmed_destination_and_amount(self):
        wallet = "0x1111111111111111111111111111111111111111"
        attacker = "0x9999999999999999999999999999999999999999"
        source = "0x2222222222222222222222222222222222222222"
        base = ("across", source, wallet, 100, 42161, True, False)

        lifi._validate_main_parameters(
            base, wallet_address=wallet, expected_src_token=source,
            expected_src_amount=100, expected_destination_chain_id=42161,
        )
        for malicious, message in (
            (("across", source, attacker, 100, 42161, True, False), "not this wallet"),
            (("across", source, wallet, 100, 10, True, False), "not confirmed chain"),
            (("across", source, wallet, 101, 42161, True, False), "exact confirmed amount"),
            (("across", source, wallet, 100, 42161, True, True), "destination-chain call"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                lifi._validate_main_parameters(
                    malicious, wallet_address=wallet, expected_src_token=source,
                    expected_src_amount=100, expected_destination_chain_id=42161,
                )


class AuthenticationAndMigrationTests(unittest.TestCase):
    def test_confirmation_requires_correct_passphrase(self):
        previous = (lock._session_key, lock._last_activity, lock._failed_attempts, lock._locked_until)
        lock._session_key = b"x" * 32
        lock._last_activity = time.time()
        lock._failed_attempts = 0
        lock._locked_until = 0
        try:
            with patch.object(encrypt, "has_new_format", return_value=True), \
                 patch.object(encrypt, "verify_new", side_effect=lambda value: b"x" * 32 if value == "correct" else None):
                self.assertFalse(lock.confirm_passphrase("wrong"))
                self.assertTrue(lock.confirm_passphrase("correct"))
        finally:
            lock._session_key, lock._last_activity, lock._failed_attempts, lock._locked_until = previous

    def test_pending_migration_is_restart_discoverable(self):
        old_env, old_pending = encrypt._ENV_FILE, encrypt._PENDING_MIGRATION_FILE
        with tempfile.TemporaryDirectory() as tmp:
            encrypt._ENV_FILE = pathlib.Path(tmp) / ".env.local"
            encrypt._PENDING_MIGRATION_FILE = pathlib.Path(tmp) / ".env.local.migration-pending"
            try:
                encrypt._ENV_FILE.write_text("SARA_MASTER_KEY=" + "11" * 32 + "\n")
                salt = b"s" * 16
                key = encrypt._scrypt_key("correct horse", salt)
                encrypt.stage_migration_update({
                    "SARA_MASTER_KEY": None,
                    "SARA_MASTER_SALT": salt.hex(),
                    "SARA_MASTER_VERIFIER": encrypt._verifier_for(key),
                })
                self.assertTrue(encrypt.has_pending_migration())
                self.assertEqual(encrypt.verify_pending_migration("correct horse"), key)
                encrypt.promote_pending_migration()
                self.assertTrue(encrypt.has_new_format())
            finally:
                encrypt._ENV_FILE, encrypt._PENDING_MIGRATION_FILE = old_env, old_pending


class WalletKeyLifecycleTests(unittest.TestCase):
    def test_key_generation_uses_only_approved_library_entrypoints(self):
        forbidden = {"random", "time", "uuid", "hashlib", "secrets"}
        boundaries = {
            keygen: {"generate_evm_wallet"},
        }
        for module, function_names in boundaries.items():
            tree = ast.parse(pathlib.Path(module.__file__).read_text())
            functions = [
                node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in function_names
            ]
            self.assertEqual({node.name for node in functions}, function_names)
            nodes = [child for function in functions for child in ast.walk(function)]
            referenced = {node.id for node in nodes if isinstance(node, ast.Name)}
            self.assertTrue(forbidden.isdisjoint(referenced), module.__name__)
            calls = {
                f"{node.func.value.id}.{node.func.attr}"
                for node in nodes
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            }
            self.assertNotIn("os.urandom", calls, module.__name__)

        evm_account = SimpleNamespace(
            address="0x" + "11" * 20,
            key=SimpleNamespace(hex=lambda: "22" * 32),
        )
        with patch.object(keygen.Account, "create", return_value=evm_account) as create:
            self.assertEqual(keygen.generate_evm_wallet()["private_key"], "22" * 32)
        create.assert_called_once_with()

    def test_key_generation_fails_closed_when_library_rng_fails(self):
        with patch.object(keygen.Account, "create", side_effect=OSError("CSPRNG unavailable")):
            with self.assertRaisesRegex(OSError, "CSPRNG unavailable"):
                keygen.generate_evm_wallet()
    def test_generated_wallet_sample_has_valid_shapes_and_no_duplicates(self):
        evm = []
        for _ in range(32):
            evm.append(keygen.generate_evm_wallet())

        self.assertEqual(len({w["private_key"] for w in evm}), len(evm))
        self.assertEqual(len({w["address"] for w in evm}), len(evm))
        self.assertTrue(all(len(w["private_key"].removeprefix("0x")) == 64 for w in evm))
        self.assertTrue(all(int(w["private_key"], 16) != 0 for w in evm))

    def test_encryption_uses_a_fresh_nonce_and_round_trips(self):
        key = b"k" * 32
        with patch.object(encrypt.os, "urandom", side_effect=[b"a" * 12, b"b" * 12]):
            first = encrypt.encrypt_with_key("secret", key)
            second = encrypt.encrypt_with_key("secret", key)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith((b"a" * 12).hex()))
        self.assertTrue(second.startswith((b"b" * 12).hex()))
        self.assertEqual(encrypt.decrypt_with_key(first, key), "secret")
        self.assertEqual(encrypt.decrypt_with_key(second, key), "secret")

    def test_binary_evidence_encryption_round_trips(self):
        key = b"e" * 32
        encrypted = encrypt.encrypt_bytes_with_key(b"PK\x03\x04evidence", key)
        self.assertNotEqual(encrypted, b"PK\x03\x04evidence")
        self.assertEqual(encrypt.decrypt_bytes_with_key(encrypted, key), b"PK\x03\x04evidence")

    def test_sensitive_exception_values_are_redacted(self):
        secret = "0x" + "ab" * 32
        message = chat._exception_message(
            RuntimeError(f"signing failed for {secret} / {secret[2:]}"), secret,
        )
        self.assertNotIn(secret, message)
        self.assertNotIn(secret[2:], message)
        self.assertIn("[REDACTED]", message)

    def test_export_is_explicitly_non_cacheable(self):
        wallet = SimpleNamespace(
            id=7, name="main", chain="evm", address="0x" + "11" * 20,
            encrypted_key="ciphertext",
        )
        query = SimpleNamespace(
            filter=lambda *args, **kwargs: SimpleNamespace(first=lambda: wallet),
        )
        db = SimpleNamespace(query=lambda *args, **kwargs: query)
        secret = "22" * 32
        with patch.object(wallets, "verify_passphrase", return_value=True), \
             patch.object(wallets, "decrypt_key", return_value=secret):
            response = wallets.export_wallet(
                wallet.id, wallets.ExportWalletRequest(passphrase="correct horse"), db,
            )
        payload = json.loads(response.body)
        self.assertEqual(payload["private_key"], secret)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(response.headers["pragma"], "no-cache")


class ReconciliationAndFrontendTests(unittest.TestCase):
    def test_removed_network_credentials_are_not_settings(self):
        from app.routers import settings
        self.assertNotIn("TRONGRID_API_KEY", settings.ALLOWED_KEYS)
        self.assertNotIn("HELIUS_RPC", settings.ALLOWED_KEYS)

    def test_amount_conversion_is_exact_and_rejects_excess_precision(self):
        self.assertEqual(to_base_units("0.29", 6, "USDC"), 290_000)
        self.assertEqual(to_base_units(0.1, 18, "ETH"), 100_000_000_000_000_000)
        with self.assertRaisesRegex(ValueError, "more than 6 decimal places"):
            to_base_units("1.0000001", 6, "USDC")
        with self.assertRaisesRegex(ValueError, "positive finite"):
            to_base_units("NaN", 6, "USDC")

    def test_reconciliation_uses_exact_base_units(self):
        self.assertEqual(reconcile._required_raw(1, 6), 1_000_000)
        self.assertEqual(reconcile._required_raw(1.0000001, 6), 1_000_001)

    def test_wallet_feedback_escapes_backend_values(self):
        html = pathlib.Path(__file__).parents[2].joinpath("index.html").read_text()
        self.assertIn("_escapeHtml(data.name)", html)
        self.assertIn("_escapeHtml(data.address)", html)

    def test_proof_theme_is_default_and_file_is_hashed_locally(self):
        html = pathlib.Path(__file__).parents[2].joinpath("index.html").read_text()
        self.assertIn('<div class="app-shell theme-proof" id="appShell">', html)
        self.assertIn('<div class="lock-overlay theme-proof open" id="lockOverlay">', html)
        self.assertIn('data-theme="proof" title="Proof — inspired by BlockchainProof"', html)
        self.assertIn("crypto.subtle.digest('SHA-256'", html)
        # The BlockchainProof flow must only ever send a file's SHA-256
        # fingerprint, never its raw bytes — scoped to that feature's own JS
        # section rather than the whole file, since business features (e.g.
        # airdrop CSV import) legitimately upload file content elsewhere.
        proof_section = html.split("// BLOCKCHAIN PROOFS", 1)[1].split("// ADDRESS BOOK / DIRECTORY", 1)[0]
        self.assertNotIn("new FormData", proof_section)


class BlockchainProofCheckoutTests(unittest.TestCase):
    def _checkout(self, address):
        now = int(time.time())
        return {
            "checkout_id": "CO_TEST", "checkout_token": "a" * 64,
            "price": {"amount": "1.00", "currency": "USDC", "network": "polygon"},
            "typed_data": {
                "types": {
                    "EIP712Domain": [
                        {"name":"name","type":"string"},{"name":"version","type":"string"},
                        {"name":"chainId","type":"uint256"},{"name":"verifyingContract","type":"address"},
                    ],
                    "ReceiveWithAuthorization": [
                        {"name":"from","type":"address"},{"name":"to","type":"address"},
                        {"name":"value","type":"uint256"},{"name":"validAfter","type":"uint256"},
                        {"name":"validBefore","type":"uint256"},{"name":"nonce","type":"bytes32"},
                    ],
                },
                "primaryType": "ReceiveWithAuthorization",
                "domain": {"name":"USD Coin","version":"2","chainId":137,"verifyingContract":assets.NETWORKS["polygon"]["usdc"]},
                "message": {"from":address,"to":"0x" + "22"*20,"value":"1000000","validAfter":"0","validBefore":str(now+900),"nonce":"0x"+"33"*32},
            },
        }

    def test_checkout_validation_binds_exact_payment(self):
        account = keygen.Account.create()
        checkout = self._checkout(account.address)
        blockchainproof.validate_checkout(checkout, "ab" * 32, account.address)
        checkout["typed_data"]["message"]["value"] = "1000001"
        with self.assertRaisesRegex(blockchainproof.ProofServiceError, "safety validation"):
            blockchainproof.validate_checkout(checkout, "ab" * 32, account.address)

    def test_typed_payment_signature_recovers_selected_wallet(self):
        account = keygen.Account.create()
        checkout = self._checkout(account.address)
        signature = blockchainproof.sign_checkout(checkout["typed_data"], account.key.hex(), account.address)
        self.assertRegex(signature, r"^0x[0-9a-f]{130}$")

    def test_wallet_secret_fields_are_cleared_and_export_is_not_cached(self):
        html = pathlib.Path(__file__).parents[2].joinpath("index.html").read_text()
        self.assertIn("document.getElementById('walletPrivKey').value = '';", html)
        self.assertIn("data.private_key = '';", html)
        self.assertIn("cache: 'no-store'", html)
        self.assertRegex(
            html,
            r'id="walletPrivKey"[^>]+autocomplete="off"[^>]+spellcheck="false"',
        )

    def test_confirmation_fee_limits_match_signer_policy(self):
        self.assertEqual(lifi.max_total_network_fee_wei(None), lifi._MAX_BRIDGE_FEE_WEI)
        self.assertEqual(
            lifi.max_total_network_fee_wei("0x2222222222222222222222222222222222222222"),
            3 * lifi._MAX_BRIDGE_FEE_WEI,
        )


class AssetPolicyTests(unittest.TestCase):
    def test_supported_networks_and_circle_usdc_contracts(self):
        self.assertEqual(
            set(assets.NETWORKS),
            {"ethereum", "arbitrum", "base", "optimism", "polygon", "arc"},
        )
        self.assertEqual(assets.NETWORKS["ethereum"]["usdc"], "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
        self.assertEqual(assets.NETWORKS["arbitrum"]["usdc"], "0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
        self.assertEqual(assets.NETWORKS["base"]["usdc"], "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
        self.assertEqual(assets.NETWORKS["optimism"]["usdc"], "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85")
        self.assertEqual(assets.NETWORKS["polygon"]["usdc"], "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")
        # Arc's USDC is an enshrined precompile, not a deployed contract -
        # a different kind of address than every other network here, so
        # it's asserted on its own rather than folded into the loop above.
        self.assertEqual(assets.NETWORKS["arc"]["usdc"], "0x3600000000000000000000000000000000000000")
        self.assertEqual(assets.NETWORKS["arc"]["native"], "USDC")

    def test_only_usdc_and_native_assets_are_trusted(self):
        enabled = ",".join(assets.ALL_NETWORKS)
        with patch.dict(os.environ, {"SARA_ENABLED_NETWORKS": enabled, "SARA_USDC_NETWORKS": enabled}):
            for network in assets.ALL_NETWORKS:
                if network == "arc":
                    # Arc is deliberately not wired into Paraswap - no swap
                    # aggregator has a confirmed Arc integration yet, so
                    # trusted_symbols() correctly returns nothing for it
                    # rather than pretending support that doesn't exist.
                    self.assertEqual(paraswap.trusted_symbols(network), [])
                    continue
                self.assertEqual(
                    paraswap.trusted_symbols(network),
                    [assets.NETWORKS[network]["native"], "USDC"],
                )
                self.assertIsNone(paraswap.resolve_token("USDT", network))

    def test_disabling_network_or_usdc_is_enforced_by_resolver(self):
        with patch.dict(os.environ, {
            "SARA_ENABLED_NETWORKS": "ethereum,base",
            "SARA_USDC_NETWORKS": "ethereum",
        }):
            self.assertEqual(paraswap.trusted_symbols("ethereum"), ["ETH", "USDC"])
            self.assertEqual(paraswap.trusted_symbols("base"), ["ETH"])
            self.assertEqual(paraswap.trusted_symbols("polygon"), [])
            self.assertIsNone(paraswap.resolve_token("USDC", "base"))


if __name__ == "__main__":
    unittest.main()
