from sqlalchemy.orm import Session, selectinload

from app.hashing import record_hash, zone_hash
from app.models import (
    AnchorType,
    BName,
    BNameRecord,
    BNameRecordVersion,
    BNameStatus,
    BNameZoneVersion,
    RecordStatus,
    RecordType,
)
from app.validation import ValidationError, validate_record_value, validate_root_name, validate_subname


def get_active_bname(db: Session, root: str) -> BName | None:
    root = validate_root_name(root)
    return (
        db.query(BName)
        .options(selectinload(BName.records), selectinload(BName.zone_versions))
        .filter(BName.name == root, BName.status == BNameStatus.active)
        .one_or_none()
    )


def register_bname(db: Session, name: str, owner_address: str) -> BName:
    name = validate_root_name(name)
    existing = db.query(BName).filter(BName.name == name).one_or_none()
    if existing and existing.status != BNameStatus.deleted:
        raise ValidationError("bName is already registered")
    bname = BName(name=name, owner_address=owner_address, current_zone_version=1)
    db.add(bname)
    db.flush()
    zh = zone_hash(bname.name, bname.current_zone_version, [])
    db.add(BNameZoneVersion(bname_id=bname.id, zone_version=1, zone_hash=zh))
    db.commit()
    db.refresh(bname)
    return bname


def upsert_record(
    db: Session,
    bname: BName,
    subname: str,
    record_type: str,
    record_key: str,
    record_value: str,
    ttl: int,
    signature: str | None = None,
    signer_address: str | None = None,
) -> BNameRecord:
    subname = validate_subname(subname)
    record_key = (record_key or "default").strip().lower()
    record_type_enum = RecordType(record_type.upper())
    record_value = validate_record_value(record_type_enum.value, record_value)

    record = (
        db.query(BNameRecord)
        .filter(
            BNameRecord.bname_id == bname.id,
            BNameRecord.subname == subname,
            BNameRecord.record_type == record_type_enum,
            BNameRecord.record_key == record_key,
        )
        .one_or_none()
    )
    if record:
        record.current_version += 1
        record.record_value = record_value
        record.ttl = ttl
        record.status = RecordStatus.active
    else:
        record = BNameRecord(
            bname_id=bname.id,
            subname=subname,
            record_type=record_type_enum,
            record_key=record_key,
            record_value=record_value,
            ttl=ttl,
            current_version=1,
        )
        db.add(record)
        db.flush()

    rh = record_hash(
        root=bname.name,
        subname=record.subname,
        record_type=record.record_type.value,
        record_key=record.record_key,
        record_value=record.record_value,
        ttl=record.ttl,
        version=record.current_version,
    )
    db.add(
        BNameRecordVersion(
            record_id=record.id,
            bname_id=bname.id,
            subname=record.subname,
            record_type=record.record_type,
            record_key=record.record_key,
            record_value=record.record_value,
            ttl=record.ttl,
            version=record.current_version,
            record_hash=rh,
            signature=signature,
            signer_address=signer_address,
        )
    )
    bname.current_zone_version += 1
    db.flush()
    refreshed_records = db.query(BNameRecord).filter(BNameRecord.bname_id == bname.id).all()
    zh = zone_hash(bname.name, bname.current_zone_version, refreshed_records)
    db.add(BNameZoneVersion(bname_id=bname.id, zone_version=bname.current_zone_version, zone_hash=zh))
    db.add(bname)
    db.commit()
    db.refresh(record)
    return record


def latest_zone(db: Session, bname: BName) -> BNameZoneVersion:
    zone = (
        db.query(BNameZoneVersion)
        .filter(BNameZoneVersion.bname_id == bname.id)
        .order_by(BNameZoneVersion.zone_version.desc())
        .first()
    )
    if not zone:
        raise ValidationError("zone version missing")
    return zone


def latest_record_hash(db: Session, record: BNameRecord) -> str:
    version = (
        db.query(BNameRecordVersion)
        .filter(BNameRecordVersion.record_id == record.id)
        .order_by(BNameRecordVersion.version.desc())
        .first()
    )
    if not version:
        raise ValidationError("record version missing")
    return version.record_hash


def mark_zone_anchored(db: Session, bname: BName, anchor_type: str, tx_hash: str | None) -> BNameZoneVersion:
    zone = latest_zone(db, bname)
    zone.anchor_type = AnchorType(anchor_type)
    zone.anchor_tx_hash = tx_hash
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone
