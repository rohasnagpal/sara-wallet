"""Redacts secret-looking text before it's persisted to ChatMessage.

Chat history is stored in plain text in sara.db (see docs/privacy.md); a
user who pastes a private key or seed phrase into the chat box — believing
they're "giving it to Sara", or just pasting the wrong thing — would
otherwise have it sitting there indefinitely, in clear, for anyone who
later reads the database (a backup, a stolen machine, a forensic copy).
Generated wallet keys are passphrase-encrypted; a pasted one deserves the
same protection, not less.

This never touches what the model or the intent parser actually sees —
only the copy that gets written to the ChatMessage row. Only a copy of the
message that would be *stored* is redacted; the live request is handled
against the original text.
"""
from __future__ import annotations

import re

_HEX64 = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_TELEGRAM_BOT_TOKEN = re.compile(r"\b\d{6,10}:[A-Za-z0-9_-]{30,}\b")

_WORDLIST: set[str] | None = None


def _wordlist() -> set[str]:
    global _WORDLIST
    if _WORDLIST is None:
        # eth-account (already a pinned dependency, used elsewhere for
        # signing) ships the standard 2048-word BIP-39 English list —
        # avoids hand-copying it, and it's the exact list a real wallet's
        # seed phrase is drawn from.
        from eth_account.hdaccount.mnemonic import get_wordlist
        _WORDLIST = set(get_wordlist("english"))
    return _WORDLIST


def _redact_mnemonics(text: str) -> str:
    """Replaces any run of 12+ consecutive words that are all valid BIP-39
    words. Ordinary sentences essentially never satisfy this — it takes 12
    real, specific words in a row from a fixed 2048-word list — so this
    only ever fires on something that looks like an actual seed phrase."""
    words = _wordlist()
    tokens = text.split(" ")
    out: list[str] = []
    run: list[str] = []

    def flush():
        if len(run) >= 12:
            out.append("[redacted — looked like a seed phrase]")
        else:
            out.extend(run)
        run.clear()

    for token in tokens:
        bare = token.strip(".,!?;:()\"'").lower()
        if bare and bare in words:
            run.append(token)
        else:
            flush()
            out.append(token)
    flush()
    return " ".join(out)


def redact_for_storage(text: str, *, role: str) -> str:
    """`role` is the ChatMessage role ("user" or "assistant"). Only a
    user's own typed text is checked against the tx-hash-shaped pattern:
    Sara's own replies legitimately contain real `0x` + 64-hex transaction
    hashes (e.g. "Tx hash: `0x...`") in exactly that shape, and those are
    meant to be kept, not stripped from the user's own history."""
    if not text:
        return text
    redacted = _redact_mnemonics(text)
    redacted = _OPENAI_STYLE_KEY.sub("[redacted — looked like an API key]", redacted)
    redacted = _TELEGRAM_BOT_TOKEN.sub("[redacted — looked like a bot token]", redacted)
    if role == "user":
        redacted = _HEX64.sub("[redacted — looked like a private key]", redacted)
    return redacted


def scrub_existing_chat_history(db) -> int:
    """One-off maintenance pass over messages already persisted before this
    filter existed (or from a version of Sara that didn't have it): applies
    the same redaction to every row and updates only the ones it changes.
    Idempotent — safe to run more than once, and a no-op on a database
    that's already clean. Returns the number of rows changed."""
    from app.db.models import ChatMessage

    changed = 0
    for row in db.query(ChatMessage).all():
        new_content = redact_for_storage(row.content, role=row.role)
        if new_content != row.content:
            row.content = new_content
            changed += 1
    if changed:
        db.commit()
    return changed
