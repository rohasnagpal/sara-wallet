# Sara Names

Sara Names gives your wallet a human-readable name — e.g. sending to
`rohas.sara` instead of a hex address.

1. **Commit/reveal registration** — reserve a name like `rohas` in two
   steps, commit then complete after a ~60s delay, so nobody can front-run
   your registration by watching the mempool.
2. **Renewals** — extend a name's expiry before it lapses; anyone can pay
   to renew it without changing who owns it.
3. **Transfers** — move ownership of a name, e.g. `rohas`, to a different
   wallet address.
4. **Subnames** — create names under one you own, e.g. `pay.rohas`, each
   with its own owner.
5. **EIP-712 signed multi-network address/payment-preference records** —
   publish a signed record so `rohas` resolves to different addresses per
   network (plus a preferred token/network for payments), without an
   on-chain transaction per update.

Backed by the Sara Names Polygon registry contract, developed and tested in
a separate repo — this codebase only holds the client that talks to it
(`backend/app/tools/names/`, `backend/app/routers/names.py`).

## Current status

The registry contract, its tests and deployment tooling live in a separate
repo. This codebase's client, signed records and indexer are implemented
and tested, but the registry has **not yet been broadcast to Polygon
Amoy**. Sara Names remains unavailable in the wallet until
`SARA_NAME_REGISTRAR_ADDRESS` points to a verified deployment.

Tracked in [../ROADMAP.md](../ROADMAP.md) (Now) —
[issue #1](https://github.com/rohasnagpal/sara-wallet/issues/1).

Sara Names should only be configured (`SARA_NAME_REGISTRAR_ADDRESS`,
`SARA_NAME_SERVICE_URL`) after the Amoy deployment is source-verified; the
off-chain record service is optional and its records are always
signature-checked against current registry state.

## Security notes

EIP-712 signatures, content hashes, sequence/epoch replay protection and
live on-chain ownership checks — see
[security-model.md](security-model.md#security-philosophy).
