# SaraNamesRegistry — security notes

## Static analysis

`slither.config.json` runs Slither against `src/SaraNamesRegistry.sol` only
(vendored `lib/` excluded). Run it with:

```
.slither-venv/bin/slither src/SaraNamesRegistry.sol --config-file slither.config.json
```

Current result: **0 High, 0 Medium, 8 Low, informational-only otherwise.**

### Accepted findings

- **`timestamp` (Low), 8 occurrences** — every registry function that checks
  expiry, the 90-day grace period, or the commit/reveal timing window
  compares against `block.timestamp`. This is inherent to any time-based
  name registry (ENS itself has the same property). A validator can shift
  a block's timestamp by at most a few seconds; that has no meaningful
  effect on a 60-second minimum commitment age, a 24-hour maximum
  commitment age, or a 90-day grace period. Accepted, not fixed.
- **`too-many-digits` (informational), 3 occurrences** — the default price
  tiers (`50_000000`, `20_000000`, `5_000000`, i.e. $50/$20/$5 at USDC's 6
  decimals) trip Slither's "use scientific notation" style nit despite
  already using underscore separators. Purely cosmetic. Accepted, not
  fixed.
- **`incorrect-equality` (false positive), 1 occurrence** — the comparison
  to `bytes32(0)` in `createSubname` detects the canonical root sentinel
  while walking the name tree. It does not compare a balance, price or
  authorization value. Nested invalidation and depth-bound tests cover the
  traversal, so this detector is explicitly excluded.
- Findings inside vendored `lib/openzeppelin-contracts` (pragma spread,
  inline assembly in `SafeERC20`/`StorageSlot`, informational-only) are
  excluded from the run entirely via `slither.config.json` — that's
  reviewed, pinned, widely-audited upstream code, not this project's
  surface.

## Design-level mitigations (not Slither findings, but the things an
independent reviewer should specifically check)

- **Front-running**: registration requires `commit()` then `register()`
  after `MIN_COMMITMENT_AGE` (60s) and before `MAX_COMMITMENT_AGE` (24h);
  the commitment hashes `(label, owner, secret)`, so an attacker observing
  a pending commitment transaction cannot derive the label or reconstruct
  a winning reveal. Covered by
  `test_front_running_a_commitment_does_not_let_attacker_steal_the_name`.
- **Reentrancy**: `register`/`renew`/`withdrawFees` are `nonReentrant`;
  state is written before the external `safeTransferFrom`/`safeTransfer`
  call (checks-effects-interactions). Covered by
  `test_reentrant_payment_token_cannot_double_register`, which uses a
  deliberately malicious ERC-20 that tries to reenter from inside
  `transferFrom`.
- **Admin cannot seize names**: no admin function takes an existing node's
  owner or expiry as an argument. Admin powers are limited to price tiers,
  reserved-label management, pausing *new* registration only, fee
  recipient, and fee withdrawal. Covered by
  `test_admin_cannot_seize_or_alter_an_existing_registered_name`.
- **Subname authority cannot exceed the parent's**: a subname records the
  parent's `ownerGeneration` at creation time; if the parent root
  transfers, or expires-through-grace and is re-registered by someone new,
  every subname created under the old ownership stops being considered
  live (`isLive()` returns `false`, and any owner-gated action on it
  reverts via `_requireLive`). Covered by
  `test_subname_authority_cannot_exceed_parent_after_root_is_reclaimed`.
- **Malicious/unexpected payment token**: the payment token address is
  fixed at construction (`immutable`) and validated to have contract code;
  all transfers go through `SafeERC20`, which reverts on a non-standard
  ERC-20 return value instead of silently treating it as success.
