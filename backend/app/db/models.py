from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, LargeBinary, UniqueConstraint
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    role       = Column(String)
    content    = Column(Text)
    timestamp  = Column(DateTime, default=datetime.utcnow)

class Config(Base):
    __tablename__ = "config"
    key   = Column(String, primary_key=True)
    value = Column(String)

class Wallet(Base):
    __tablename__ = "wallets"
    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String, unique=True, nullable=False)
    chain         = Column(String, nullable=False)   # "evm" | "solana" | "tron"
    address       = Column(String, nullable=False)
    encrypted_key = Column(Text, nullable=False)     # AES-256-GCM, hex-encoded blob
    created_at    = Column(DateTime, default=datetime.utcnow)

class AddressBook(Base):
    """The single "who do I know" list — a free, local nickname/address
    entry, optionally typed (vendor/customer/employee/etc.) for use in
    batches, payroll and invoicing. Used to be split across this table and
    a separate Counterparty table; unified here so there's one list, not
    two overlapping ones. Counterparty itself is kept in the schema
    (unused) rather than dropped, so nothing already in it is destroyed."""
    __tablename__ = "address_book"
    id           = Column(Integer, primary_key=True, index=True)
    nickname     = Column(String, unique=True, nullable=False)
    address      = Column(String, nullable=False)
    chain        = Column(String, default="evm")
    type         = Column(String, nullable=False, default="friend")  # vendor | customer | employee | contractor | friend | other
    display_name = Column(String, nullable=True)  # friendlier label; falls back to nickname (minus .sara) if blank
    tags         = Column(Text, nullable=True, default="[]")  # JSON string array
    notes        = Column(Text, nullable=True)
    active       = Column(Boolean, nullable=False, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id         = Column(Integer, primary_key=True, index=True)
    wallet_id  = Column(Integer, nullable=False)
    chain      = Column(String)
    network    = Column(String, nullable=True, index=True)
    external_id = Column(String, nullable=True, unique=True, index=True)
    tx_hash    = Column(String)
    from_address = Column(String, nullable=True)
    to_address = Column(String)
    # ``amount`` is retained for backwards compatibility with existing Sara
    # databases/UI. New accounting code must use amount_raw + decimals: a
    # binary Float cannot exactly represent token amounts.
    amount     = Column(Float)
    amount_raw = Column(String, nullable=True)
    decimals   = Column(Integer, nullable=True)
    token      = Column(String, default="native")
    status     = Column(String, default="pending")   # pending | submitted | confirmed | failed
    direction  = Column(String, nullable=True)        # incoming | outgoing | self
    category   = Column(String, nullable=True)
    counterparty = Column(String, nullable=True)
    note       = Column(Text, nullable=True)
    tags       = Column(Text, nullable=True)          # JSON string array
    fee_raw    = Column(String, nullable=True)
    fee_token  = Column(String, nullable=True)
    block_number = Column(Integer, nullable=True)
    block_hash = Column(String, nullable=True)
    confirmations = Column(Integer, nullable=False, default=0)
    confirmed_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    failure_reason = Column(Text, nullable=True)
    fiat_usd_value = Column(String, nullable=True)
    fiat_inr_value = Column(String, nullable=True)
    valuation_source = Column(String, nullable=True)
    valued_at = Column(DateTime, nullable=True)
    reference  = Column(String, nullable=True, index=True)  # invoice/reference ID, set when paying a payment request
    timestamp  = Column(DateTime, default=datetime.utcnow)


class SchemaMigration(Base):
    """Applied migration ledger; makes schema changes durable and inspectable."""
    __tablename__ = "schema_migrations"
    version    = Column(String, primary_key=True)
    applied_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DomainEvent(Base):
    """Transactional outbox used by alerts, webhooks and background jobs."""
    __tablename__ = "domain_events"
    id             = Column(Integer, primary_key=True, index=True)
    event_key      = Column(String, unique=True, nullable=False, index=True)
    event_type     = Column(String, nullable=False, index=True)
    aggregate_type = Column(String, nullable=True)
    aggregate_id   = Column(String, nullable=True)
    payload         = Column(Text, nullable=False, default="{}")
    status          = Column(String, nullable=False, default="pending", index=True)
    attempts        = Column(Integer, nullable=False, default=0)
    available_at    = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    processed_at    = Column(DateTime, nullable=True)
    last_error      = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditLog(Base):
    """Append-only, hash-chained record of security and money-moving events."""
    __tablename__ = "audit_log"
    id          = Column(Integer, primary_key=True, index=True)
    actor_type  = Column(String, nullable=False, default="system")
    actor_id    = Column(String, nullable=False, default="local")
    action      = Column(String, nullable=False, index=True)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    details     = Column(Text, nullable=False, default="{}")
    previous_hash = Column(String, nullable=True)
    entry_hash  = Column(String, unique=True, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)


class Principal(Base):
    """Local identity record reserved for business/multi-user operation."""
    __tablename__ = "principals"
    id          = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    active      = Column(Boolean, nullable=False, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)


class Role(Base):
    __tablename__ = "roles"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String, unique=True, nullable=False)
    permissions = Column(Text, nullable=False, default="[]")
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)


