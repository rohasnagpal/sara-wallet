from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, UniqueConstraint
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
    __tablename__ = "address_book"
    id         = Column(Integer, primary_key=True, index=True)
    nickname   = Column(String, unique=True, nullable=False)
    address    = Column(String, nullable=False)
    chain      = Column(String, default="evm")
    created_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id         = Column(Integer, primary_key=True, index=True)
    wallet_id  = Column(Integer, nullable=False)
    chain      = Column(String)
    tx_hash    = Column(String)
    to_address = Column(String)
    amount     = Column(Float)
    token      = Column(String, default="native")
    status     = Column(String, default="pending")   # pending | submitted | confirmed | failed
    reference  = Column(String, nullable=True, index=True)  # invoice/reference ID, set when paying a payment request
    timestamp  = Column(DateTime, default=datetime.utcnow)

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
    note       = Column(String, default="")
    status     = Column(String, default="pending")   # pending | paid | cancelled
    matched_tx_hash = Column(String, nullable=True)  # set when auto-reconciliation finds a matching transfer
    created_at = Column(DateTime, default=datetime.utcnow)
