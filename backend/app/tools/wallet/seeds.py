"""BIP-39/44 HD wallet derivation - one seed phrase backs up many wallets,
the same model MetaMask/Ledger/every real wallet uses, instead of each
wallet needing its own independent backup.

Deliberately separate from keygen.py's generate_evm_wallet(), which keeps
its own "no key material derived from anything but a chain library's CSPRNG
constructor" invariant for genuinely independent wallets (created fresh, or
imported from a key the user already had elsewhere). This module covers a
different, equally standard case: a wallet whose key is deterministically
derived from a CSPRNG-generated seed phrase via the standard BIP-32/44 path
- the same thing every major wallet does, not a weaker substitute for it.

Derivation was independently verified against a second, unrelated
implementation (bip_utils) before this was written - see
tests/test_wallet_seeds.py - to confirm eth_account's HD support produces
the same addresses MetaMask would for the same phrase (i.e. a genuinely
portable, standard seed phrase, not something Sara-specific)."""
from eth_account import Account
from mnemonic import Mnemonic

Account.enable_unaudited_hdwallet_features()

_WORDLIST = Mnemonic("english")
_PATH = "m/44'/60'/0'/0/{index}"


class SeedError(Exception):
    """A problem with a seed phrase, worded for the user."""


def generate_seed_phrase() -> str:
    """A fresh 24-word BIP-39 phrase (256 bits of CSPRNG entropy via the
    `mnemonic` package's own os.urandom-backed generator)."""
    return _WORDLIST.generate(strength=256)


def validate_seed_phrase(phrase: str) -> str:
    """Normalizes whitespace and checks the BIP-39 checksum. Raises
    SeedError (never a bare library exception) on anything invalid - a
    single mistyped word must be caught here, not silently accepted as a
    different, wrong wallet."""
    normalized = " ".join((phrase or "").strip().lower().split())
    if not normalized:
        raise SeedError("Recovery phrase cannot be blank")
    if not _WORDLIST.check(normalized):
        raise SeedError(
            "That recovery phrase isn't valid - check the word count and spelling "
            "(each word must be an exact BIP-39 word)."
        )
    return normalized


def derive_wallet(phrase: str, index: int) -> dict:
    """Returns {"address", "private_key"} for account `index` under this
    seed, via the standard Ethereum BIP-44 path - portable to any other
    wallet that imports the same phrase, not just Sara."""
    if index < 0:
        raise SeedError("Derivation index cannot be negative")
    acct = Account.from_mnemonic(phrase, account_path=_PATH.format(index=index))
    return {"address": acct.address, "private_key": acct.key.hex()}
