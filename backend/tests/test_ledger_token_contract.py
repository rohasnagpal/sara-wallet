import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, TokenDeployment, Transaction, Wallet
from app.routers import ledger

ADDR_A = "0x" + "aa" * 20
ADDR_B = "0x" + "bb" * 20


class LedgerTokenContractTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()
        self.db.add(Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x"))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _deploy(self, symbol, contract, tx):
        self.db.add(TokenDeployment(
            template_id="fixed_supply", wallet_id=1, network="polygon", contract_address=contract,
            owner_address="0x" + "11" * 20, name=symbol, symbol=symbol, decimals=18,
            initial_supply_raw="1", compiler_version="x", source_sha256="x", deployment_tx_hash=tx,
        ))

    def _list(self):
        return ledger.list_ledger(wallet_id=None, network=None, token=None, category=None,
                                  status=None, limit=100, db=self.db)

    def _tx(self, category, token="ROHAS1"):
        self.db.add(Transaction(wallet_id=1, chain="evm", network="polygon", tx_hash="0x" + "cc" * 32,
                                token=token, category=category, amount_raw="1", decimals=18))
        self.db.commit()

    def test_mint_links_to_the_single_matching_deployment(self):
        self._deploy("ROHAS1", ADDR_A, "0x1")
        self._tx("token_mint")
        rows = self._list()["transactions"]
        self.assertEqual(rows[0]["token_contract"], ADDR_A)

    def test_ambiguous_symbol_is_not_linked(self):
        self._deploy("ROHAS1", ADDR_A, "0x1")
        self._deploy("ROHAS1", ADDR_B, "0x2")
        self._tx("token_mint")
        self.assertNotIn("token_contract", self._list()["transactions"][0])

    def test_plain_transfer_is_not_given_a_contract(self):
        self._deploy("ROHAS1", ADDR_A, "0x1")
        self._tx("transfer")
        self.assertNotIn("token_contract", self._list()["transactions"][0])


if __name__ == "__main__":
    unittest.main()
