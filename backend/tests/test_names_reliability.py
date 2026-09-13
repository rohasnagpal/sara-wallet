from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import AddressBook, Base, DomainEvent, IndexerCursor, SaraName, Wallet
from app.services import names_indexer
from app.tools.names import sara_names
from app.tools.names.resolver import resolve_recipient_input

ADDR_A = "0x" + "22" * 20
ADDR_B = "0x" + "33" * 20


class Stage7TestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address=ADDR_A, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()

    def tearDown(self):
        self.db.close()


class RpcRedundancyTests(unittest.TestCase):
    def test_amoy_rpc_urls_splits_comma_separated_list(self):
        with patch.object(settings, "SARA_NAME_AMOY_RPC_URL", "https://a.example, https://b.example ,https://c.example"):
            self.assertEqual(sara_names.amoy_rpc_urls(), ["https://a.example", "https://b.example", "https://c.example"])

    def _mock_web3_class(self, good_url: str | None):
        """Simulates Web3.HTTPProvider(url) -> provider, then Web3(provider)
        -> client, exactly as get_web3() calls it — a single side_effect on
        just the Web3 name couldn't distinguish the two calls."""
        mock_cls = MagicMock()
        mock_cls.HTTPProvider.side_effect = lambda url, **kw: SimpleNamespace(url=url)

        def _construct(provider):
            client = MagicMock()
            client.is_connected.return_value = (provider.url == good_url)
            return client

        mock_cls.side_effect = _construct
        return mock_cls

    def test_get_web3_tries_each_url_until_one_connects(self):
        mock_cls = self._mock_web3_class(good_url="https://good.example")
        with patch.object(settings, "SARA_NAME_AMOY_RPC_URL", "https://bad.example,https://good.example"), \
             patch("app.tools.names.sara_names.Web3", mock_cls):
            w3 = sara_names.get_web3()
        self.assertEqual([c.args[0] for c in mock_cls.HTTPProvider.call_args_list], ["https://bad.example", "https://good.example"])
        self.assertTrue(w3.is_connected())

    def test_get_web3_raises_when_all_urls_fail(self):
        mock_cls = self._mock_web3_class(good_url=None)
        with patch.object(settings, "SARA_NAME_AMOY_RPC_URL", "https://bad1.example,https://bad2.example"), \
             patch("app.tools.names.sara_names.Web3", mock_cls):
            with self.assertRaises(sara_names.SaraNamesError):
                sara_names.get_web3()


class IndexerCursorTests(Stage7TestCase):
    def test_sync_events_returns_early_when_unconfigured(self):
        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", ""):
            result = names_indexer.sync_events(self.db)
        self.assertFalse(result["synced"])

    def test_sync_events_only_advances_past_confirmation_depth(self):
        fake_w3 = MagicMock()
        fake_w3.eth.block_number = 1000
        fake_contract = MagicMock()
        for name in names_indexer._EVENT_NAMES:
            event_filter = MagicMock()
            event_filter.get_logs.return_value = []
            setattr(fake_contract.events, name, MagicMock(return_value=event_filter))

        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", "0x" + "44" * 20), \
             patch.object(settings, "SARA_NAME_AMOY_CONFIRMATIONS", 12), \
             patch.object(sara_names, "get_web3", return_value=fake_w3), \
             patch.object(sara_names, "_contract", return_value=fake_contract):
            result = names_indexer.sync_events(self.db)

        self.assertTrue(result["synced"])
        self.assertEqual(result["to_block"], 1000 - 12)  # never claims to have processed unconfirmed blocks
        cursor = self.db.query(IndexerCursor).filter_by(contract=names_indexer.CONTRACT_KEY).first()
        self.assertEqual(cursor.last_block, 1000 - 12)

    def test_apply_event_upserts_sara_name_for_own_wallet(self):
        log = {
            "args": {"node": b"\x11" * 32, "label": "rohas", "owner": ADDR_A, "expiry": int(datetime.utcnow().timestamp()) + 3600},
            "transactionHash": SimpleNamespace(hex=lambda: "0xabc"),
        }
        changed = names_indexer._apply_event(self.db, log, "NameRegistered", {ADDR_A.lower()})
        self.db.commit()
        self.assertTrue(changed)
        row = self.db.query(SaraName).filter_by(node="0x" + (b"\x11" * 32).hex()).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.wallet_id, self.wallet.id)
        self.assertEqual(row.status, "registered")

    def test_apply_event_ignores_names_owned_by_someone_else(self):
        log = {
            "args": {"node": b"\x22" * 32, "label": "someone-else", "owner": "0x" + "99" * 20,
                     "expiry": int(datetime.utcnow().timestamp()) + 3600},
            "transactionHash": SimpleNamespace(hex=lambda: "0xdef"),
        }
        changed = names_indexer._apply_event(self.db, log, "NameRegistered", {ADDR_A.lower()})
        self.assertFalse(changed)
        self.assertEqual(self.db.query(SaraName).count(), 0)


