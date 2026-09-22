from datetime import datetime, timedelta
from decimal import Decimal
import io
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import AccountingClassification, Base, CostLot, Disposal, Transaction, Wallet
from app.routers import accounting, ledger
from app.services import accounting_matcher, cost_basis

NET = "polygon"
TOKEN = "USDC"


class AccountingTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = Session()
        self.wallet_a = Wallet(name="A", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.wallet_b = Wallet(name="B", chain="evm", address="0x" + "22" * 20, encrypted_key="x")
        self.db.add_all([self.wallet_a, self.wallet_b])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def make_tx(self, *, wallet, direction, amount, decimals=6, token=TOKEN, network=NET,
                status="confirmed", tx_hash=None, category="transfer", fiat_usd_value=None,
                fee_raw=None, fee_token=None, when=None, note=None) -> Transaction:
        amount_raw = str(int(Decimal(amount) * (Decimal(10) ** decimals)))
        tx = Transaction(
            wallet_id=wallet.id, chain="evm", network=network, token=token, direction=direction,
            status=status, tx_hash=tx_hash,
            # incoming money comes from someone else; outgoing goes to someone else
            from_address=("0x" + "99" * 20) if direction == "incoming" else wallet.address,
            to_address=wallet.address if direction == "incoming" else "0x" + "99" * 20,
            amount=float(amount), amount_raw=amount_raw, decimals=decimals, category=category,
            fiat_usd_value=fiat_usd_value, fee_raw=fee_raw, fee_token=fee_token,
            timestamp=when or datetime(2026, 1, 1), note=note,
        )
        self.db.add(tx)
        self.db.commit()
        return tx


class MatchingTests(AccountingTestCase):
    def test_internal_transfer_matched_via_shared_tx_hash(self):
        out_tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="4", tx_hash="0xmove")
        in_tx = self.make_tx(wallet=self.wallet_b, direction="incoming", amount="4", tx_hash="0xmove")
        matched = accounting_matcher.match_internal_transfers_and_swaps(self.db)
        self.assertEqual(matched, 1)
        out_cls = self.db.query(AccountingClassification).filter_by(transaction_id=out_tx.id).first()
        in_cls = self.db.query(AccountingClassification).filter_by(transaction_id=in_tx.id).first()
        self.assertEqual(out_cls.classification, "transfer")
        self.assertTrue(out_cls.is_internal_transfer)
        self.assertTrue(in_cls.is_internal_transfer)
        self.assertEqual(out_cls.match_group_id, in_cls.match_group_id)
        self.assertEqual(out_cls.match_confidence, "confirmed")

    def test_unmatched_single_leg_is_left_unclassified(self):
        self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="1", tx_hash="0xlonely")
        accounting_matcher.match_internal_transfers_and_swaps(self.db)
        self.assertEqual(self.db.query(AccountingClassification).count(), 0)

    def test_swap_legs_matched_and_both_classified_swap(self):
        sell = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="10", token="USDC",
                             tx_hash="0xswap", category="swap")
        buy = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="5", token="POL",
                            decimals=18, tx_hash="0xswap", category="transfer")
        matched = accounting_matcher.match_internal_transfers_and_swaps(self.db)
        self.assertEqual(matched, 1)
        sell_cls = self.db.query(AccountingClassification).filter_by(transaction_id=sell.id).first()
        buy_cls = self.db.query(AccountingClassification).filter_by(transaction_id=buy.id).first()
        self.assertEqual(sell_cls.classification, "swap")
        self.assertEqual(buy_cls.classification, "swap")
        self.assertFalse(sell_cls.is_internal_transfer)


