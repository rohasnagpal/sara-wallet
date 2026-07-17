import pathlib
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.tools.market import jupiter, tx_simulate
from app.tools.payments import reconcile
from app.tools.trading import lifi
from app.tools.wallet import encrypt, lock
from app.core.amounts import to_base_units


class _Amount:
    def __init__(self, amount):
        self.amount = str(amount)


class _TokenBalance:
    def __init__(self, owner, mint, amount):
        self.owner = owner
        self.mint = mint
        self.ui_token_amount = _Amount(amount)


class TransactionValidationTests(unittest.TestCase):
    def test_jupiter_rejects_unexpected_native_loss(self):
        wallet, src, dst = "Wallet111", "SrcMint111", "DstMint111"
        result = SimpleNamespace(
            err=None,
            fee=5_000,
            pre_token_balances=[_TokenBalance(wallet, src, 100), _TokenBalance(wallet, dst, 0)],
            post_token_balances=[_TokenBalance(wallet, src, 0), _TokenBalance(wallet, dst, 90)],
            pre_balances=[10_000_000_000],
            post_balances=[1],
        )
        client = SimpleNamespace(
            simulate_transaction=lambda *args, **kwargs: SimpleNamespace(value=result)
        )
        with self.assertRaisesRegex(ValueError, "above the confirmed input"):
            jupiter._verify_via_simulation(client, object(), wallet, src, dst, 100, 90)

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


class ReconciliationAndFrontendTests(unittest.TestCase):
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

    def test_confirmation_fee_limits_match_signer_policy(self):
        self.assertEqual(lifi.max_total_network_fee_wei(None), lifi._MAX_BRIDGE_FEE_WEI)
        self.assertEqual(
            lifi.max_total_network_fee_wei("0x2222222222222222222222222222222222222222"),
            3 * lifi._MAX_BRIDGE_FEE_WEI,
        )
        fee, rent = jupiter.confirmation_safety_limits()
        self.assertEqual(fee, jupiter._MAX_TX_FEE_LAMPORTS)
        self.assertEqual(rent, jupiter._WRAP_RENT_BUFFER_LAMPORTS)


if __name__ == "__main__":
    unittest.main()
