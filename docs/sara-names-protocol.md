# Sara Names — protocol specification

Status: implementation and deployment tooling complete; **not yet broadcast** to Polygon Amoy. See
`contracts/MAINNET_READINESS.md` for what's required before any mainnet
deployment.

## Namespace

- ASCII-only: `a`-`z`, `0`-`9`, hyphen. A label must not start or end with
  a hyphen. Length 3-63 characters (`MIN_LABEL_LENGTH`/`MAX_LABEL_LENGTH`
  in `SaraNamesRegistry.sol`).
- Root names have no dots (`rohas`, `c4lab`, `me-india`). Subnames are
  dot-joined (`pay.rohas`) — a dot never appears *inside* one label, only
  as the separator between labels.
- Canonicalization: lowercase, trimmed. The contract's own `isValidLabel`
  is the final authority; client-side validation
  (`app/tools/names/sara_names.py::validate_name`,
  `SaraNamesRegistry.sol::isValidLabel`) is a fast pre-check only, mirroring
  the same rule.

## namehash

Standard ENS-style recursive namehash, applied per dot-separated label,
right-to-left:

```
namehash(name):
  node = 0x00...00 (32 bytes)
  if name is empty: return node
  for label in reversed(name.split(".")):
    node = keccak256(node ++ keccak256(utf8(label)))
  return node
```

A multi-label name is the *composition* of one `namehash(parentNode,
label)` call per level, parent-first — the contract never hashes a dotted
string in one call. `node = namehash(namehash(0x0, "rohas"), "pay")` for
`pay.rohas`.

**Cross-language test vectors**:
`contracts/test/vectors/namehash_vectors.json`, independently computed in
Python (`eth_utils.keccak`) and cross-checked against the Solidity
implementation in `contracts/test/SaraNamesRegistry.namehash_vectors.t.sol`
and again in the backend client
(`backend/tests/test_sara_names.py::NamehashCrossLanguageTests`). Any
future implementation (a JS client, say) must reproduce these exact values.

## Registration lifecycle

1. **Commit**: `commit(commitment)` where `commitment =
   keccak256(abi.encode(label, owner, secret))` — the label and owner are
   hidden from mempool observers, resisting front-running.