class PrincipalRole(Base):
    __tablename__ = "principal_roles"
    __table_args__ = (UniqueConstraint("principal_id", "role_id", name="uq_principal_role"),)
    id           = Column(Integer, primary_key=True, index=True)
    principal_id = Column(Integer, nullable=False, index=True)
    role_id      = Column(Integer, nullable=False, index=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class BalanceMonitor(Base):
    __tablename__ = "balance_monitors"
    id            = Column(Integer, primary_key=True, index=True)
    wallet_id     = Column(Integer, nullable=False, index=True)
    network       = Column(String, nullable=False)
    token         = Column(String, nullable=False)
    token_address = Column(String, nullable=True)
    decimals      = Column(Integer, nullable=False, default=18)
    condition     = Column(String, nullable=False, default="below")  # below | above
    threshold_raw = Column(String, nullable=False)
    last_value_raw = Column(String, nullable=True)
    triggered     = Column(Boolean, nullable=False, default=False)
    enabled       = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    checked_at    = Column(DateTime, nullable=True)


class AlertDestination(Base):
    __tablename__ = "alert_destinations"
    id         = Column(Integer, primary_key=True, index=True)
    kind       = Column(String, nullable=False)  # telegram | email | webhook
    target     = Column(Text, nullable=False)
    secret     = Column(Text, nullable=True)
    enabled    = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "destination_id", name="uq_alert_delivery"),)
    id             = Column(Integer, primary_key=True, index=True)
    event_id       = Column(Integer, nullable=False, index=True)
    destination_id = Column(Integer, nullable=False, index=True)
    status         = Column(String, nullable=False, default="pending")
    last_error     = Column(Text, nullable=True)
    delivered_at   = Column(DateTime, nullable=True)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id          = Column(Integer, primary_key=True, index=True)
    total_usd   = Column(String, nullable=False)
    holdings    = Column(Text, nullable=False, default="[]")
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    __table_args__ = (
        # Two concurrent reconciliation checks can both observe the same
        # matched_tx_hash as unused before either commits ("check then
        # commit" in reconcile.check_payment_request) — this constraint is
        # the actual source of truth that closes that race: one of the two
        # commits fails and is treated as "already claimed" rather than both
        # succeeding. NULL is excluded from uniqueness (standard SQL/SQLite
        # behavior), so still-pending requests are unaffected.
        UniqueConstraint("chain", "network", "matched_tx_hash",
                          name="uq_payment_requests_chain_network_txhash"),
    )
    id         = Column(Integer, primary_key=True, index=True)
    wallet_id  = Column(Integer, nullable=False)
    reference  = Column(String, unique=True, nullable=False, index=True)
    chain      = Column(String, nullable=False)
    network    = Column(String, nullable=False)
    token      = Column(String, nullable=False)
    amount     = Column(Float, nullable=False)
    amount_raw = Column(String, nullable=True)
    decimals   = Column(Integer, nullable=True)
    note       = Column(String, default="")
    customer_name = Column(String, nullable=True)
    customer_email = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    due_date    = Column(DateTime, nullable=True)
    payment_address = Column(String, nullable=True)
    merchant_client_id = Column(Integer, nullable=True, index=True)
    status     = Column(String, default="pending")   # pending | paid | overdue | cancelled
    matched_tx_hash = Column(String, nullable=True)  # set when auto-reconciliation finds a matching transfer
    created_at = Column(DateTime, default=datetime.utcnow)


