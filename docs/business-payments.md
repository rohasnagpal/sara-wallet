# Business Payments, Accounting & Intelligence

The **Business** view manages counterparties, payment/airdrop batches,
schedules, payroll runs, spending policies and accounting. Batch amounts
remain integers in token base units through validation and signing.
Transactions are signed and durably recorded before broadcast so an
interrupted run can retry the same transaction without signing a
duplicate.

## Batch, recurring & payroll payments

1. **Batch payments** — upload a CSV (`recipient_address`, `amount`, and
   optional `reference`, `note`, `tags`), review the checked result, then
   press Send. Each row is sent as its own on-chain transaction. Sara checks
   the whole file first — addresses, duplicates, exact amounts, spending
   policies, and balance and gas for the batch — and if anything fails it
   creates nothing, so a partial list can never be sent by accident. The
   Batches tab lists only these uploads; a draft or cancelled one that was
   never sent can be deleted, and anything sent stays as a record.
2. **Recurring payments** — set a schedule (e.g. "every 1st of the month")
   that materializes a reviewable payment batch each time it's due. Those
   batches appear under **Schedules → Generated payments**, where you Send
   or Cancel them; payroll runs are reviewed and sent under **Payroll**.
3. **Crypto payroll** — run payroll for a list of employees/contractors in
   one action, built on top of counterparties and batches.

## Accounting

1. **Automatic money in / money out** — each transaction counts toward the
   income and expense report based on the category Sara records when it's
   made: invoice payments, airdrops and payroll received are money in;
   payments you send, batch payments, x402 payments and name fees are money
   out. Moves between your own wallets, swaps and anything Sara can't tell
   apart are left out. You can override any transaction in the Ledger tab
   (Edit, then "Counts as").
2. **Exact-base-unit transaction ledger** — every send/receive is recorded
   in the token's exact base units (no floating-point rounding), searchable
   by tag or note.
3. **Fiat valuation** — each transaction is snapshotted with its USD/INR
   value at the time it happened, for accurate reporting later.
4. **Tags and notes** — label transactions, e.g. "rent" or "invoice #42",
   so you can filter and categorize your ledger.
5. **Counterparties** — save vendors, employees and contractors separately
   from your personal address book, for use in batches and payroll.
6. **Income/expense reporting** — totals every ledger entry by category
   over a date range — a quick P&L across all tokens and networks.
7. **FIFO cost basis and P&L** — tracks acquisition cost lots per token and
   computes realized gains/losses (not tax advice) when you dispose of
   them.
8. **CSV/XLSX exports** — download your full ledger with fiat values, fees
   and classifications for your accountant or tax software.

## Controls

1. **Review before sending** — a batch is validated and shown to you in
   full before anything is signed, and **Send** asks for your wallet
   passphrase. Sending approves and executes in one step, re-validating
   first. This is a check against mistakes, not a maker/checker control —
   Sara is a single-user, local wallet, so there's no independent second
   party to approve on your behalf.
2. **Scoped spending controls** — limit what Sara will send, enforced at
   preview and again right before signing. A policy can set a **maximum per
   payment** and a **cumulative cap** over a **cap period**. The cumulative
   cap adds up everything already sent in a rolling window (per day = the
   last 24 hours, per week = 7 days, per month = 30 days, not calendar
   periods) and blocks a payment that would push the total over the cap.
   Amount limits are set in a specific token (USDC, ETH or POL), so each
   amount is read in that token's own units.

> Spending-policy enforcement covers batch payments (manual batches,
> airdrops, schedules, payroll), chat sends, chat swaps and bridges,
> deployed-token transfers, contract calls and x402 payments. Chat sends,
> swaps and bridges are checked twice: when the action is previewed (so a
> blocked action never asks for CONFIRM) and again right before signing. A
> swap or bridge counts the token you spend against your caps. Not yet
> covered: Sara Names registration/renewal fees, tracked in
> [../ROADMAP.md](../ROADMAP.md) (Now) as
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
