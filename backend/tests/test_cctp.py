"""Tests for Sara's CCTP v2 integration (native USDC burn-and-mint,
alongside LI.FI's aggregated bridging — see app/tools/trading/cctp.py's
module docstring for why both exist and what each is for).

AddressTriangulationTests reproduces, as a permanent regression check, the
same live verification done before writing this module: that
TokenMessengerV2 and MessageTransmitterV2 really do sit at the claimed
address on every one of Sara's five networks, and cross-reference each
other correctly. AttestationApiTests replays a real, historical CCTP burn
transaction against Circle's live Iris API — since attestations for an
already-settled transaction never change, this is a stable integration
check, not a flaky one. Both skip automatically without network access.

Everything else mocks web3 and exercises the actual signing/safety logic.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.tools.trading import cctp

REAL_BASE_BURN_TX = "0x4e90fb42f59394ee011c7d2b0f91bee9e1af810e26f54d62393fca9c00207b03"


class AddressTriangulationTests(unittest.TestCase):
    def _skip_if_unreachable(self, w3):
        try:
            w3.eth.get_block_number()
        except Exception:
            self.skipTest("no network access to verify live contract state")

    def test_every_networks_contracts_agree_on_domain_and_cross_reference(self):
        from web3 import Web3
        from app.chains.evm import _RPC

        transmitter_abi = [{"inputs": [], "name": "localDomain",
                             "outputs": [{"type": "uint32"}], "stateMutability": "view", "type": "function"}]
        messenger_abi = [{"inputs": [], "name": "localMessageTransmitter",
                           "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}]

        for network in cctp.SUPPORTED_NETWORKS:
            with self.subTest(network=network):
                w3 = Web3(Web3.HTTPProvider(_RPC[network]))
                self._skip_if_unreachable(w3)

                transmitter = w3.eth.contract(
                    address=Web3.to_checksum_address(cctp.MESSAGE_TRANSMITTER), abi=transmitter_abi,
                )
                self.assertEqual(
                    transmitter.functions.localDomain().call(), cctp.DOMAINS[network],
                    f"{network}: MessageTransmitterV2's own reported domain doesn't match",
                )

                messenger = w3.eth.contract(
                    address=Web3.to_checksum_address(cctp.TOKEN_MESSENGER), abi=messenger_abi,
                )
                self.assertEqual(
                    messenger.functions.localMessageTransmitter().call(),
                    Web3.to_checksum_address(cctp.MESSAGE_TRANSMITTER),
                    f"{network}: TokenMessengerV2 doesn't point at the configured MessageTransmitterV2",
                )


class AttestationApiTests(unittest.TestCase):
    def test_a_real_settled_burn_still_returns_its_attestation(self):
        try:
            import httpx
            httpx.get("https://iris-api.circle.com/v2/messages/6", params={"transactionHash": REAL_BASE_BURN_TX}, timeout=5)
        except Exception:
            self.skipTest("no network access to Circle's Iris API")
        attestation = cctp.fetch_attestation("base", REAL_BASE_BURN_TX)
        self.assertIsNotNone(attestation)
        self.assertTrue(attestation.message.startswith("0x"))
        self.assertTrue(attestation.attestation.startswith("0x"))

    def test_an_unknown_transaction_returns_none_not_an_error(self):
        try:
            import httpx
            httpx.get("https://iris-api.circle.com/v2/messages/6", timeout=5)
        except Exception:
            self.skipTest("no network access to Circle's Iris API")
        result = cctp.fetch_attestation("base", "0x" + "00" * 32)
        self.assertIsNone(result)


class AddressEncodingTests(unittest.TestCase):
    def test_address_to_bytes32_left_pads_to_32_bytes(self):
        addr = "0x" + "ab" * 20
        encoded = cctp.address_to_bytes32(addr)
        self.assertEqual(len(encoded), 32)
        self.assertEqual(encoded[:12], bytes(12))
        self.assertEqual(encoded[12:].hex(), "ab" * 20)


class UnsupportedNetworkTests(unittest.TestCase):
    def test_usdc_address_rejects_unsupported_network(self):
        with self.assertRaises(cctp.CctpError):
            cctp.usdc_address("solana")

    def test_execute_burn_rejects_unsupported_source_network(self):
        with self.assertRaises(cctp.CctpError):
            cctp.execute_burn("0xkey", "solana", "base", 1_000_000, "0x" + "11" * 20)


def _make_fake_w3(*, allowance=0, chain_id=1):
    w3 = MagicMock()
    account = MagicMock()
    account.address = "0x" + "22" * 20
    account.key = b"key"
    w3.eth.account.from_key.return_value = account
    w3.eth.get_transaction_count.return_value = 1
    w3.eth.estimate_gas.return_value = 100_000
    w3.eth.gas_price = 10 * 10**9

    receipt = MagicMock()
    w3.eth.wait_for_transaction_receipt.return_value = receipt
    sent_hash = MagicMock()
    sent_hash.hex.return_value = "0xhash"
    w3.eth.send_raw_transaction.return_value = sent_hash
    signed = MagicMock()
    signed.raw_transaction = b"raw"
    w3.eth.account.sign_transaction.return_value = signed

    erc20 = MagicMock()
    erc20.functions.allowance.return_value.call.return_value = allowance
    erc20.functions.approve.return_value.build_transaction.return_value = {"data": "0xapprove"}

    messenger = MagicMock()
    messenger.functions.depositForBurn.return_value.build_transaction.return_value = {"data": "0xburn"}

    transmitter = MagicMock()
    transmitter.functions.receiveMessage.return_value.build_transaction.return_value = {"data": "0xmint"}

    def contract(address, abi):
        if abi is cctp._ERC20_ABI:
            return erc20
        if abi is cctp._TOKEN_MESSENGER_ABI:
            return messenger
        return transmitter
    w3.eth.contract.side_effect = contract
    return w3, account, erc20, messenger, transmitter


class ExecuteBurnTests(unittest.TestCase):
    def test_rejects_zero_amount(self):
        with self.assertRaises(cctp.CctpError):
            cctp.execute_burn("0xkey", "base", "arbitrum", 0, "0x" + "11" * 20)

    def test_approves_exactly_the_burned_amount_when_needed(self):
        w3, account, erc20, messenger, transmitter = _make_fake_w3(allowance=0)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"base": 8453}):
            tx_hash = cctp.execute_burn("0xkey", "base", "arbitrum", 1_000_000, "0x" + "11" * 20)
        erc20.functions.approve.assert_called_once()
        _, approved_amount = erc20.functions.approve.call_args[0]
        self.assertEqual(approved_amount, 1_000_000)
        self.assertEqual(tx_hash, "0xhash")

    def test_skips_approval_when_allowance_already_sufficient(self):
        w3, account, erc20, messenger, transmitter = _make_fake_w3(allowance=5_000_000)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"base": 8453}):
            cctp.execute_burn("0xkey", "base", "arbitrum", 1_000_000, "0x" + "11" * 20)
        erc20.functions.approve.assert_not_called()

    def test_deposit_for_burn_uses_the_correct_domain_and_fast_finality_by_default(self):
        w3, account, erc20, messenger, transmitter = _make_fake_w3(allowance=5_000_000)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"base": 8453}):
            cctp.execute_burn("0xkey", "base", "arbitrum", 1_000_000, "0x" + "11" * 20)
        args = messenger.functions.depositForBurn.call_args[0]
        amount, destination_domain, mint_recipient, burn_token, destination_caller, max_fee, finality = args
        self.assertEqual(amount, 1_000_000)
        self.assertEqual(destination_domain, cctp.DOMAINS["arbitrum"])
        self.assertEqual(mint_recipient, cctp.address_to_bytes32("0x" + "11" * 20))
        self.assertEqual(destination_caller, bytes(32))  # permissionless - anyone may complete it
        self.assertEqual(finality, cctp.FINALITY_FAST)

    def test_standard_finality_used_when_fast_is_false(self):
        w3, account, erc20, messenger, transmitter = _make_fake_w3(allowance=5_000_000)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"base": 8453}):
            cctp.execute_burn("0xkey", "base", "arbitrum", 1_000_000, "0x" + "11" * 20, fast=False)
        finality = messenger.functions.depositForBurn.call_args[0][6]
        self.assertEqual(finality, cctp.FINALITY_STANDARD)

    def test_refuses_when_locally_estimated_fee_is_excessive(self):
        w3, account, erc20, messenger, transmitter = _make_fake_w3(allowance=5_000_000)
        w3.eth.gas_price = 10**18
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"base": 8453}):
            with self.assertRaises(cctp.CctpError):
                cctp.execute_burn("0xkey", "base", "arbitrum", 1_000_000, "0x" + "11" * 20)


class ExecuteMintTests(unittest.TestCase):
    def test_passes_decoded_message_and_attestation_bytes_through(self):
        w3, account, erc20, messenger, transmitter = _make_fake_w3()
        attestation = cctp.Attestation(message="0x" + "ab" * 10, attestation="0x" + "cd" * 10)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"arbitrum": 42161}):
            tx_hash = cctp.execute_mint("0xkey", "arbitrum", attestation)
        message_arg, attestation_arg = transmitter.functions.receiveMessage.call_args[0]
        self.assertEqual(message_arg, bytes.fromhex("ab" * 10))
        self.assertEqual(attestation_arg, bytes.fromhex("cd" * 10))
        self.assertEqual(tx_hash, "0xhash")

    def test_never_touches_the_erc20_approval_path(self):
        """Minting needs no approval - the recipient is just receiving
        freshly-minted USDC, not spending anything."""
        w3, account, erc20, messenger, transmitter = _make_fake_w3()
        attestation = cctp.Attestation(message="0x" + "ab" * 10, attestation="0x" + "cd" * 10)
        with patch("app.chains.evm.get_web3", return_value=w3), \
             patch("app.chains.evm._CHAIN_IDS", {"arbitrum": 42161}):
            cctp.execute_mint("0xkey", "arbitrum", attestation)
        erc20.functions.approve.assert_not_called()
        erc20.functions.allowance.assert_not_called()


class WaitForAttestationTests(unittest.TestCase):
    def test_returns_as_soon_as_fetch_succeeds(self):
        expected = cctp.Attestation(message="0xaa", attestation="0xbb")
        with patch.object(cctp, "fetch_attestation", return_value=expected) as fetch:
            result = cctp.wait_for_attestation("base", "0xtx", max_wait_seconds=5, poll_interval_seconds=0.01)
        self.assertEqual(result, expected)
        fetch.assert_called_once()

    def test_returns_none_after_the_timeout_instead_of_blocking_forever(self):
        with patch.object(cctp, "fetch_attestation", return_value=None), \
             patch("time.sleep", return_value=None):
            result = cctp.wait_for_attestation("base", "0xtx", max_wait_seconds=0.05, poll_interval_seconds=0.01)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