class MerchantClient(Base):
    __tablename__ = "merchant_clients"
    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String, unique=True, nullable=False)
    wallet_id      = Column(Integer, nullable=False)
    api_key_prefix = Column(String, nullable=False, index=True)
    api_key_hash   = Column(String, unique=True, nullable=False)
    alert_destination_id = Column(Integer, nullable=True)
    enabled        = Column(Boolean, nullable=False, default=True)
    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProofRecord(Base):
    """Local, wallet-owned index of a BlockchainProof checkout.

    Private checkout material (including the access token, document hash and
    description) and the evidence ZIP are encrypted with Sara's in-memory
    wallet key. Public status fields remain queryable for recovery/polling.
    """
    __tablename__ = "proof_records"
    id                = Column(Integer, primary_key=True, index=True)
    checkout_id       = Column(String, unique=True, nullable=False, index=True)
    wallet_id         = Column(Integer, nullable=False, index=True)
    wallet_address    = Column(String, nullable=False)
    encrypted_details = Column(Text, nullable=False)
    encrypted_evidence = Column(LargeBinary, nullable=True)
    status            = Column(String, nullable=False, default="created", index=True)
    payment_txid      = Column(String, nullable=True)
    proof_id          = Column(String, nullable=True, index=True)
    proof_status      = Column(String, nullable=True)
    encrypted_proof   = Column(Text, nullable=True)
    last_error        = Column(Text, nullable=True)
    expires_at        = Column(String, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Counterparty(Base):
    """DEPRECATED — superseded by AddressBook's type/tags/notes/active
    columns, which unified this and the address book into one list at the
    user's request. No longer read or written by application code; kept
    here only so the table itself (and anything already in it) isn't
    dropped. Do not add new code paths against this model."""
    __tablename__ = "counterparties"
    id                 = Column(Integer, primary_key=True, index=True)
    display_name       = Column(String, nullable=False)
    type               = Column(String, nullable=False, default="vendor")  # vendor | employee | contractor
    addresses          = Column(Text, nullable=False, default="{}")  # JSON: {network: address}
    default_network    = Column(String, nullable=True)
    default_token      = Column(String, nullable=True)
    external_reference = Column(String, nullable=True)
    tags               = Column(Text, nullable=True, default="[]")  # JSON string array
    notes              = Column(Text, nullable=True)
    active             = Column(Boolean, nullable=False, default=True)
    created_at         = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PaymentBatch(Base):
    """A draft/reviewable/executable set of payment items. Batch payments,
    airdrops, payroll runs and materialized recurring obligations all share
    this one engine (see PaymentBatchItem) rather than each reimplementing
    validation/approval/execution."""
    __tablename__ = "payment_batches"
    id                     = Column(Integer, primary_key=True, index=True)
    kind                   = Column(String, nullable=False, default="payment")  # payment | airdrop | payroll | recurring
    status                 = Column(String, nullable=False, default="draft", index=True)
    # draft | awaiting_approval | approved | executing | completed | cancelled
    wallet_id              = Column(Integer, nullable=False)
    network                = Column(String, nullable=False)
    token                  = Column(String, nullable=False)
    memo                   = Column(String, nullable=True)
    execution_date         = Column(DateTime, nullable=True)
    payroll_period         = Column(String, nullable=True)
    schedule_id            = Column(Integer, nullable=True, index=True)
    created_by             = Column(String, nullable=False, default="local-owner")
    approved_by            = Column(String, nullable=True)
    approved_at            = Column(DateTime, nullable=True)
    approval_payload_hash  = Column(String, nullable=True)
    # Compare-and-swap lease: execute_batch only proceeds if it can claim this
    # token atomically, so a double-click or a second concurrent worker on the
    # same batch is rejected rather than double-sending items.
    execution_lock_token   = Column(String, nullable=True)
    execution_lock_acquired_at = Column(DateTime, nullable=True)
    created_at             = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at             = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PaymentBatchItem(Base):
    __tablename__ = "payment_batch_items"
    __table_args__ = (UniqueConstraint("batch_id", "row_index", name="uq_batch_item_row"),)
    id               = Column(Integer, primary_key=True, index=True)
    batch_id         = Column(Integer, nullable=False, index=True)
    row_index        = Column(Integer, nullable=False)
    recipient_address = Column(String, nullable=False)
    counterparty_id  = Column(Integer, nullable=True, index=True)
    amount_raw       = Column(String, nullable=False)
    decimals         = Column(Integer, nullable=False)
    reference        = Column(String, nullable=True)
    note             = Column(Text, nullable=True)
    tags             = Column(Text, nullable=True)          # JSON string array
    status           = Column(String, nullable=False, default="draft", index=True)
    # draft | validated | awaiting_approval | approved | broadcasting | submitted | confirmed | failed | cancelled
    tx_hash          = Column(String, nullable=True, unique=True, index=True)
    signed_tx_raw    = Column(Text, nullable=True)
    reserved_nonce   = Column(Integer, nullable=True)
    transaction_id   = Column(Integer, nullable=True)
    failure_reason   = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class BatchApproval(Base):
    """Approval decision trail — separate from the cached latest state on
    PaymentBatch so every approve/invalidate is preserved, not just
    overwritten by the next one."""
    __tablename__ = "batch_approvals"
    id           = Column(Integer, primary_key=True, index=True)
    batch_id     = Column(Integer, nullable=False, index=True)
    action       = Column(String, nullable=False)  # approved | invalidated
    actor        = Column(String, nullable=False)
    payload_hash = Column(String, nullable=False)
    reason       = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)


