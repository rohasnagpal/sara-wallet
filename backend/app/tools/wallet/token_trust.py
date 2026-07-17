"""Typo correction against Sara's trusted-token allowlists.

Sara never resolves a token symbol to an arbitrary on-chain contract — every
send/swap/bridge only accepts symbols present in the hardcoded, developer-
verified address lists in paraswap.py, jupiter.py, and lifi.py. This module
just helps recover from a misspelled *symbol* (e.g. "UDST" -> "USDT") without
ever substituting a different asset for the one the user actually typed.
"""
import difflib

# Real, distinct assets that must NEVER be auto-corrected to a different
# trusted symbol, even though plain edit-distance can put them well past the
# fuzzy cutoff. Verified directly: SequenceMatcher gives "USDE" vs "USDT" the
# *identical* 0.750 ratio as the genuine typo "UDST" vs "USDT" — both are a
# single-character edit apart on a 4-letter string, so no cutoff threshold
# can separate "typo of a trusted symbol" from "different real coin that
# happens to be similarly spelled." Silently "fixing" USDE to USDT would
# move the user's funds into a different real stablecoin than the one they
# typed, not correct a misspelling — this denylist is what stops that,
# independent of whatever cutoff is in play.
_PROTECTED_DISTINCT_SYMBOLS = {
    "USDE", "DAI", "BUSD", "TUSD", "USDD", "FDUSD", "PYUSD", "USDP", "GUSD", "LUSD", "USDS", "FRAX",
}


def fuzzy_correct(symbol: str, valid_symbols: list[str], cutoff: float = 0.72) -> str | None:
    """Return the closest trusted symbol if `symbol` looks like a typo of
    exactly one entry in `valid_symbols`, else None. An exact match returns
    None (nothing to correct), and so does any symbol that's itself a known,
    distinct real asset (see _PROTECTED_DISTINCT_SYMBOLS) — those are never
    treated as typos of something else, no matter how close the spelling."""
    sym = symbol.upper()
    valid = [v.upper() for v in valid_symbols]
    if sym in valid or sym in _PROTECTED_DISTINCT_SYMBOLS:
        return None
    matches = difflib.get_close_matches(sym, valid, n=1, cutoff=cutoff)
    return matches[0] if matches else None