class FifoCostBasisTests(AccountingTestCase):
    def test_partial_disposal_reduces_lot_and_realizes_gain(self):
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10.00",
                      when=datetime(2026, 1, 1), tx_hash="0xacq1")
        disposal_tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="3", fiat_usd_value="3.30",
                                    when=datetime(2026, 1, 2), tx_hash="0xdisp1")
        result = cost_basis.rebuild_lots(self.db, TOKEN, NET)
        self.assertEqual(result["lots_created"], 1)
        self.assertEqual(result["disposals_created"], 1)
        lot = self.db.query(CostLot).filter_by(token=TOKEN, network=NET).one()
        self.assertEqual(Decimal(lot.remaining_raw) / Decimal(10 ** 6), Decimal("7"))
        disposal = self.db.query(Disposal).filter_by(disposal_transaction_id=disposal_tx.id).one()
        self.assertEqual(Decimal(disposal.cost_basis_usd), Decimal("3.00"))
        self.assertEqual(Decimal(disposal.proceeds_usd), Decimal("3.30"))
        self.assertEqual(Decimal(disposal.realized_gain_usd), Decimal("0.30"))

    def test_disposal_spanning_two_lots(self):
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10.00",
                      when=datetime(2026, 1, 1), tx_hash="0xacq1")
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="5", fiat_usd_value="5.00",
                      when=datetime(2026, 1, 2), tx_hash="0xacq2")
        disposal_tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="12", fiat_usd_value="12.00",
                                    when=datetime(2026, 1, 3), tx_hash="0xdisp1")
        cost_basis.rebuild_lots(self.db, TOKEN, NET)
        disposals = self.db.query(Disposal).filter_by(disposal_transaction_id=disposal_tx.id).all()
        self.assertEqual(len(disposals), 2)  # 10 from lot1, 2 from lot2
        total_qty = sum(int(d.quantity_raw) for d in disposals)
        self.assertEqual(total_qty, 12_000000)
        total_cost = sum(Decimal(d.cost_basis_usd) for d in disposals)
        self.assertEqual(total_cost, Decimal("10.00") + Decimal("2") * (Decimal("5.00") / Decimal("5")))

    def test_fee_reduces_realized_gain(self):
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10.00",
                      when=datetime(2026, 1, 1), tx_hash="0xacq1")
        disposal_tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="10", fiat_usd_value="11.00",
                                    when=datetime(2026, 1, 2), tx_hash="0xdisp1",
                                    fee_raw=str(10**16), fee_token="POL")  # 0.01 POL
        with patch("app.tools.market.coingecko.get_historical_price", return_value={"price": 0.5}):
            cost_basis.rebuild_lots(self.db, TOKEN, NET)
        disposal = self.db.query(Disposal).filter_by(disposal_transaction_id=disposal_tx.id).one()
        self.assertEqual(Decimal(disposal.fee_usd), Decimal("0.005"))
        self.assertEqual(Decimal(disposal.realized_gain_usd), Decimal("11.00") - Decimal("10.00") - Decimal("0.005"))

    def test_internal_transfer_moves_lot_without_disposal(self):
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10.00",
                      when=datetime(2026, 1, 1), tx_hash="0xacq1")
        self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="4", tx_hash="0xmove",
                      when=datetime(2026, 1, 2))
        self.make_tx(wallet=self.wallet_b, direction="incoming", amount="4", tx_hash="0xmove",
                      when=datetime(2026, 1, 2))
        accounting_matcher.match_internal_transfers_and_swaps(self.db)
        result = cost_basis.rebuild_lots(self.db, TOKEN, NET)
        self.assertEqual(result["disposals_created"], 0)
        lot_a = self.db.query(CostLot).filter_by(wallet_id=self.wallet_a.id).one()
        lot_b = self.db.query(CostLot).filter_by(wallet_id=self.wallet_b.id).one()
        self.assertEqual(Decimal(lot_a.remaining_raw) / Decimal(10 ** 6), Decimal("6"))
        self.assertEqual(Decimal(lot_b.quantity_raw) / Decimal(10 ** 6), Decimal("4"))
        self.assertEqual(Decimal(lot_b.acquisition_cost_usd), Decimal("4.00"))
        self.assertEqual(lot_b.acquired_at, datetime(2026, 1, 1))  # original acquisition date preserved

    def test_disposal_with_no_history_creates_synthetic_lot_and_warning(self):
        disposal_tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="5", fiat_usd_value="5.00",
                                    when=datetime(2026, 1, 1), tx_hash="0xdisp1")
        result = cost_basis.rebuild_lots(self.db, TOKEN, NET)
        self.assertTrue(any("no matching purchase record" in w for w in result["warnings"]))
        disposal = self.db.query(Disposal).filter_by(disposal_transaction_id=disposal_tx.id).one()
        self.assertEqual(Decimal(disposal.cost_basis_usd), Decimal("0"))
        self.assertEqual(Decimal(disposal.realized_gain_usd), Decimal("5.00"))
        lot = self.db.query(CostLot).filter_by(source="unknown_opening_balance").one()
        self.assertEqual(lot.remaining_raw, "0")

    def test_rebuild_is_deterministic(self):
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10.00",
                      when=datetime(2026, 1, 1), tx_hash="0xacq1")
        self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="3", fiat_usd_value="3.30",
                      when=datetime(2026, 1, 2), tx_hash="0xdisp1")
        first = cost_basis.rebuild_lots(self.db, TOKEN, NET)
        first_total_realized = sum(Decimal(d.realized_gain_usd) for d in self.db.query(Disposal).all())
        second = cost_basis.rebuild_lots(self.db, TOKEN, NET)
        second_total_realized = sum(Decimal(d.realized_gain_usd) for d in self.db.query(Disposal).all())
        self.assertEqual(first["lots_created"], second["lots_created"])
        self.assertEqual(first["disposals_created"], second["disposals_created"])
        self.assertEqual(first_total_realized, second_total_realized)


