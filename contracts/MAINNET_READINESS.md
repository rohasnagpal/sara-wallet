# Mainnet readiness — what's left, and why it isn't done here

`CLAUDE_STAGES_3_TO_7.md`'s Stage 7 release gates require things outside
what an AI coding agent can or should do. This document says plainly what
remains, so nothing in this repository is mistaken for "launch-ready"
without it.

## 1. Independent security audit — not started

Everything engineering-side is in place to *hand to* an auditor:
`contracts/THREAT_MODEL.md`, `contracts/SECURITY.md` (Slither: 0 High/
Medium), the full Foundry suite (unit/fuzz/invariant — see
`contracts/README.md` for how to run it), and the protocol spec
(`docs/sara-names-protocol.md`). What's missing is an actual **human,
external, paid or otherwise independently accountable** security firm or
individual reviewing the contract and its test suite, and signing off. This
cannot be satisfied by more automated analysis, however thorough — the doc
is explicit: "Obtain an independent audit before handling meaningful public
registration value."

**Action needed from a human**: engage an auditor (e.g. a firm, or a
reputable independent Solidity security researcher), give them
`contracts/`, `THREAT_MODEL.md`, and this repo's test suite as a starting
point, and resolve whatever they find before proceeding to §2.

## 2. Hardware-controlled multisig — not set up

The Amoy deployment (`script/DeployAmoy.s.sol`) intentionally uses a
**single EOA deployer key** — appropriate for testnet, where funds are
free and worthless. A mainnet deployment must not reuse that pattern: the
admin role (`Ownable2Step` owner) and the `feeRecipient` must both be a
multisig (e.g. Safe) whose signers hold **hardware wallets** (Ledger/
Trezor), per the doc's explicit requirement. This needs:

- Deciding the signer set and threshold (a human/organizational decision).
- Deploying the multisig itself (e.g. Safe on Polygon mainnet).
- Passing the multisig's address as `initialOwner`/`initialFeeRecipient` at
  mainnet deployment time — **not** the Amoy deployer key, which must be
  treated as burned/discarded for any mainnet purpose regardless.

## 3. Mainnet chain ID and USDC address reconfirmation — not done, deliberately

`SaraNamesRegistry`'s constructor already validates the payment token has
contract code, but that only catches "not a contract," not "not actually
USDC." Before any mainnet broadcast, re-derive the exact Polygon mainnet
USDC address from an authoritative source (Circle's own published contract
list, not memory or a cached value) the same way this project verified the
Amoy testnet address **on-chain** before writing it anywhere (queried
`name()`/`symbol()`/`decimals()` live — see `contracts/README.md`).
`app/core/assets.py`'s existing `NETWORKS["polygon"]["usdc"]` value is
Sara's own already-verified mainnet USDC reference and is the right value
to reconfirm against, not re-derive from scratch.

## 4. The canary registration procedure (to run once §1-§3 are done)

Not executed here — this is the literal "spend real funds" step the doc
requires explicit human authorization for. Once a human has completed
§1-§3 and explicitly authorizes proceeding:

1. Deploy to Polygon mainnet via a `DeployMainnet.s.sol` script (create by
   copying `script/DeployAmoy.s.sol` and replacing the chain-ID guard with
   `137`, and the RPC/USDC address with the reconfirmed mainnet values from
   §3) — deterministically where practical, source-verified on Polygonscan,
   with the address/ABI/compiler settings/constructor args/deployment tx
   hash all published (`contracts/README.md`'s export step already
   produces the ABI artifact for this).
2. Register one low-value, disposable name as a canary (a name nobody
   needs, registered and paid for by whoever is running the launch).
3. Publish and verify a signed off-chain record for that canary name.
4. Pay a small amount to the canary name (exercising resolution end-to-end
   through a real Sara send flow).
5. Renew the canary name.
6. Only once all five steps above are confirmed working exactly as
   designed does general public registration open.

## What Stage 7 *did* build (engineering scope, complete)

Revenue tracking (registration/renewal spend now appears in the paying
wallet's own ledger; a `withdraw_fees`/`admin/fees` path for the fee
recipient), redundant RPC providers, a reorg-safe block-cursor event
indexer with recovery-from-downtime, expiry reminders via the existing
alert pipeline (never auto-renewing), Sara Names wired into batch payments
and schedules as an accepted recipient input, a reverse wallet→name lookup
on invoice pages, and this document plus the three others in
`contracts/`/`docs/`. All of it is tested (`backend/tests/test_sara_names.py`,
`backend/tests/test_names_reliability.py`, the full Foundry suite) and
none of it requires — or performs — a mainnet broadcast.