class ExpiryReminderTests(Stage7TestCase):
    def test_reminder_fires_once_per_unique_expiry(self):
        expiry = datetime.utcnow() + timedelta(days=10)
        self.db.add(SaraName(node="0x" + "aa" * 32, label="rohas", wallet_id=self.wallet.id, status="registered", expiry=expiry))
        self.db.commit()

        first = names_indexer.check_expiring_names(self.db)
        second = names_indexer.check_expiring_names(self.db)  # same expiry, must not re-fire or crash

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(self.db.query(DomainEvent).filter_by(event_type="sara_name.expiring_soon").count(), 1)

    def test_reminder_skips_names_outside_the_window(self):
        far_expiry = datetime.utcnow() + timedelta(days=90)
        self.db.add(SaraName(node="0x" + "bb" * 32, label="farfuture", wallet_id=self.wallet.id, status="registered", expiry=far_expiry))
        self.db.commit()
        self.assertEqual(names_indexer.check_expiring_names(self.db), 0)

    def test_reminder_skips_already_expired_names(self):
        past_expiry = datetime.utcnow() - timedelta(days=1)
        self.db.add(SaraName(node="0x" + "cc" * 32, label="expired", wallet_id=self.wallet.id, status="registered", expiry=past_expiry))
        self.db.commit()
        self.assertEqual(names_indexer.check_expiring_names(self.db), 0)


class ResolverTests(Stage7TestCase):
    def test_raw_address_resolves_directly(self):
        resolved = resolve_recipient_input(self.db, ADDR_B, "polygon")
        self.assertEqual(resolved.source, "address")

    def test_address_book_nickname_resolves(self):
        self.db.add(AddressBook(nickname="zara", address=ADDR_B, chain="evm"))
        self.db.commit()
        resolved = resolve_recipient_input(self.db, "zara", "polygon")
        self.assertEqual(resolved.source, "address_book")
        self.assertEqual(resolved.address, ADDR_B)

    def test_address_book_takes_priority_over_sara_names(self):
        self.db.add(AddressBook(nickname="rohas", address=ADDR_B, chain="evm"))
        self.db.commit()
        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", "0x" + "44" * 20), \
             patch.object(sara_names, "resolve", return_value={"name": "rohas", "owner": ADDR_A, "expiry": 0}) as mocked:
            resolved = resolve_recipient_input(self.db, "rohas", "polygon")
        self.assertEqual(resolved.source, "address_book")
        mocked.assert_not_called()  # never even tries Sara Names once the address book matched

    def test_sara_name_resolves_when_no_address_book_entry(self):
        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", "0x" + "44" * 20), \
             patch.object(sara_names, "resolve", return_value={"name": "rohas", "owner": ADDR_A, "expiry": 0}):
            resolved = resolve_recipient_input(self.db, "rohas", "polygon")
        self.assertEqual(resolved.source, "sara_name")
        self.assertEqual(resolved.address, ADDR_A)

    def test_unresolvable_input_returns_none_not_a_guess(self):
        with patch.object(settings, "SARA_NAME_REGISTRAR_ADDRESS", ""):
            resolved = resolve_recipient_input(self.db, "not-a-real-anything-xyz", "polygon")
        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