class SpendingPolicy(Base):
    """Scoped spending limits evaluated at batch prepare and again
    immediately before signing each item. Null scope fields match anything;
    the tightest matching set of active policies governs."""
    __tablename__ = "spending_policies"
    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String, nullable=False)
    wallet_id           = Column(Integer, nullable=True)
    principal_id        = Column(String, nullable=True)
    network             = Column(String, nullable=True)
    token                = Column(String, nullable=True)
    counterparty_id      = Column(Integer, nullable=True)
    destination_address  = Column(String, nullable=True)
    max_amount_raw       = Column(String, nullable=True)
    period                = Column(String, nullable=True)  # day | week | month
    period_limit_raw      = Column(String, nullable=True)
    window_start          = Column(String, nullable=True)  # "HH:MM", local to `timezone`
    window_end            = Column(String, nullable=True)
    timezone               = Column(String, nullable=False, default="UTC")
    active                = Column(Boolean, nullable=False, default=True)
    created_at             = Column(DateTime, default=datetime.utcnow, nullable=False)


class Schedule(Base):
    """A recurring payment or payroll-salary instruction. Materializes into a
    PaymentBatch per due occurrence (see ScheduleRun) — scheduling itself
    never signs anything."""
    __tablename__ = "schedules"
    id              = Column(Integer, primary_key=True, index=True)
    kind            = Column(String, nullable=False, default="recurring_payment")  # recurring_payment | payroll_salary
    wallet_id       = Column(Integer, nullable=False)
    network         = Column(String, nullable=False)
    token           = Column(String, nullable=False)
    counterparty_id = Column(Integer, nullable=True, index=True)
    recipient_address = Column(String, nullable=True)
    amount_raw      = Column(String, nullable=True)   # fixed-crypto mode
    fiat_amount     = Column(String, nullable=True)   # fiat-locked mode (quoted at each materialization)
    fiat_currency   = Column(String, nullable=True)
    memo            = Column(String, nullable=True)
    rrule           = Column(Text, nullable=False)     # dateutil.rrule text form
    timezone        = Column(String, nullable=False, default="UTC")
    start_date      = Column(DateTime, nullable=False)
    end_date        = Column(DateTime, nullable=True)
    next_run_at     = Column(DateTime, nullable=False, index=True)
    active          = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ScheduleRun(Base):
    """One row per due occurrence — the unique constraint is what guarantees
    exactly one materialized payment per occurrence across restarts/retries."""
    __tablename__ = "schedule_runs"
    __table_args__ = (UniqueConstraint("schedule_id", "occurrence_key", name="uq_schedule_run_occurrence"),)
    id              = Column(Integer, primary_key=True, index=True)
    schedule_id     = Column(Integer, nullable=False, index=True)
    occurrence_key  = Column(String, nullable=False)
    occurrence_date = Column(DateTime, nullable=False)
    status          = Column(String, nullable=False, default="materialized")  # materialized | skipped
    batch_id        = Column(Integer, nullable=True)
    skip_reason     = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)