2. **Wait**: `MIN_COMMITMENT_AGE` = 60 seconds minimum before reveal (same-
   block commit+reveal is rejected), `MAX_COMMITMENT_AGE` = 24 hours
   maximum (a stale commitment can't be revealed indefinitely later).
3. **Reveal**: `register(label, owner, durationSeconds, secret)` —
   re-derives the commitment, checks timing, checks the label is available
   (unregistered, or past its previous registration's grace period),
   checks `durationSeconds` is within `[minRegistrationDuration,
   maxRegistrationDuration]` (defaults: 365 days .. 3650 days), computes
   price from the length-tiered table, pulls payment via
   `SafeERC20.safeTransferFrom` (caller must `approve` the registry first),
   sets ownership/expiry, bumps `recordEpoch` and `ownerGeneration`.

**Pricing** (`priceFor(label, durationSeconds)`): a length-tiered USDC
table, default `{≥3 chars: $50/yr, ≥5 chars: $20/yr, ≥7 chars: $5/yr}`,
prorated linearly by `durationSeconds`. Admin-adjustable
(`setPriceTiers`), with a `PriceTiersUpdated` event for transparency —
never silently changed mid-transaction (the price used is whatever's
configured at reveal time, shown to the user before they sign).

**Renewal**: `renew(label, durationSeconds)` — callable by **anyone**
(third-party renewal without changing ownership), extends `expiry`, does
**not** bump `recordEpoch` (a renewal is not an ownership or signing-
authority change, so previously-signed off-chain records for this name
remain valid).

**Grace period**: 90 days (`GRACE_PERIOD`) after `expiry`. During grace,
only `renew()` succeeds (by the prior owner or a third party paying on
their behalf) — all other owner-gated actions (subname creation, transfer,
signer change) revert. After grace fully elapses, the label becomes
registrable by anyone via a fresh commit/reveal.

## Subnames

`createSubname(parentNode, label, owner)` — caller must currently own
`parentNode` (checked via `_requireLive`, which itself recurses one level
for a subname's own liveness — see below). Subname authority can never
exceed what the parent already has, since creation is gated the same way
at every level, including for a sub-subname under an existing subname.

**Generation invalidation**: each subname records `parentGenerationAtCreation`
= the parent's `ownerGeneration` at creation time. `ownerGeneration` is a
counter bumped only on an actual ownership change (`register()` on a fresh/
reclaimed root, or `transferRoot()`/`transferSubname()`) — deliberately
**not** bumped by a plain `setRecordSigner` call, so rotating your own
off-chain signing key doesn't orphan your entire subname tree. A subname is
`isLive()` only while its parent is itself live **and**
`parent.ownerGeneration == subname.parentGenerationAtCreation` — so a root
that expires, passes grace, and is reclaimed by someone new immediately
orphans every subname the *previous* owner had granted, with no cleanup
transaction required.

`revokeSubname(node)` — the parent's current owner, or the subname's own
owner voluntarily giving it up.

## EIP-712 signed off-chain records

The registry tracks only ownership/expiry/`recordSigner`/`recordEpoch` per
node — it never sees resolution data (addresses per network, payment
preferences). That data is a versioned, signed record the owner (or their
delegated `recordSigner`) publishes off-chain.

**Domain**: `{name: "SaraNames", version: "1", chainId, verifyingContract:
<registry address>}`.

**`SaraNameRecord` struct** (`app/tools/names/eip712_records.py`):

| field | type | purpose |
|---|---|---|
| `node` | `bytes32` | which name this record is for |
| `recordEpoch` | `uint32` | must match the registry's *current* epoch for this node — a transfer or signer change invalidates every record signed under the old epoch |
| `sequence` | `uint64` | strictly increasing; a verifier rejects any sequence ≤ the last one it saw, defeating replay of a superseded-but-still-epoch-valid record |
| `issuedAt` / `expiresAt` | `uint64` | unix seconds; a verifier rejects outside this window |
| `addresses` | `NetworkAddress[]` | `{network: <CAIP-2 id>, addr: <string>}[]` — e.g. `{"network": "eip155:137", "addr": "0x..."}` |
| `preferredNetwork` / `preferredToken` | `string` | a payment preference, never binding — the payer always sees and confirms the actual resolved address/chain/asset |
| `contentHash` | `bytes32` | `keccak256(canonical_json(record without this field))` — an optional integrity check over the full record |

**Canonical JSON** (`NameRecord.canonical_json()`): sorted keys, no
whitespace (`json.dumps(..., sort_keys=True, separators=(",", ":"))`),
every integer field serialized as a **decimal string**, not a JSON number
— dodges JavaScript's float-precision loss on large `uint64` values. This
is a *separate* serialization from the EIP-712 typed-data structure used
for the actual signature (EIP-712 already defines its own canonical
hashing; canonical JSON here exists only to make `contentHash` and
cross-language fixtures reproducible).

**Verification** (`eip712_records.verify_record`) — every check must pass,
and any single failure means "treat as no record," never "use it anyway":
node matches, `recordEpoch` matches the *freshly-read* on-chain epoch,
`sequence` > last-seen, `issuedAt` ≤ now ≤ `expiresAt`, the recovered
signer equals the *freshly-read* on-chain owner or `recordSigner`, and (if
present) `contentHash` matches the record's own canonical JSON.

## Multi-chain resolution

`NetworkAddress.network` uses CAIP-2 identifiers (`eip155:137` for Polygon
mainnet, `eip155:80002` for Amoy, and equivalent identifiers for Solana/
Tron) so one name can resolve to addresses on any chain family, not just
EVM. The payer always sees the actual resolved address/chain/asset before
confirming — a `preferredNetwork`/`preferredToken` is a hint, never
authoritative on its own.
