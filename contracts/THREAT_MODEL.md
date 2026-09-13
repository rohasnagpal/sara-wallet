# SaraNamesRegistry — threat model

Written for Stage 7's "independent contract security review" release gate.
This document is Sara's own engineering-side threat model — it is **not**
a substitute for the independent, human-conducted audit the doc's release
gates require before handling meaningful public registration value. See
`MAINNET_READINESS.md` for what that audit needs to cover and why it can't
be done here.

Each threat below references the specific test(s) that exercise it and,
where relevant, `SECURITY.md`'s Slither findings write-up.

## 1. Registrar abuse

**Threat**: the admin role reassigns, seizes, or extends/shortens an
already-registered name's ownership or expiry.
**Mitigation**: no admin function takes an existing node's owner or expiry
as an argument at all — `Ownable2Step`'s admin can only touch price tiers,
reserved-label management, pause state, fee recipient, and fee withdrawal.
**Test**: `test_admin_cannot_seize_or_alter_an_existing_registered_name`.

**Threat**: admin reserves a label a user is mid-commit on, to block them.
**Mitigation**: `reserveNames` blocks future `register()` calls on that
label but has no effect on an already-registered name; a pending
commitment for a label reserved after `commit()` still succeeds at
`register()` time only if the label check at reveal time passes — reserving
after commit but before reveal *would* block that specific reveal (a real,
narrow griefing vector, documented rather than hidden: an admin can front-
run a user's reveal by reserving their exact label in the ~24h window).
Mitigated by admin being a real, disclosed, accountable party (not
anonymous), and by `MAX_COMMITMENT_AGE` bounding the exposure window.

## 2. Front-running

**Threat**: an attacker observes a `commit()` transaction in the mempool
and front-runs the eventual `register()` to steal the label.
**Mitigation**: the commitment is `keccak256(label, owner, secret)` — an
observer sees only the hash, not the label, and cannot construct a valid
reveal without the secret. `MIN_COMMITMENT_AGE` (60s) additionally prevents
same-block commit+reveal, which would otherwise let an attacker who
somehow learned a label still race a reveal into the same block.
**Test**: `test_front_running_a_commitment_does_not_let_attacker_steal_the_name`.

**Threat**: commitment replay — reusing a stale commitment after it was
already consumed, or after `MAX_COMMITMENT_AGE`.
**Mitigation**: `commitments[commitment]` is deleted on successful
`register()`; reveal reverts outside the `[MIN_COMMITMENT_AGE,
MAX_COMMITMENT_AGE]` window.
**Tests**: `test_commitment_cannot_be_replayed_after_use`,
`test_register_reverts_if_revealed_too_early`,
`test_register_reverts_if_commitment_expired`.

## 3. Expiry / grace period edge cases

**Threat**: a name is registrable by a new owner *during* the grace period,
letting the previous owner's renewal race a new registration.
**Mitigation**: `_isAvailable`/`renew` both gate strictly on
`block.timestamp > expiry + GRACE_PERIOD` — during grace, only `renew()`
(prior owner or any third party paying on their behalf) succeeds; a fresh
`register()` reverts with `LabelUnavailable` until grace has fully elapsed.
**Test**: `test_renew_reverts_after_grace_period`,
`test_expired_and_regraced_name_becomes_registrable_by_anyone`.

**Threat**: a subname survives its parent's expiry-and-reclaim, letting the
*old* owner's subname continue resolving under the *new* owner.
**Mitigation**: each subname records `parentGenerationAtCreation`
(`ownerGeneration` at creation time); `_requireLive`/`isLive` compare that
against the parent's *current* `ownerGeneration`, bumped on every fresh
`register()` and `transferRoot()`. A generation mismatch makes the subname
non-live immediately, with no cleanup transaction required.
**Test**: `test_subname_authority_cannot_exceed_parent_after_root_is_reclaimed`.

## 4. Signature replay (off-chain EIP-712 records)

Out of the contract's own attack surface (it never sees these signatures)
but load-bearing for correct resolution — see `app/tools/names/eip712_records.py`.
**Threat**: an old signed record is replayed after the name transferred or
its signer changed.
**Mitigation**: verification requires `record.recordEpoch == <current
on-chain epoch, fetched fresh>`; `recordEpoch` bumps on every transfer and
every `setRecordSigner` call.
**Threat**: an old record for the *same* epoch is replayed (e.g. a lower
preference the owner already superseded).
**Mitigation**: strictly-increasing `sequence`, checked against the
caller-supplied `last_seen_sequence`.
**Tests**: `backend/tests/test_sara_names.py::Eip712RecordTests` — wrong
chain, wrong node, stale sequence, expired, epoch-mismatch-after-transfer,
signer-not-owner-or-recordSigner, tampered content hash — all rejected.

## 5. Stale resolution

**Threat**: a client (or Sara's own local cache) shows an address for a
name that no longer owns it, or has expired.
**Mitigation**: `resolve()` always calls `isLive()` fresh — an expired-past-
grace name returns `None`, never a stale owner. The local
`SaraNameRecordCache` is re-verified against fresh on-chain state
(`current_owner`/`current_record_signer`/`current_epoch`) on every read,
never trusted past that; `app/services/names_indexer.py` only advances its
cursor past `SARA_NAME_AMOY_CONFIRMATIONS` blocks, so a reorg within that
depth is never surfaced as final in the first place.

## 6. Subname authority

Covered under §3 (parent-generation invalidation). Additionally:
**Threat**: a subname owner grants themselves broader rights than their
parent has.
**Mitigation**: `createSubname` is gated on `_requireLive(parentNode).owner
== msg.sender` at every level of nesting — a sub-subname's creation is
bounded the same way, recursively, by construction (there is no separate
"grant more scope" primitive to misuse).
**Tests**: `test_non_parent_owner_cannot_create_subname`,
`test_parent_owner_can_create_and_transfer_subname`.

## 7. USDC behaviour

**Threat**: a non-standard ERC-20 (no return value, or a malicious/
reentrant one) is configured as the payment token.
**Mitigation**: `SafeERC20` is used for every transfer
(`safeTransferFrom`/`safeTransfer`), which reverts on a missing/false
return rather than treating it as success. `register`/`renew`/
`withdrawFees` are all `nonReentrant`, and state is written *before* the
external token call (checks-effects-interactions) so even a maximally
adversarial token can't re-enter into a double-registration.
**Test**: `test_reentrant_payment_token_cannot_double_register` (a
purpose-built malicious ERC-20 that tries to reenter `register()` from
inside `transferFrom`).
**Residual risk, documented not mitigated in-contract**: the payment token
address is fixed at construction (`immutable`) — if the *configured*
address is wrong (not actually USDC), that's a deployment-time human error,
not a contract bug; §2 of `MAINNET_READINESS.md` covers reconfirming it.

## 8. Fee withdrawal

**Threat**: an unauthorised address withdraws accumulated fees.
**Mitigation**: `withdrawFees` requires `msg.sender == feeRecipient ||
msg.sender == owner()`.
**Test**: `test_non_recipient_non_owner_cannot_withdraw_fees`,
`test_fee_withdrawal_goes_only_to_fee_recipient`.

## 9. Admin-key compromise

**Threat**: the admin private key is stolen; attacker pauses registration
indefinitely, drains fees, or reserves every short label.
**Mitigation in contract**: `Ownable2Step` (two-step transfer — a
compromised key alone can't silently move ownership to an attacker-
controlled address without a second confirming transaction from that
address, giving a window to notice). Pause only blocks *new* registration —
existing owners keep renewing/transferring/reading throughout, so a paused
registry doesn't strand existing users' names.
**Mitigation outside the contract (Stage 7 release gate, not built here)**:
the admin and fee-recipient roles must be a hardware-controlled multisig
before mainnet, not a single EOA — see `MAINNET_READINESS.md`. This
repository's Amoy deployment script (`script/DeployAmoy.s.sol`) uses a
single deployer key by design, since testnet funds have no value; that
must **not** carry over to a mainnet deployment.
