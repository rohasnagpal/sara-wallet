from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean
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
    chain         = Column(String, nullable=False)   # "evm" | "solana"
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
    status     = Column(String, default="pending")   # pending | confirmed | failed
    timestamp  = Column(DateTime, default=datetime.utcnow)
