from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NonceRequest(BaseModel):
    owner_address: str
    purpose: str = Field(default="update_record")


class NonceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    owner_address: str
    nonce: str
    purpose: str
    expires_at: datetime


class RegisterRequest(BaseModel):
    name: str
    owner_address: str
    payment_tx_hash: str | None = None
    signature: str | None = None
    nonce: str | None = None


class RecordUpsertRequest(BaseModel):
    subname: str | None = "@"
    record_type: Literal["WALLET", "URL", "TEXT", "IPFS", "CONTENTHASH", "SOCIAL", "EMAIL"]
    record_key: str = "default"
    record_value: str
    ttl: int = Field(default=300, ge=30, le=86400)
    signature: str
    nonce: str


class AnchorRequest(BaseModel):
    anchor_type: Literal["hash", "full"]
    payment_tx_hash: str | None = None


class RecordResponse(BaseModel):
    name: str
    root: str
    subname: str
    record_type: str
    record_key: str
    record_value: str
    version: int
    ttl: int
    record_hash: str
    zone_version: int
    zone_hash: str
    anchor: dict


class NameResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    owner_address: str
    status: str
    current_zone_version: int
    created_at: datetime
    updated_at: datetime


class ZoneRecord(BaseModel):
    subname: str
    record_type: str
    record_key: str
    record_value: str
    ttl: int
    version: int


class ZoneResponse(BaseModel):
    zone: str
    owner_address: str
    zone_version: int
    zone_hash: str
    records: list[ZoneRecord]
