# SaraNamesRegistry — operator runbook

## Database backup / restore

Sara's whole local state (`sara.db`, SQLite) is a single file —
`backend/sara.db` by default (`DATABASE_URL` in `backend/.env.local`). Sara
Names adds five tables to it: `sara_names`, `sara_name_record_cache`,
`indexer_cursors`, plus Stage 6/7's `risk_screenings`/`risk_reviews` (used
by the address-screening feature, not Sara Names itself, but co-located).

- **Backup**: stop Sara (or accept a brief inconsistency window — SQLite
  handles concurrent readers fine, but for an exact point-in-time copy,
  stop the process first), then copy the file: `cp backend/sara.db
  backend/sara.db.bak-$(date +%Y%m%d)`.
- **Restore**: stop Sara, replace `backend/sara.db` with the backup, start
  Sara. The `run_migrations()` startup step (`app/db/migrations.py`) is
  idempotent and safe to run against an older backup — it only ever adds
  columns/tables, never removes data.
- **What's *not* at risk of loss**: on-chain state (names, ownership,
  expiry) lives entirely on Polygon Amoy — losing `sara.db` entirely loses
  only Sara's *local index* over that state (see rebuild procedure below),
  never the names themselves.

## Rebuilding the resolver cache from chain state alone

`sara_name_record_cache` (signed off-chain records) and `sara_names` (the
local `SaraName` index) are both derived data — the registry contract and,
optionally, the external `SARA_NAME_SERVICE_URL` record-hosting service
remain the actual sources of truth.

1. **`sara_names` (ownership/expiry index)**: delete all rows, reset
   `indexer_cursors` for `contract="sara_names_registry"` to an earlier
   block (or delete the cursor row entirely — `names_indexer.sync_events`
   recreates it starting `SARA_NAME_AMOY_CONFIRMATIONS` blocks behind the
   current tip), then let the foundation cycle's `sync_events` call
   (`main.py::_run_foundation_cycle`) replay `NameRegistered`/
   `NameRenewed`/`NameTransferred`/`SubnameCreated`/`SubnameRevoked` events
   from that point forward. This only recovers names belonging to *this
   Sara instance's own wallets* (see `app/services/names_indexer.py`'s
   module docstring for that scope boundary) — for a single lost/expired
   `expiry` value on one name, cheaper to just call
   `GET /api/names/{name}` once, which reads fresh on-chain state directly.
2. **`sara_name_record_cache` (signed records)**: delete all rows. Any
   record fetch (`GET /api/names/{name}/records`) that finds the local
   cache empty falls back to `SARA_NAME_SERVICE_URL` if configured; if a
   record was never published externally, it is genuinely gone and the
   name's owner must re-sign and republish
   (`POST /api/names/{name}/records`) — there was never a second copy by
   design (off-chain records are explicitly not authoritative anywhere,
   including in Sara's own cache).

## Registry migration strategy

If a new registry contract version is ever deployed (bug fix, protocol
upgrade — the current contract is deliberately non-upgradeable, per the
doc's "prefer non-upgradeable" guidance and the project-owner-approval
requirement for any proxy), **names do not carry over automatically**:

1. The new registry is a fresh, empty contract at a new address — nothing
   from the old one is copied on-chain (this is a deliberate consequence of
   choosing non-upgradeable; do not build a silent migration path that
   would let admin "re-mint" old names on the new contract, since that
   would reintroduce the exact seizure risk §1/§9 of `THREAT_MODEL.md`
   rule out).
2. Existing name owners must explicitly re-register on the new contract
   (their own action, their own gas/fee cost) — Sara can offer a "renew on
   the new registry" convenience flow (not built in this stage) that reads
   the old contract's data and pre-fills a fresh commit/reveal on the new
   one, but the actual claim is a new, independent action by the owner.
3. Update `SARA_NAME_REGISTRAR_ADDRESS` (and re-run
   `contracts/scripts/export_artifacts.py` if the ABI changed) only after
   every user-facing surface (this runbook, `docs/sara-names-protocol.md`,
   the frontend) is updated to say so — never silently point existing users
   at a different contract without them knowing their old names don't
   transfer.

## Monitoring

`GET /api/system/foundation` (existing diagnostics endpoint, extended in
Stage 7) returns a `sara_names` section: counts by local status
(`pending`/`committed`/`registered`/`renewed`/`transferred_away`) and
indexer cursor lag (`current_block - last_block`). No wallet secrets or
personal data are in this payload. Alert/webhook backlog is the existing
top-level `events` breakdown (Sara Names reminders flow through the same
`DomainEvent` outbox as everything else — no separate queue to monitor).

A `lag_blocks` that keeps growing across polls means the Amoy RPC (or both
configured fallbacks, `SARA_NAME_AMOY_RPC_URL`) is unreachable, or the
process isn't running its foundation cycle — check `sara.log`/process
health first, not the indexer itself.