class PayrollProfile(Base):
    """Thin identity so /api/payroll/people has its own list distinct from
    the general counterparty directory, without duplicating counterparty
    fields."""
    __tablename__ = "payroll_profiles"
    id              = Column(Integer, primary_key=True, index=True)
    counterparty_id = Column(Integer, nullable=False, unique=True)
    schedule_id     = Column(Integer, nullable=True)
    active          = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)


class AccountingClassification(Base):
    """Editable accounting metadata for one Transaction, kept separate from
    the original chain facts on Transaction itself so re-classifying a
    transaction never touches the authoritative on-chain record."""
    __tablename__ = "accounting_classifications"
    id                = Column(Integer, primary_key=True, index=True)
    transaction_id    = Column(Integer, nullable=False, unique=True, index=True)
    classification    = Column(String, nullable=False, default="unknown")
    # income | expense | transfer | swap | fee | payroll | invoice_receipt |
    # airdrop | acquisition | disposal | unknown
    counterparty_id   = Column(Integer, nullable=True, index=True)
    project           = Column(String, nullable=True)
    client            = Column(String, nullable=True)
    is_internal_transfer = Column(Boolean, nullable=False, default=False)
    match_group_id    = Column(String, nullable=True, index=True)
    match_confidence  = Column(String, nullable=True)  # confirmed | suggested
    notes             = Column(Text, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CostLot(Base):
    """One acquisition lot for FIFO cost-basis tracking. `remaining_raw` is
    decremented as disposals/moves consume it; wallet_id changes when an
    internal transfer moves the lot rather than realising a gain."""
    __tablename__ = "cost_lots"
    id                       = Column(Integer, primary_key=True, index=True)
    wallet_id                = Column(Integer, nullable=False, index=True)
    token                    = Column(String, nullable=False, index=True)
    network                  = Column(String, nullable=False, index=True)
    acquisition_transaction_id = Column(Integer, nullable=True)  # null for a synthetic unknown_opening_balance lot
    acquired_at              = Column(DateTime, nullable=False)
    quantity_raw             = Column(String, nullable=False)
    remaining_raw            = Column(String, nullable=False)
    decimals                 = Column(Integer, nullable=False)
    acquisition_cost_usd     = Column(String, nullable=False)
    source                   = Column(String, nullable=False, default="unknown")
    # purchase | swap_in | transfer_in | airdrop | payroll_receipt |
    # invoice_receipt | unknown_opening_balance
    created_at               = Column(DateTime, default=datetime.utcnow, nullable=False)


class TokenDeployment(Base):
    """An ERC-20 token deployed through Sara's token creator (compiled from
    the pinned templates under contracts/, never arbitrary Solidity)."""
    __tablename__ = "token_deployments"
    id                  = Column(Integer, primary_key=True, index=True)
    template_id         = Column(String, nullable=False)  # fixed_supply | mintable_burnable_capped
    wallet_id           = Column(Integer, nullable=False)  # deployer
    network             = Column(String, nullable=False)
    contract_address    = Column(String, nullable=True, index=True)  # set once the deployment confirms
    owner_address        = Column(String, nullable=False)  # initial recipient / mint authority
    name                = Column(String, nullable=False)
    symbol              = Column(String, nullable=False)
    decimals            = Column(Integer, nullable=False)
    initial_supply_raw  = Column(String, nullable=False)
    cap_raw             = Column(String, nullable=True)  # null for fixed_supply
    compiler_version    = Column(String, nullable=False)
    source_sha256       = Column(String, nullable=False)
    deployment_tx_hash  = Column(String, nullable=False, unique=True, index=True)
    status              = Column(String, nullable=False, default="submitted")  # submitted | confirmed | failed
    created_at          = Column(DateTime, default=datetime.utcnow, nullable=False)
    confirmed_at        = Column(DateTime, nullable=True)


class Disposal(Base):
    """One FIFO allocation of a disposal transaction against a CostLot. A
    single disposal transaction may span multiple lots, hence one row per
    (disposal transaction, lot) pair rather than one row per transaction."""
    __tablename__ = "disposals"
    id                     = Column(Integer, primary_key=True, index=True)
    disposal_transaction_id = Column(Integer, nullable=False, index=True)
    lot_id                 = Column(Integer, nullable=False, index=True)
    quantity_raw           = Column(String, nullable=False)
    proceeds_usd           = Column(String, nullable=False)
    cost_basis_usd         = Column(String, nullable=False)
    fee_usd                = Column(String, nullable=True)
    realized_gain_usd      = Column(String, nullable=False)
    method                 = Column(String, nullable=False, default="FIFO")
    created_at             = Column(DateTime, default=datetime.utcnow, nullable=False)


class RiskScreening(Base):
    """A recorded screening check — provider, time, result and evidence
    identifiers only, never unsupported allegation text (Stage 5.6)."""
    __tablename__ = "risk_screenings"
    id          = Column(Integer, primary_key=True, index=True)
    address     = Column(String, nullable=False, index=True)
    network     = Column(String, nullable=False)
    provider    = Column(String, nullable=False)  # "unconfigured" until a real adapter is set up
    result      = Column(String, nullable=False)  # clear | flagged | unavailable
    evidence    = Column(Text, nullable=True)      # JSON list of provider evidence identifiers
    reason      = Column(Text, nullable=True)
    checked_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at  = Column(DateTime, nullable=True)


class RiskReview(Base):
    """Audited manual override of a flagged/unavailable screening result —
    never an invisible bypass."""
    __tablename__ = "risk_reviews"
    id          = Column(Integer, primary_key=True, index=True)
    address     = Column(String, nullable=False, index=True)
    network     = Column(String, nullable=False)
    decision    = Column(String, nullable=False)  # approved | rejected
    actor       = Column(String, nullable=False)
    reason      = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)


