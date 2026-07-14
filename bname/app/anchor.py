import json

from app.models import BName, BNameRecord


def build_hash_anchor_calldata(root: str, zone_version: int, zone_hash: str) -> str:
    return f"BNAME_ZONE_HASH_V1:{root}:{zone_version}:{zone_hash}"


def build_full_anchor_calldata(root: str, zone_version: int, zone_hash: str, records: list[BNameRecord]) -> str:
    canonical_records = [
        {
            "subname": record.subname,
            "record_type": record.record_type.value,
            "record_key": record.record_key,
            "record_value": record.record_value,
            "ttl": record.ttl,
            "version": record.current_version,
        }
        for record in sorted(records, key=lambda r: (r.subname, r.record_type.value, r.record_key))
        if record.status.value == "active"
    ]
    payload = {"root": root, "zone_version": zone_version, "zone_hash": zone_hash, "records": canonical_records}
    return "BNAME_ZONE_FULL_V1:" + json.dumps(payload, separators=(",", ":"), sort_keys=True)


def queue_anchor_job(bname: BName, anchor_type: str, zone_hash: str) -> str:
    # Production implementation should enqueue a worker that signs and sends a
    # Polygon transaction, then stores the resulting tx hash. This deterministic
    # placeholder keeps the API contract usable in local/dev deployments.
    return f"queued:{anchor_type}:{bname.name}:{bname.current_zone_version}:{zone_hash}"
