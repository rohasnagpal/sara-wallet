import unittest

from app.tools.payments.links import parse_eip681

RECIPIENT = "0x" + "11" * 20
POLYGON_USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"


class ParseEip681Tests(unittest.TestCase):
    def test_usdc_transfer_from_invoice_qr(self):
        data = parse_eip681(f"ethereum:{POLYGON_USDC}@137/transfer?address={RECIPIENT}&uint256=1500000")
        self.assertEqual((data["network"], data["token"], data["amount"], data["to"]),
                         ("polygon", "USDC", "1.5", RECIPIENT))

    def test_native_transfer(self):
        data = parse_eip681(f"ethereum:{RECIPIENT}@8453?value=100000000000000000")
        self.assertEqual((data["network"], data["token"], data["amount"]), ("base", "ETH", "0.1"))

    def test_rejects_untrusted_token_contract(self):
        usdt = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
        with self.assertRaises(ValueError):
            parse_eip681(f"ethereum:{usdt}@1/transfer?address={RECIPIENT}&uint256=5")

    def test_rejects_usdc_address_from_another_chain(self):
        with self.assertRaises(ValueError):
            parse_eip681(f"ethereum:{POLYGON_USDC}@8453/transfer?address={RECIPIENT}&uint256=5")

    def test_rejects_unknown_chain_bad_amount_and_non_payment_uris(self):
        for bad in (
            f"ethereum:{RECIPIENT}@56?value=5",
            f"ethereum:{RECIPIENT}@1?value=0",
            f"ethereum:{RECIPIENT}@1?value=1.5",
            f"ethereum:{RECIPIENT}@1",
            f"ethereum:{POLYGON_USDC}@137/approve?address={RECIPIENT}&uint256=5",
            "https://example.com",
        ):
            with self.subTest(uri=bad), self.assertRaises(ValueError):
                parse_eip681(bad)


if __name__ == "__main__":
    unittest.main()
