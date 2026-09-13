import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from eth_account import Account

from app.tools.names import eip712_records, sara_names
from app.tools.names.eip712_records import NameRecord

REGISTRY = "0x3333333333333333333333333333333333333333"
CHAIN_ID = 80002


class NamehashCrossLanguageTests(unittest.TestCase):
    """These expected values come from contracts/test/vectors/namehash_vectors.json,
    itself cross-checked against SaraNamesRegistry.namehash() in Solidity
    (contracts/test/SaraNamesRegistry.namehash_vectors.t.sol). Any change
    here must be regenerated the same way, never hand-edited."""

    def test_root_labels(self):
        self.assertEqual(sara_names.node_hex("rohas"), "0x25e638aff03d6438bbe32948636d1ff586a6c6224f5b6583faeba8b5d425aae8")
        self.assertEqual(sara_names.node_hex("c4lab"), "0x034d32cd1aa5fc319f277c02103273e7b3ec4745f341b1ac169e2faaf409e70a")
        self.assertEqual(sara_names.node_hex("me-india"), "0xba538f6593850a54de37fe1dedcb71e69339ddb4ed65cda0a3afaa5c9a7b10ec")

    def test_multi_label_names(self):
        self.assertEqual(sara_names.node_hex("pay.rohas"), "0xb8d2446c3f2e4d9b0ca947afc016005b1ddb3a019b50ffde7dd19c57b16283c9")
        self.assertEqual(sara_names.node_hex("a.b.c"), "0x257d8d183501fdaabc229b5b3b63dc77d8dc3685dc5623e91f472e7f2e443c5a")

    def test_empty_name_is_zero_node(self):
        self.assertEqual(sara_names.node_hex(""), "0x" + "00" * 32)

    def test_deterministic_and_composable(self):
        root = sara_names.namehash_name("rohas")
        composed = sara_names.namehash_label(root, "pay")
        self.assertEqual("0x" + composed.hex(), sara_names.node_hex("pay.rohas"))


class LabelValidationTests(unittest.TestCase):
    def test_valid_labels(self):
        for label in ("rohas", "c4-lab", "abc", "a" * 63):
            self.assertTrue(sara_names.is_valid_label(label), label)

    def test_invalid_labels(self):
        for label in ("ab", "a" * 64, "-rohas", "rohas-", "Rohas", "ro has", "ro.has", ""):
            self.assertFalse(sara_names.is_valid_label(label), label)

    def test_validate_name_checks_every_dot_separated_label(self):
        self.assertIsNone(sara_names.validate_name("pay.rohas"))
        self.assertIsNotNone(sara_names.validate_name("pay.-rohas"))
        self.assertIsNotNone(sara_names.validate_name(""))


