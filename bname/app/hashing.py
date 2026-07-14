from eth_utils import keccak

from app.models import BNameRecord


def _hex_keccak(payload: str) -> str:
    return "0x" + keccak(text=payload).hex()


def record_hash(
    root: str,
    subname: str,
    record_type: str,
    record_key: str,
    record_value: str,
    ttl: int,
    version: int,
) -> str:
    payload = "|".join(
        [
            "BNAME_RECORD_V1",
            root,
            subname,
            record_type.upper(),
            record_key.lower(),
            record_value,
            str(ttl),
            str(version),
        ]
    )
    return _hex_keccak(payload)


def zone_hash(root: str, zone_version: int, records: list[BNameRecord]) -> str:
    parts = ["BNAME_ZONE_V1", root, str(zone_version)]
    active = [record for record in records if record.status.value == "active"]
    for record in sorted(active, key=lambda r: (r.subname, r.record_type.value, r.record_key)):
        parts.append(
            record_hash(
                root=root,
                subname=record.subname,
                record_type=record.record_type.value,
                record_key=record.record_key,
                record_value=record.record_value,
                ttl=record.ttl,
                version=record.current_version,
            )
        )
    return _hex_keccak("|".join(parts))