class SaraName(Base):
    """Local tracking of a Sara Name this Sara instance's wallets have
    interacted with — powers the portfolio/expiry-warning view. The
    registry contract remains the actual source of truth for
    ownership/expiry; this table is a local index over it, not a second
    authority (Stage 6.5)."""
    __tablename__ = "sara_names"
    id                  = Column(Integer, primary_key=True, index=True)
    node                = Column(String, unique=True, nullable=False, index=True)  # 0x-prefixed namehash
    label               = Column(String, nullable=False)
    parent_node         = Column(String, nullable=True)  # null for a root name
    wallet_id           = Column(Integer, nullable=False, index=True)
    status              = Column(String, nullable=False, default="pending")
    # pending | committed | registered | renewed | transferred_away | expired
    expiry              = Column(DateTime, nullable=True)
    commit_tx_hash       = Column(String, nullable=True)
    register_tx_hash     = Column(String, nullable=True)
    last_renew_tx_hash   = Column(String, nullable=True)
    last_transfer_tx_hash = Column(String, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class IndexerCursor(Base):
    """Saved block-cursor position for an off-chain event indexer (Stage 7
    reliability) — lets a resync after downtime pick up exactly where it
    left off instead of rescanning from genesis, and lets the indexer
    withhold a block from being treated as final until it has enough
    confirmations to be reorg-safe."""
    __tablename__ = "indexer_cursors"
    id          = Column(Integer, primary_key=True, index=True)
    contract    = Column(String, unique=True, nullable=False, index=True)
    last_block  = Column(Integer, nullable=False, default=0)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SaraNameRecordCache(Base):
    """Bounded-TTL local cache of a signed off-chain name record. Never
    authoritative on its own — every read revalidates the cached record's
    signature against freshly-fetched on-chain owner/recordSigner/epoch
    before trusting it (app.tools.names.eip712_records.verify_record)."""
    __tablename__ = "sara_name_record_cache"
    id              = Column(Integer, primary_key=True, index=True)
    node            = Column(String, unique=True, nullable=False, index=True)
    sequence        = Column(Integer, nullable=False)
    record_epoch    = Column(Integer, nullable=False)
    signed_payload  = Column(Text, nullable=False)  # JSON: the NameRecord fields as signed
    signature       = Column(String, nullable=False)
    cached_at       = Column(DateTime, default=datetime.utcnow, nullable=False)
    revalidated_at  = Column(DateTime, nullable=True)
