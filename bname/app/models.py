import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


class BNameStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    expired = "expired"
    deleted = "deleted"


class RecordStatus(str, enum.Enum):
    active = "active"
    deleted = "deleted"


class RecordType(str, enum.Enum):
    wallet = "WALLET"
    url = "URL"
    text = "TEXT"
    ipfs = "IPFS"
    contenthash = "CONTENTHASH"
    social = "SOCIAL"
    email = "EMAIL"


class AnchorType(str, enum.Enum):
    none = "none"
    hash = "hash"
    full = "full"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    consumed = "consumed"
    rejected = "rejected"


class PaymentPurpose(str, enum.Enum):
    register = "register"
    renew = "renew"
    anchor_hash = "anchor_hash"
    anchor_full = "anchor_full"


class BName(Base):
    __tablename__ = "bnames"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    owner_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[BNameStatus] = mapped_column(Enum(BNameStatus), nullable=False, default=BNameStatus.active)
    current_zone_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    records: Mapped[list["BNameRecord"]] = relationship(back_populates="bname", cascade="all, delete-orphan")
    zone_versions: Mapped[list["BNameZoneVersion"]] = relationship(back_populates="bname", cascade="all, delete-orphan")


class BNameRecord(Base):
    __tablename__ = "bname_records"
    __table_args__ = (UniqueConstraint("bname_id", "subname", "record_type", "record_key", name="uq_record_slot"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    bname_id: Mapped[str] = mapped_column(ForeignKey("bnames.id"), nullable=False, index=True)
    subname: Mapped[str] = mapped_column(String(255), nullable=False, default="@")
    record_type: Mapped[RecordType] = mapped_column(Enum(RecordType), nullable=False)
    record_key: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    record_value: Mapped[str] = mapped_column(Text, nullable=False)
    ttl: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus), nullable=False, default=RecordStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    bname: Mapped[BName] = relationship(back_populates="records")
    versions: Mapped[list["BNameRecordVersion"]] = relationship(back_populates="record", cascade="all, delete-orphan")


class BNameRecordVersion(Base):
    __tablename__ = "bname_record_versions"
    __table_args__ = (UniqueConstraint("record_id", "version", name="uq_record_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    record_id: Mapped[str] = mapped_column(ForeignKey("bname_records.id"), nullable=False, index=True)
    bname_id: Mapped[str] = mapped_column(ForeignKey("bnames.id"), nullable=False, index=True)
    subname: Mapped[str] = mapped_column(String(255), nullable=False)
    record_type: Mapped[RecordType] = mapped_column(Enum(RecordType), nullable=False)
    record_key: Mapped[str] = mapped_column(String(64), nullable=False)
    record_value: Mapped[str] = mapped_column(Text, nullable=False)
    ttl: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    record_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    signature: Mapped[str | None] = mapped_column(Text)
    signer_address: Mapped[str | None] = mapped_column(String(64), index=True)
    anchor_type: Mapped[AnchorType] = mapped_column(Enum(AnchorType), nullable=False, default=AnchorType.none)
    anchor_tx_hash: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    record: Mapped[BNameRecord] = relationship(back_populates="versions")


class BNameZoneVersion(Base):
    __tablename__ = "bname_zone_versions"
    __table_args__ = (UniqueConstraint("bname_id", "zone_version", name="uq_zone_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    bname_id: Mapped[str] = mapped_column(ForeignKey("bnames.id"), nullable=False, index=True)
    zone_version: Mapped[int] = mapped_column(Integer, nullable=False)
    zone_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    anchor_type: Mapped[AnchorType] = mapped_column(Enum(AnchorType), nullable=False, default=AnchorType.none)
    anchor_tx_hash: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bname: Mapped[BName] = relationship(back_populates="zone_versions")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    tx_hash: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    payer_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount: Mapped[str] = mapped_column(String(80), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[PaymentPurpose] = mapped_column(Enum(PaymentPurpose), nullable=False)
    bname_id: Mapped[str | None] = mapped_column(ForeignKey("bnames.id"), index=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), nullable=False, default=PaymentStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Nonce(Base):
    __tablename__ = "nonces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_address: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    nonce: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    app_name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
