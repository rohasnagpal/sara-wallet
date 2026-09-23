"""Small, dependency-free schema migration runner for Sara's local SQLite DB.

SQLAlchemy's ``create_all`` creates new tables but deliberately does not alter
existing ones.  Sara is distributed as a local application, so keeping the
migrator in-process avoids requiring users to install a separate CLI while
still giving every schema change a durable version and transaction boundary.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db.models import SchemaMigration

log = logging.getLogger("sara.migrations")


def _column(engine: Engine, table: str, name: str) -> bool:
    return name in {c["name"] for c in inspect(engine).get_columns(table)}


def _add_columns(engine: Engine, table: str, columns: dict[str, str]) -> None:
    for name, sql_type in columns.items():
        if _column(engine, table, name):
            continue
        # Table and column identifiers are constants in this module, never
        # request data. SQLite cannot bind identifiers as parameters.
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def _index(engine: Engine, name: str, table: str, columns: tuple[str, ...], *, unique: bool = False) -> None:
    if name in {i["name"] for i in inspect(engine).get_indexes(table)}:
        return
    qualifier = "UNIQUE " if unique else ""
    with engine.begin() as conn:
        conn.execute(text(f"CREATE {qualifier}INDEX {name} ON {table} ({', '.join(columns)})"))


def _migration_001_legacy_payment_fields(engine: Engine) -> None:
    _add_columns(engine, "transactions", {"reference": "VARCHAR"})
    _add_columns(engine, "payment_requests", {"matched_tx_hash": "VARCHAR"})
    _index(
        engine, "uq_payment_requests_chain_network_txhash", "payment_requests",
        ("chain", "network", "matched_tx_hash"), unique=True,
    )


def _migration_002_transaction_foundation(engine: Engine) -> None:
    _add_columns(engine, "transactions", {
        "network": "VARCHAR",
        "from_address": "VARCHAR",
        "amount_raw": "VARCHAR",
        "decimals": "INTEGER",
        "direction": "VARCHAR",
        "category": "VARCHAR",
        "counterparty": "VARCHAR",
        "note": "TEXT",
        "fee_raw": "VARCHAR",
        "fee_token": "VARCHAR",
        "block_number": "INTEGER",
        "block_hash": "VARCHAR",
        "confirmations": "INTEGER NOT NULL DEFAULT 0",
        "confirmed_at": "DATETIME",
        "last_checked_at": "DATETIME",
        "failure_reason": "TEXT",
        "fiat_usd_value": "VARCHAR",
        "fiat_inr_value": "VARCHAR",
        "valuation_source": "VARCHAR",
        "valued_at": "DATETIME",
    })
    _index(engine, "ix_transactions_network", "transactions", ("network",))
    _index(engine, "ix_transactions_status_checked", "transactions", ("status", "last_checked_at"))
    _index(engine, "ix_transactions_chain_network_hash", "transactions", ("chain", "network", "tx_hash"))


def _migration_003_wallet_intelligence(engine: Engine) -> None:
    _add_columns(engine, "transactions", {"tags": "TEXT"})


def _migration_004_activity_identity(engine: Engine) -> None:
    _add_columns(engine, "transactions", {"external_id": "VARCHAR"})
    _index(engine, "uq_transactions_external_id", "transactions", ("external_id",), unique=True)


def _migration_005_invoicing(engine: Engine) -> None:
    _add_columns(engine, "payment_requests", {
        "amount_raw": "VARCHAR", "decimals": "INTEGER", "customer_name": "VARCHAR",
        "customer_email": "VARCHAR", "description": "TEXT", "due_date": "DATETIME",
        "payment_address": "VARCHAR", "merchant_client_id": "INTEGER",
    })
    _index(engine, "ix_payment_requests_merchant_client_id", "payment_requests", ("merchant_client_id",))


def _migration_006_payment_safety(engine: Engine) -> None:
    _add_columns(engine, "payment_batches", {"execution_lock_acquired_at": "DATETIME"})
    _add_columns(engine, "payment_batch_items", {
        "signed_tx_raw": "TEXT", "reserved_nonce": "INTEGER",
    })
    _add_columns(engine, "spending_policies", {"principal_id": "VARCHAR"})


def _migration_007_batch_item_tags_and_notes(engine: Engine) -> None:
    _add_columns(engine, "payment_batch_items", {"note": "TEXT", "tags": "TEXT"})


def _migration_008_unify_directory_and_counterparties(engine: Engine) -> None:
    """Schema-only: no data is copied from the (now-unused) counterparties
    table — the user confirmed existing test counterparties don't need to
    survive this unification. See app/db/models.py's AddressBook/
    Counterparty docstrings. Inline DEFAULTs (as migration 002 already
    does for transactions.confirmations) backfill existing rows without a
    separate UPDATE pass."""
    _add_columns(engine, "address_book", {
        "type": "VARCHAR NOT NULL DEFAULT 'friend'",
        "display_name": "VARCHAR",
        "tags": "TEXT",
        "notes": "TEXT",
        "active": "BOOLEAN NOT NULL DEFAULT 1",
    })


def _drop_column(engine: Engine, table: str, name: str) -> None:
    if not _column(engine, table, name):
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {name}"))
    except Exception:
        # DROP COLUMN needs SQLite 3.35+ (bundled with every supported Python).
        # Don't stop startup over a cleanup; the column just stays.
        log.warning("Could not drop %s.%s; leaving it in place", table, name, exc_info=True)


def _migration_009_drop_dual_control(engine: Engine) -> None:
    """Maker/checker approval was removed but databases created while it
    existed still carry spending_policies.require_dual_control as NOT NULL
    with no default. The model no longer writes it, so every new policy insert
    failed with an IntegrityError."""
    _drop_column(engine, "spending_policies", "require_dual_control")


def _migration_010_paywall_preview_message(engine: Engine) -> None:
    """x402_paywall_pages existed for a short time without this column — an
    install that started the app in that window needs it added explicitly
    (create_all only creates missing tables, it never alters existing ones)."""
    _add_columns(engine, "x402_paywall_pages", {"preview_message": "TEXT"})


def _migration_011_fetched_content_file_path(engine: Engine) -> None:
    """x402_fetched_content originally stored the fetched body directly in
    a NOT NULL `body` column. Switching to file-based storage renamed that
    to `file_path` in the model, but an install that already created this
    table under the old schema never got the new column — every insert and
    read against it failed outright (NOT NULL body / no such column
    file_path) rather than degrading gracefully, since create_all only
    creates missing tables. Drop is safe: this table only ever holds
    disposable, regenerable fetch history, never a wallet key or anything
    that can't be re-fetched (at the cost of a fresh payment)."""
    _add_columns(engine, "x402_fetched_content", {"file_path": "TEXT"})
    _drop_column(engine, "x402_fetched_content", "body")