class ReportTests(AccountingTestCase):
    def test_data_quality_report_detects_each_category(self):
        unpriced = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="1", fiat_usd_value=None,
                                 tx_hash="0xunpriced")
        failed = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="1", status="failed",
                               tx_hash="0xfailed")
        self.make_tx(wallet=self.wallet_a, direction="incoming", amount="1", fiat_usd_value="1.00", tx_hash="0xdup")
        dup2 = Transaction(wallet_id=self.wallet_a.id, chain="evm", network=NET, token=TOKEN, direction="incoming",
                            status="confirmed", tx_hash="0xdup", amount=1.0, amount_raw="1000000", decimals=6,
                            fiat_usd_value="1.00", timestamp=datetime(2026, 1, 1))
        self.db.add(dup2)
        incomplete = Transaction(wallet_id=self.wallet_a.id, chain="evm", network=NET, token=TOKEN,
                                  direction="incoming", status="confirmed", amount=2.0, amount_raw=None,
                                  timestamp=datetime(2026, 1, 1))
        self.db.add(incomplete)
        self.db.commit()

        report = accounting.data_quality_report(self.db)
        self.assertIn(unpriced.id, report["unpriced_transaction_ids"])
        self.assertIn(failed.id, report["failed_transaction_ids"])
        self.assertIn(incomplete.id, report["incomplete_transaction_ids"])
        self.assertEqual(report["counts"]["duplicated_groups"], 1)
        # everything here has no classification row, so all are uncategorised
        self.assertGreaterEqual(report["counts"]["uncategorised"], 4)

    def test_internal_transfers_excluded_from_income_expense_totals(self):
        income_tx = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="100", fiat_usd_value="100.00",
                                  tx_hash="0xincome")
        expense_tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="20", fiat_usd_value="20.00",
                                   tx_hash="0xexpense")
        transfer_out = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="50", fiat_usd_value="50.00",
                                     tx_hash="0xtransfer")
        transfer_in = self.make_tx(wallet=self.wallet_b, direction="incoming", amount="50", fiat_usd_value="50.00",
                                    tx_hash="0xtransfer")
        accounting.update_classification(income_tx.id, accounting.ClassificationUpdate(classification="income"), self.db)
        accounting.update_classification(expense_tx.id, accounting.ClassificationUpdate(classification="expense"), self.db)
        accounting_matcher.match_internal_transfers_and_swaps(self.db)  # classifies the transfer pair as internal

        report = accounting.income_expense_report(
            None, None, None, None, None, None, None, None, self.db,
        )
        self.assertEqual(Decimal(report["income_total_usd"]), Decimal("100.00"))
        self.assertEqual(Decimal(report["expense_total_usd"]), Decimal("20.00"))
        self.assertNotIn(transfer_out.id, report["income_transaction_ids"] + report["expense_transaction_ids"])
        self.assertNotIn(transfer_in.id, report["income_transaction_ids"] + report["expense_transaction_ids"])