class Eip712RecordTests(unittest.TestCase):
    def setUp(self):
        self.owner = Account.create()
        self.other = Account.create()
        self.node = sara_names.node_hex("rohas")
        self.now = int(time.time())

    def make_record(self, **overrides) -> NameRecord:
        fields = dict(
            node=self.node, record_epoch=1, sequence=1, issued_at=self.now - 10, expires_at=self.now + 3600,
            addresses=[{"network": "eip155:137", "addr": "0x2222222222222222222222222222222222222222"}],
            preferred_network="eip155:137", preferred_token="USDC",
        )
        fields.update(overrides)
        return NameRecord(**fields).with_content_hash()

    def sign(self, record: NameRecord, key: str) -> str:
        return eip712_records.sign_record(record, chain_id=CHAIN_ID, registry_address=REGISTRY, private_key=key)

    def verify(self, record, signature, **kwargs):
        defaults = dict(
            chain_id=CHAIN_ID, registry_address=REGISTRY, expected_node=self.node,
            current_owner=self.owner.address, current_record_signer=self.owner.address, current_epoch=1,
        )
        defaults.update(kwargs)
        return eip712_records.verify_record(record, signature, **defaults)

    def test_valid_record_verifies(self):
        record = self.make_record()
        sig = self.sign(record, self.owner.key.hex())
        ok, reason = self.verify(record, sig)
        self.assertTrue(ok, reason)

    def test_persisted_record_round_trip_keeps_content_hash_and_signature(self):
        record = self.make_record(addresses=[
            {"network": "eip155:8453", "addr": "0x4444444444444444444444444444444444444444"},
            {"network": "eip155:137", "addr": "0x2222222222222222222222222222222222222222"},
        ])
        signature = self.sign(record, self.owner.key.hex())
        fields = json.loads(record.canonical_json())
        restored = NameRecord(
            node=fields["node"], record_epoch=int(fields["recordEpoch"]),
            sequence=int(fields["sequence"]), issued_at=int(fields["issuedAt"]),
            expires_at=int(fields["expiresAt"]), addresses=fields["addresses"],
            preferred_network=fields["preferredNetwork"], preferred_token=fields["preferredToken"],
            content_hash=fields["contentHash"],
        )
        ok, reason = self.verify(restored, signature)
        self.assertTrue(ok, reason)

    def test_wrong_chain_is_rejected(self):
        record = self.make_record()
        sig = self.sign(record, self.owner.key.hex())
        ok, reason = self.verify(record, sig, chain_id=1)  # signed for Amoy, checked against mainnet chain id
        self.assertFalse(ok)

    def test_wrong_node_is_rejected(self):
        record = self.make_record()
        sig = self.sign(record, self.owner.key.hex())
        ok, reason = self.verify(record, sig, expected_node=sara_names.node_hex("someone-else"))
        self.assertFalse(ok)
        self.assertIn("node", reason)

    def test_stale_sequence_is_rejected(self):
        record = self.make_record(sequence=5)
        sig = self.sign(record, self.owner.key.hex())
        ok, reason = self.verify(record, sig, last_seen_sequence=5)
        self.assertFalse(ok)
        self.assertIn("stale", reason)

    def test_expired_record_is_rejected(self):
        record = self.make_record(issued_at=self.now - 100, expires_at=self.now - 10)
        sig = self.sign(record, self.owner.key.hex())
        ok, reason = self.verify(record, sig)
        self.assertFalse(ok)
        self.assertIn("expired", reason)

    def test_epoch_mismatch_after_transfer_is_rejected(self):
        record = self.make_record(record_epoch=1)
        sig = self.sign(record, self.owner.key.hex())
        # Simulate a transfer: on-chain epoch is now 2, but the record was signed under epoch 1.
        ok, reason = self.verify(record, sig, current_epoch=2)
        self.assertFalse(ok)
        self.assertIn("epoch", reason)

    def test_signature_from_non_owner_non_signer_is_rejected(self):
        record = self.make_record()
        sig = self.sign(record, self.other.key.hex())
        ok, reason = self.verify(record, sig)
        self.assertFalse(ok)
        self.assertIn("neither", reason)

    def test_record_signer_other_than_owner_is_accepted(self):
        record = self.make_record()
        sig = self.sign(record, self.other.key.hex())
        ok, reason = self.verify(record, sig, current_record_signer=self.other.address)
        self.assertTrue(ok, reason)

    def test_tampered_content_hash_is_rejected(self):
        record = self.make_record()
        sig = self.sign(record, self.owner.key.hex())
        tampered = NameRecord(
            node=record.node, record_epoch=record.record_epoch, sequence=record.sequence,
            issued_at=record.issued_at, expires_at=record.expires_at,
            addresses=[{"network": "eip155:137", "addr": "0x9999999999999999999999999999999999999999"}],
            preferred_network=record.preferred_network, preferred_token=record.preferred_token,
            content_hash=record.content_hash,  # stale hash from the original addresses
        )
        ok, reason = self.verify(tampered, sig)
        self.assertFalse(ok)

    def test_canonical_json_is_deterministic_regardless_of_address_order(self):
        r1 = self.make_record(addresses=[
            {"network": "eip155:137", "addr": "0xaaa"}, {"network": "solana:mainnet", "addr": "Sol111"},
        ])
        r2 = self.make_record(addresses=[
            {"network": "solana:mainnet", "addr": "Sol111"}, {"network": "eip155:137", "addr": "0xaaa"},
        ])
        # with_content_hash() already ran on both — canonical_json() sorts by network either way.
        self.assertEqual(
            NameRecord(**{**vars(r1)}).canonical_json(),
            NameRecord(**{**vars(r2)}).canonical_json(),
        )


class RegistryClientHelperTests(unittest.TestCase):
    def test_price_decimal_uses_six_decimals(self):
        self.assertEqual(sara_names.price_decimal(50_000000), "50")
        self.assertEqual(sara_names.price_decimal(5_500000), "5.5")

    def test_is_configured_reflects_setting(self):
        from app.core.config import settings
        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", ""):
            self.assertFalse(sara_names.is_configured())
        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", REGISTRY):
            self.assertTrue(sara_names.is_configured())


if __name__ == "__main__":
    unittest.main()
