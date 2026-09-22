"""x402 has no session or receipt concept: a paid /fetch's response body
was previously shown once and then lost forever, even though the user had
already paid for it. Hybrid storage fixes this: a DB row for fast/
searchable metadata, the actual body as a plain file on disk (no
encryption — an explicit, deliberate choice for this data). These tests
drive the real /x402/fetch, /x402/fetched, /x402/fetched/{id} and
DELETE /x402/fetched/{id} endpoints against a real temp directory.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, SpendingPolicy, Wallet, X402FetchedContent
from app.routers import x402
from app.tools.payments import x402_client

POLYGON_USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
PAY_TO = "0x" + "22" * 20


class ExtensionMappingTests(unittest.TestCase):
    def test_known_content_types_get_a_friendly_extension(self):
        self.assertEqual(x402._extension_for("application/json"), ".json")
        self.assertEqual(x402._extension_for("application/json; charset=utf-8"), ".json")
        self.assertEqual(x402._extension_for("text/html"), ".html")

    def test_unknown_or_missing_content_type_falls_back_to_txt(self):
        self.assertEqual(x402._extension_for("application/octet-stream"), ".txt")
        self.assertEqual(x402._extension_for(None), ".txt")


class FetchedContentEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.dir_patch = patch.object(x402, "FETCHED_CONTENT_DIR", Path(self.tmp_dir))
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()
        self.wallet = Wallet(name="Main", chain="evm", address="0x" + "11" * 20, encrypted_key="x")
        self.db.add(self.wallet)
        self.db.add(SpendingPolicy(name="x402-auto", network="polygon", token="USDC",
                                    destination_address=PAY_TO, max_amount_raw="1000000", active=True))
        self.db.add(SpendingPolicy(name="x402-auto-testnet", network="base-sepolia", token="USDC",
                                    destination_address=PAY_TO, max_amount_raw="1000000", active=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _paid_fetch(self, body_text="the premium content", content_type="text/plain",
                     network="polygon", tx_hash="0xabc"):
        probed = x402_client.X402Requirement(network=network, asset=POLYGON_USDC, amount_raw="10000", pay_to=PAY_TO)
        paid_result = x402_client.X402Result(
            status_code=200, body_text=body_text, content_type=content_type, paid=True, tx_hash=tx_hash,
        )
        fetch_body = x402.X402FetchBody(wallet_id=self.wallet.id, network=network, url="https://example.com/article")
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch.object(x402_client, "probe", AsyncMock(return_value=probed)), \
             patch("app.tools.wallet.encrypt.decrypt_key", return_value="k"), \
             patch.object(x402_client, "pay_and_fetch", AsyncMock(return_value=paid_result)):
            return asyncio.run(x402.fetch(fetch_body, self.db))

    def test_a_paid_fetch_writes_a_file_and_a_metadata_row(self):
        result = self._paid_fetch(body_text="hello premium world")
        self.assertIsNotNone(result["content_id"])
        row = self.db.query(X402FetchedContent).filter_by(id=result["content_id"]).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.url, "https://example.com/article")
        saved_file = Path(self.tmp_dir) / row.file_path
        self.assertTrue(saved_file.exists())
        self.assertEqual(saved_file.read_text(), "hello premium world")

    def test_testnet_paid_fetches_are_saved_even_though_not_ledgered(self):
        result = self._paid_fetch(network="base-sepolia")
        self.assertIsNotNone(result["content_id"])
        row = self.db.query(X402FetchedContent).filter_by(id=result["content_id"]).first()
        self.assertIsNone(row.transaction_id)  # testnet never gets a ledger row
        self.assertIsNotNone(row.file_path)

    def test_free_unpaid_fetches_are_not_saved(self):
        with patch("app.tools.wallet.lock.is_unlocked", return_value=True), \
             patch.object(x402_client, "probe", AsyncMock(return_value=None)):
            import httpx
            with patch.object(httpx, "AsyncClient") as mock_client:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.text = "free stuff"
                mock_resp.headers = {"content-type": "text/plain"}
                instance = mock_client.return_value.__aenter__.return_value
                instance.request = AsyncMock(return_value=mock_resp)
                fetch_body = x402.X402FetchBody(wallet_id=self.wallet.id, network="polygon", url="https://example.com/free")
                result = asyncio.run(x402.fetch(fetch_body, self.db))
        self.assertFalse(result["paid"])
        self.assertEqual(self.db.query(X402FetchedContent).count(), 0)

    def test_list_endpoint_returns_metadata_without_reading_files(self):
        self._paid_fetch(body_text="content A")
        self._paid_fetch(body_text="content B")
        items = x402.list_fetched_content(wallet_id=None, db=self.db)["items"]
        self.assertEqual(len(items), 2)
        self.assertNotIn("body", items[0])

    def test_get_endpoint_reads_the_file_content(self):
        result = self._paid_fetch(body_text="the actual article text")
        got = x402.get_fetched_content(result["content_id"], self.db)
        self.assertEqual(got["body"], "the actual article text")
        self.assertEqual(got["url"], "https://example.com/article")

    def test_get_endpoint_404s_for_unknown_id(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            x402.get_fetched_content(999999, self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_endpoint_410s_if_the_file_was_removed_outside_sara(self):
        from fastapi import HTTPException
        result = self._paid_fetch()
        row = self.db.query(X402FetchedContent).filter_by(id=result["content_id"]).first()
        (Path(self.tmp_dir) / row.file_path).unlink()
        with self.assertRaises(HTTPException) as ctx:
            x402.get_fetched_content(result["content_id"], self.db)
        self.assertEqual(ctx.exception.status_code, 410)

    def test_delete_removes_both_the_row_and_the_file(self):
        result = self._paid_fetch()
        row = self.db.query(X402FetchedContent).filter_by(id=result["content_id"]).first()
        saved_file = Path(self.tmp_dir) / row.file_path
        self.assertTrue(saved_file.exists())
        x402.delete_fetched_content(result["content_id"], self.db)
        self.assertEqual(self.db.query(X402FetchedContent).count(), 0)
        self.assertFalse(saved_file.exists())

    def test_delete_unknown_id_404s(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            x402.delete_fetched_content(999999, self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_saved_filenames_never_escape_the_content_directory(self):
        """The list/get/delete paths join row.file_path onto
        FETCHED_CONTENT_DIR — a saved row must only ever carry a bare
        filename, never something with path-traversal segments."""
        result = self._paid_fetch()
        row = self.db.query(X402FetchedContent).filter_by(id=result["content_id"]).first()
        self.assertNotIn("/", row.file_path)
        self.assertNotIn("..", row.file_path)


if __name__ == "__main__":
    unittest.main()
