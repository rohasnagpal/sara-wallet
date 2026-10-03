import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AddressBook, Base
from app.tools.names.resolver import resolve_recipient_input


ADDRESS = "0x" + "33" * 20


class LocalAliasResolverTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = session()

    def tearDown(self):
        self.db.close()

    def test_raw_evm_address_resolves_directly(self):
        resolved = resolve_recipient_input(self.db, ADDRESS, "polygon")
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.source, "address")

    def test_local_sara_alias_resolves_from_directory(self):
        self.db.add(AddressBook(nickname="supplier.sara", address=ADDRESS, chain="evm"))
        self.db.commit()

        resolved = resolve_recipient_input(self.db, "SUPPLIER.SARA", "base")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.source, "address_book")
        self.assertEqual(resolved.address, ADDRESS)
        self.assertEqual(resolved.input_label, "SUPPLIER.SARA")

    def test_unknown_name_is_not_guessed_or_resolved_onchain(self):
        self.assertIsNone(resolve_recipient_input(self.db, "unknown.sara", "ethereum"))
        self.assertIsNone(resolve_recipient_input(self.db, "unknown", "ethereum"))


if __name__ == "__main__":
    unittest.main()
