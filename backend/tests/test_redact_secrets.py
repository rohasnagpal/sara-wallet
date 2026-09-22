"""A pasted private key or seed phrase must not sit in clear in sara.db
forever. app/core/redact.py redacts secret-looking text before it's
persisted to ChatMessage, without touching what the live request actually
sees (intent parsing / the model get the original text)."""
import asyncio
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.redact import redact_for_storage, scrub_existing_chat_history
from app.db.models import Base, ChatMessage
from app.routers import chat

MNEMONIC_12 = "abandon ability able about above absent absorb abstract absurd abuse access accident"
PRIVATE_KEY = "0x" + "ab" * 32
TX_HASH = "0x" + "cd" * 32


class RedactionUnitTests(unittest.TestCase):
    def test_a_twelve_word_bip39_run_is_redacted(self):
        out = redact_for_storage(f"here is my wallet seed: {MNEMONIC_12} - please keep it safe", role="user")
        self.assertNotIn("abandon", out)
        self.assertIn("[redacted", out)
        self.assertIn("please keep it safe", out)  # surrounding text survives

    def test_fewer_than_twelve_words_is_left_alone(self):
        eleven = " ".join(MNEMONIC_12.split()[:11])
        out = redact_for_storage(f"words: {eleven}", role="user")
        self.assertEqual(out, f"words: {eleven}")

    def test_ordinary_sentences_never_false_positive_on_the_word_count_alone(self):
        prose = "please send the money to my friend today and also check the balance before you do that thanks"
        self.assertEqual(redact_for_storage(prose, role="user"), prose)

    def test_a_pasted_private_key_is_redacted_from_a_user_message(self):
        out = redact_for_storage(f"my private key is {PRIVATE_KEY}, can you import it?", role="user")
        self.assertNotIn(PRIVATE_KEY, out)
        self.assertIn("[redacted", out)

    def test_saras_own_tx_hash_in_an_assistant_reply_is_kept(self):
        # Real tx hashes are exactly this shape (0x + 64 hex) and Sara's own
        # replies legitimately contain them as a receipt — only a *user's*
        # typed message is checked against this pattern, since we can't
        # otherwise tell a private key from a tx hash by shape alone.
        text = f"Broadcast **10 USDC**.\nTx hash: `{TX_HASH}`"
        self.assertEqual(redact_for_storage(text, role="assistant"), text)

    def test_the_same_shaped_string_is_redacted_when_the_user_types_it(self):
        out = redact_for_storage(f"what happened to {TX_HASH}?", role="user")
        self.assertNotIn(TX_HASH, out)

    def test_openrouter_style_api_keys_are_redacted(self):
        out = redact_for_storage("use sk-abcdefghijklmnopqrstuvwxyz012345", role="user")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", out)

    def test_telegram_bot_tokens_are_redacted(self):
        token = "123456789:AAFabcDEF-ghijklmno_pqrstuvwxyz012345"
        out = redact_for_storage(f"bot token {token}", role="user")
        self.assertNotIn(token, out)

    def test_ordinary_send_commands_are_completely_unaffected(self):
        msg = "send 10 USDC to zara.sara"
        self.assertEqual(redact_for_storage(msg, role="user"), msg)

    def test_empty_string_is_a_no_op(self):
        self.assertEqual(redact_for_storage("", role="user"), "")


class ChatPersistenceIntegrationTests(unittest.TestCase):
    """Drives the real /chat handler end to end: what actually lands in the
    ChatMessage table, not just the helper function in isolation."""

    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()
        chat._pending.clear()
        self.addCleanup(chat._pending.clear)

    def tearDown(self):
        self.db.close()

    def test_a_pasted_seed_phrase_is_not_stored_in_clear(self):
        message = f"lost access, here's my seed phrase: {MNEMONIC_12}"
        req = chat.ChatRequest(message=message, session_id="s1", history=[])
        # The persist-then-commit at the top of chat() runs synchronously
        # before any streaming/LLM work, so we don't need to (and shouldn't)
        # drive the response body for this — just await the coroutine.
        asyncio.run(chat.chat(req, self.db))
        row = self.db.query(ChatMessage).filter_by(session_id="s1", role="user").one()
        self.assertNotIn("abandon", row.content)
        self.assertIn("[redacted", row.content)
        self.assertIn("lost access", row.content)


class HistoricScrubTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)()

    def tearDown(self):
        self.db.close()

    def test_scrubs_secrets_left_over_from_before_this_filter_existed(self):
        dirty = ChatMessage(session_id="s1", role="user", content=f"my seed: {MNEMONIC_12}")
        clean = ChatMessage(session_id="s1", role="assistant", content=f"Tx hash: `{TX_HASH}`")
        self.db.add_all([dirty, clean])
        self.db.commit()

        changed = scrub_existing_chat_history(self.db)

        self.assertEqual(changed, 1)
        self.db.refresh(dirty); self.db.refresh(clean)
        self.assertNotIn("abandon", dirty.content)
        self.assertEqual(clean.content, f"Tx hash: `{TX_HASH}`")  # untouched, correctly

    def test_running_it_twice_changes_nothing_the_second_time(self):
        self.db.add(ChatMessage(session_id="s1", role="user", content=f"key {PRIVATE_KEY}"))
        self.db.commit()
        first = scrub_existing_chat_history(self.db)
        second = scrub_existing_chat_history(self.db)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    def test_a_clean_history_is_a_no_op(self):
        self.db.add(ChatMessage(session_id="s1", role="user", content="send 10 USDC to zara.sara"))
        self.db.commit()
        self.assertEqual(scrub_existing_chat_history(self.db), 0)


if __name__ == "__main__":
    unittest.main()
