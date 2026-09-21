# Roadmap

Sara Wallet's public direction, grouped by how soon it's actually happening.
Each item links to the GitHub issue tracking it — comment there if you want
to help or have opinions on scope.

This roadmap reflects what's actually planned; it isn't a promise of
delivery dates. See [SECURITY.md](SECURITY.md) for audit status before
relying on anything here for mainnet use.

## Now

Actively being worked on, or explicitly blocking the next release.

- **Deploy Sara Names registry to Polygon Amoy** — the client, signed
  records and indexer are implemented and tested, but the registry
  contract itself hasn't been broadcast yet, so Sara Names is unavailable
  in the wallet until then. ([#1](https://github.com/rohasnagpal/sara-wallet/issues/1))
- **Pre-mainnet hardening** — independent smart-contract audit,
  hardware-controlled multisig, authoritative reconfirmation of
  network/token addresses, and a low-value canary deployment, all required
  before any mainnet launch. ([#2](https://github.com/rohasnagpal/sara-wallet/issues/2))
- **Harden and expand x402 real-payment coverage** — the buyer client,
  policy-gated auto-pay and demo seller landed recently; broadening seller/
  network coverage and policy-gating scenarios beyond the demo is next.
  ([#3](https://github.com/rohasnagpal/sara-wallet/issues/3))
- **Finish spending-policy coverage** — policies now govern batches,
  payroll, schedules, chat sends, swaps, bridges, token transfers,
  contract calls and x402. Still uncovered: Sara Names registration and
  renewal fees. ([#4](https://github.com/rohasnagpal/sara-wallet/issues/4))

## Next

Scoped and wanted, not yet started.

- **Broader live reconciliation coverage** — more networks/tokens and
  matching heuristics for automatically matching incoming transfers
  against payment requests. ([#5](https://github.com/rohasnagpal/sara-wallet/issues/5))

## Exploring

Directionally interesting, not committed, may not happen.

- **Realtime voice, where supported** — a lower-friction alternative to
  typed chat commands, on platforms/models where it's available.
  ([#6](https://github.com/rohasnagpal/sara-wallet/issues/6))
- **Additional command languages** — natural-language support beyond
  English. ([#7](https://github.com/rohasnagpal/sara-wallet/issues/7))

## Proposing something new

Open an issue describing the problem you want solved (not just the
feature) before sending a PR for anything roadmap-sized — see
[CONTRIBUTING.md](CONTRIBUTING.md). Items move from Exploring → Next → Now
as they get scoped and someone (maintainer or contributor) actually picks
them up; there's no fixed schedule.
