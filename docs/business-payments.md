# Business Payments, Accounting & Intelligence

The **Business** view manages counterparties, payment/airdrop batches,
schedules, payroll runs, spending policies and accounting. Batch amounts
remain integers in token base units through validation and signing.
Transactions are signed and durably recorded before broadcast so an
interrupted run can retry the same transaction without signing a
duplicate.

## Batch, recurring & payroll payments

1. **Batch payments** — pay or airdrop many recipients in one go from a
   list, each sent as its own on-chain transaction.
2. **Recurring payments** — set a schedule (e.g. "every 1st of the month")
   that materializes a reviewable payment batch each time it's due.
3. **Crypto payroll** — run payroll for a list of employees/contractors in
   one action, built on top of counterparties and batches.

## Accounting

1. **Exact-base-unit transaction ledger** — every send/receive is recorded
   in the token's exact base units (no floating-point rounding), searchable
   by tag or note.
2. **Fiat valuation** — each transaction is snapshotted with its USD/INR
   value at the time it happened, for accurate reporting later.
3. **Tags and notes** — label transactions, e.g. "rent" or "invoice #42",
   so you can filter and categorize your ledger.
4. **Counterparties** — save vendors, employees and contractors separately
   from your personal address book, for use in batches and payroll.
5. **Income/expense reporting** — totals every ledger entry by category
   over a date range — a quick P&L across all tokens and networks.
6. **FIFO cost basis and P&L** — tracks acquisition cost lots per token and
   computes realized gains/losses (not tax advice) when you dispose of
   them.
7. **CSV/XLSX exports** — download your full ledger with fiat values, fees
   and classifications for your accountant or tax software.

## Controls

1. **Approval workflows** — require a second, independently-generated
   checker credential before a sensitive payment batch executes. Checker
   keys are shown once and stored only as SHA-256 hashes; caller-supplied
   names are not treated as identities.
2. **Scoped spending controls** — cap how much a wallet can send per
   transaction or per day/week/month, enforced right before signing.

When a policy requires dual control, create a checker credential in the
Business view and give it to the authorised approver.

> Spending-policy enforcement currently covers batch payments (manual
> batches, airdrops, schedules, payroll). Ad-hoc chat/safety sends are not
> yet covered — tracked in [../ROADMAP.md](../ROADMAP.md) (Now) as
> [issue #4](https://github.com/rohasnagpal/sara-wallet/issues/4).

## Portfolio & wallet intelligence

1. **Historical performance** — track total portfolio value over time
   across every wallet and network.
2. **Exposure** — see how holdings break down by token and network at a
   glance.
3. **Top-payee analysis** — "who have I sent the most to?", ranked from
   your own ledger, not a third-party service.
4. **Spend by category** — totals outgoing transactions by the
   tags/categories you've assigned them.
5. **Recurring-counterparty detection** — flags addresses you pay
   repeatedly, useful for spotting subscriptions or regular vendors.
6. **Unusual-activity detection** — flags transactions that look out of
   pattern compared to your usual activity.
7. **Treasury monitoring** — see combined balances and exposure across
   every wallet and network in one view.
8. **Stablecoin route comparison** — estimates cost and time to move funds
   between two networks, e.g. Polygon to Arbitrum, for deciding how to
   rebalance.

## Alerts

Alerts can monitor payments, invoices, transactions and balance thresholds
(e.g. "alert me if my Treasury wallet drops below 50 USDC") through
Telegram, email or signed webhooks.

## Roadmap

Broader live reconciliation coverage is tracked in
[../ROADMAP.md](../ROADMAP.md) (Next) —
[issue #5](https://github.com/rohasnagpal/sara-wallet/issues/5).