class AutoLabelTests(AccountingTestCase):
    """The report must work without anyone hand-labelling transactions: the
    category Sara records at creation decides what clearly counts."""

    def _report(self):
        return accounting.income_expense_report(None, None, None, None, None, None, None, None, self.db)

    def _ids(self, report):
        return set(report["income_transaction_ids"]), set(report["expense_transaction_ids"])

    def test_categories_decide_money_in_and_money_out(self):
        invoice = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="100", fiat_usd_value="100", category="invoice_payment", tx_hash="0x1")
        airdrop_in = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="5", fiat_usd_value="5", category="airdrop", tx_hash="0x2")
        batch = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="30", fiat_usd_value="30", category="batch_payment", tx_hash="0x3")
        send = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="20", fiat_usd_value="20", category="transfer", tx_hash="0x4")
        report = self._report()
        income, expense = self._ids(report)
        self.assertEqual(income, {invoice.id, airdrop_in.id})
        self.assertEqual(expense, {batch.id, send.id})
        self.assertEqual(Decimal(report["net_usd"]), Decimal("55"))

    def test_uncertain_rows_are_not_counted(self):
        plain_receive = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10", category="transfer", tx_hash="0x5")
        swap = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="10", fiat_usd_value="10", category="swap", tx_hash="0x6")
        contract = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="1", fiat_usd_value="1", category="contract_interaction", tx_hash="0x7")
        income, expense = self._ids(self._report())
        self.assertFalse({plain_receive.id, swap.id, contract.id} & (income | expense))

    def test_sending_to_your_own_wallet_is_never_an_expense(self):
        tx = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="50", fiat_usd_value="50", category="transfer", tx_hash="0x8")
        tx.to_address = self.wallet_b.address
        self.db.commit()
        income, expense = self._ids(self._report())  # no Reconcile run
        self.assertNotIn(tx.id, income | expense)

    def test_a_label_you_set_beats_the_automatic_one(self):
        receive = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="10", fiat_usd_value="10", category="transfer", tx_hash="0x9")
        accounting.update_classification(receive.id, accounting.ClassificationUpdate(classification="income"), self.db)
        self.assertIn(receive.id, self._ids(self._report())[0])
        # setting it back to "unknown" hands the decision back to the category
        accounting.update_classification(receive.id, accounting.ClassificationUpdate(classification="unknown"), self.db)
        self.assertNotIn(receive.id, self._ids(self._report())[0])

    def test_ledger_shows_how_each_row_counts_and_who_decided(self):
        auto = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="3", fiat_usd_value="3", category="batch_payment", tx_hash="0xc")
        manual = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="4", fiat_usd_value="4", category="transfer", tx_hash="0xd")
        accounting.update_classification(manual.id, accounting.ClassificationUpdate(classification="income"), self.db)
        rows = {r["id"]: r for r in ledger.list_ledger(None, None, None, None, None, 100, self.db)["transactions"]}
        self.assertEqual((rows[auto.id]["accounting_label"], rows[auto.id]["accounting_label_source"]), ("expense", "auto"))
        self.assertEqual((rows[manual.id]["accounting_label"], rows[manual.id]["accounting_label_source"]), ("income", "you"))

    def test_data_check_only_flags_what_cannot_be_worked_out(self):
        known = self.make_tx(wallet=self.wallet_a, direction="outgoing", amount="1", fiat_usd_value="1", category="batch_payment", tx_hash="0xa")
        unknown = self.make_tx(wallet=self.wallet_a, direction="incoming", amount="1", fiat_usd_value="1", category="transfer", tx_hash="0xb")
        flagged = accounting.data_quality_report(self.db)["uncategorised_transaction_ids"]
        self.assertNotIn(known.id, flagged)
        self.assertIn(unknown.id, flagged)


class ExportTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool
        from app.core.session_auth import require_session
        from app.db.session import get_db

        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.db = Session()
        self.wallet = Wallet(name="Treasury", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.commit()
        tx = Transaction(
            wallet_id=self.wallet.id, chain="evm", network=NET, token=TOKEN, direction="incoming",
            status="confirmed", tx_hash="0xexport", amount=1.23, amount_raw="1234567", decimals=6,
            fiat_usd_value="1.23", note="=cmd|'/c calc'!A1", timestamp=datetime(2026, 1, 1),
        )
        self.db.add(tx)
        self.db.commit()

        app = FastAPI()
        app.include_router(accounting.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_session] = lambda: None
        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()

    def test_csv_export_neutralizes_formula_injection(self):
        resp = self.client.get("/api/accounting/exports/transactions.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("'=cmd|", resp.text)   # neutralized with a leading apostrophe
        self.assertNotIn("\n=cmd|", resp.text)  # never appears as a raw leading formula

    def test_xlsx_export_round_trips_exact_amount(self):
        from openpyxl import load_workbook
        resp = self.client.get("/api/accounting/exports/transactions.xlsx")
        self.assertEqual(resp.status_code, 200)
        wb = load_workbook(io.BytesIO(resp.content))
        ws = wb.active
        header = [c.value for c in ws[1]]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        self.assertEqual(len(rows), 1)
        row = dict(zip(header, rows[0]))
        self.assertEqual(row["amount_raw"], "1234567")
        self.assertEqual(row["decimals"], "6")  # every export cell is an explicit string (formula-injection guard)
        self.assertEqual(Decimal(row["amount_raw"]) / (Decimal(10) ** int(row["decimals"])), Decimal("1.234567"))


if __name__ == "__main__":
    unittest.main()