def _migration_012_paywall_facilitator(engine: Engine) -> None:
    """x402_paywall_pages predates the "circle" facilitator option — every
    live-mode row created before this column existed used Coinbase's CDP
    facilitator exclusively (it was the only one available). Backfill it
    explicitly so an existing live page's code still regenerates with the
    facilitator it actually was built for, instead of silently defaulting
    to "circle" (config that row was never given) the next time its code
    is fetched. Test-mode rows are left null - the column is live-mode
    only, matching the model."""
    _add_columns(engine, "x402_paywall_pages", {"facilitator": "TEXT"})
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE x402_paywall_pages SET facilitator = 'cdp' WHERE mode = 'live' AND facilitator IS NULL"
        ))


def _migration_013_wallet_seeds(engine: Engine) -> None:
    """wallet_seeds is a brand-new table (SQLAlchemy's create_all handles
    that on its own, no migration needed) - only wallets needs altering,
    since it predates seed-derived wallets. Both columns stay NULL for
    every wallet that already exists (imported keys, or anything created
    before this feature) - that's their normal, permanent state, not
    unmigrated data."""
    _add_columns(engine, "wallets", {"seed_id": "INTEGER", "derivation_index": "INTEGER"})


MIGRATIONS: tuple[tuple[str, Callable[[Engine], None]], ...] = (
    ("001_legacy_payment_fields", _migration_001_legacy_payment_fields),
    ("002_transaction_foundation", _migration_002_transaction_foundation),
    ("003_wallet_intelligence", _migration_003_wallet_intelligence),
    ("004_activity_identity", _migration_004_activity_identity),
    ("005_invoicing", _migration_005_invoicing),
    ("006_payment_safety", _migration_006_payment_safety),
    ("007_batch_item_tags_and_notes", _migration_007_batch_item_tags_and_notes),
    ("008_unify_directory_and_counterparties", _migration_008_unify_directory_and_counterparties),
    ("009_drop_dual_control", _migration_009_drop_dual_control),
    ("010_paywall_preview_message", _migration_010_paywall_preview_message),
    ("011_fetched_content_file_path", _migration_011_fetched_content_file_path),
    ("012_paywall_facilitator", _migration_012_paywall_facilitator),
    ("013_wallet_seeds", _migration_013_wallet_seeds),
)


def run_migrations(engine: Engine) -> None:
    """Apply every unapplied migration, stopping startup on any failure."""
    SchemaMigration.__table__.create(bind=engine, checkfirst=True)
    with engine.connect() as conn:
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}

    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        try:
            migration(engine)
            with engine.begin() as conn:
                conn.execute(
                    SchemaMigration.__table__.insert().values(version=version, applied_at=datetime.utcnow())
                )
        except Exception:
            log.exception("Failed to apply schema migration %s", version)
            raise
