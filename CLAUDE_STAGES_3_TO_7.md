# Claude implementation prompt: Sara Stages 3–7

You are continuing development of **Sara**, a free and open-source, local-first crypto wallet and business payments application. Work directly in this repository. Implement Stages 3–7 in sequence, including the Polygon smart contract for **Sara Names**.

This is an implementation assignment, not a planning-only exercise. Inspect the existing code before changing it, preserve unrelated work, implement each stage completely, add migrations and tests, and verify the result. Do not claim a stage is complete merely because routes, schemas, mock screens, or TODOs exist.

## 1. Operating rules

1. Read `AGENTS.md`, `README.md`, the current git diff, existing tests, database models, migrations, routers, chain adapters and frontend before implementation.
2. Treat the current working tree as potentially dirty. Never discard, reset or overwrite unrelated changes.
3. Build on Stages 0–2. Do not rewrite completed foundations unless a tested correction is necessary.
4. Implement one stage at a time. At the end of every stage:
   - run all existing and new tests;
   - run compilation/static checks available in the repository;
   - perform a focused API/UI smoke test;
   - inspect `git diff --check`;
   - record what is complete, what was tested and any external configuration still required.
5. Use exact integer base units for blockchain amounts. Floats may remain only where legacy compatibility requires them and must never drive signing, reconciliation, balances, cost basis or accounting.
6. Keep signing local. Private keys, passphrases and decrypted key material must never leave the local Sara process or appear in logs, events, exports, error messages or API responses.
7. Every money-moving action must follow: prepare → validate/simulate → present an exact confirmation → authorise/approve if required → sign → broadcast → persist → monitor finality.
8. Never silently fall back from a failed simulation, risk check, policy check, approval or reconciliation step.
9. Use authoritative chain data and verified token/contract addresses. Do not trust token names, symbols, calldata, aggregator responses or user-provided ABIs without validation.
10. Add idempotency, audit events and safe retry behaviour for recurring jobs, webhooks and payment execution.
11. All sensitive local API routes must retain Sara's session protection. Public payment/name resolution routes must expose only the minimum public data.
12. Avoid placeholder implementations. If an external provider is unavailable, implement a clean adapter interface, explicit configuration state, deterministic tests and a fail-closed user experience.

## 2. Current baseline — do not rebuild

Stages 0–2 already provide the foundation that future work must reuse:

- FastAPI backend, local SQLite persistence and single-page frontend.
- Encrypted local wallet keys, unlock/session controls and transaction confirmation.
- Supported EVM networks and trusted-token resolution.
- Exact transaction fields, confirmation/finality monitoring, append-only audit records, transactional domain-event outbox and bounded retries.
- Basic identities/roles foundation.
- Portfolio intelligence, transaction indexing and ledger.
- Fiat value snapshots, tags, notes and payment receipts.
- Balance monitors and Telegram, email and signed webhook destinations.
- Token allowance visibility/revocation and transaction simulation.
- Persistent Polygon USDC invoices with customer, amount, due date, description, payment address and lifecycle status.
- Public invoice payment pages and QR payloads.
- Background on-chain invoice reconciliation and invoice-linked receipts.
- Hashed merchant API keys, merchant invoice endpoints and merchant-scoped signed payment webhooks.

Important existing areas include:

- `backend/app/db/models.py`
- `backend/app/db/migrations.py`
- `backend/app/core/events.py`
- `backend/app/core/audit.py`
- `backend/app/routers/payments.py`
- `backend/app/routers/ledger.py`
- `backend/app/routers/safety.py`
- `backend/app/services/`
- `backend/app/tools/payments/`
- `backend/main.py`
- `index.html`
- `backend/tests/`

Confirm the baseline from the code rather than relying only on this summary.

## 3. Cross-stage architecture

Extend the current modular structure instead of concentrating new functionality in `chat.py` or one oversized router.

Use clear modules for:

- business counterparties and payment obligations;
- payment batches and execution items;
- schedules and recurring runs;
- approvals and policy evaluation;
- accounting classifications, lots and disposals;
- reporting and exports;
- treasury snapshots and routing quotes;
- token deployment/management;
- risk screening and contract interaction;
- Sara Names resolution, signed records and registry integration.

Every durable feature requires:

- SQLAlchemy models;
- an idempotent migration for legacy databases;
- service/domain logic separated from HTTP handlers;
- authenticated API routes, except deliberately public endpoints;
- frontend controls with escaped untrusted content;
- audit and domain events;
- unit and integration tests;
- concise README/API documentation.

Background jobs must use leases or durable run identity so restarts and concurrent workers cannot execute the same payment twice. Use database uniqueness constraints as the final source of truth, not only pre-insert checks.

## 4. Stage 3 — business payment operations

Build reliable multi-recipient and recurring payment operations before adding advanced accounting.

### Features

1. **Counterparties**
   - Store vendors, employees and contractors separately from the simple address book.
   - Support display name, type, wallet addresses by network, default token/network, external reference, tags, notes and active status.
   - Never store unnecessary personal payroll data.

2. **Batch payments**
   - Create a draft containing multiple recipients, exact token amounts, network, memo/reference and optional execution date.
   - Validate every address, asset, amount, balance, gas requirement and duplicate row before approval.
   - Show item totals, fees and failures before signing.
   - Initial execution may submit separate Polygon transactions sequentially; do not describe that as one on-chain transaction.
   - Track every item independently: draft, awaiting approval, approved, submitted, confirmed, failed, cancelled.
   - Make retries item-specific and idempotent. Never resend an item already broadcast.

3. **Airdrops / batch token distribution**
   - Reuse the batch engine with a distinct distribution type.
   - Support CSV import with strict schema validation, duplicate detection, row-level errors and safe spreadsheet handling.
   - Permit only trusted or Sara-created tokens.

4. **Recurring payments**
   - Store recurrence, timezone, start/end dates, next run, amount, token, network, recipient and approval policy.
   - Materialise each occurrence as a unique payment obligation before execution.
   - Missed schedules must not cause duplicate catch-up sends.
   - Require an unlocked wallet and any required approval before signing; scheduling is not blanket signing authority.

5. **Crypto payroll**
   - Maintain employee/contractor payment profiles and recurring salary instructions.
   - Produce payroll runs as reviewable batches.
   - Support fixed crypto amount initially; fiat-denominated payroll must lock a quoted crypto amount with timestamp/source before approval.
   - Provide payroll run and per-recipient receipts.

6. **Approval workflows**
   - Implement maker/checker separation using the existing principal/role foundation.
   - A preparer cannot approve their own payment when two-person control applies.
   - Record approver, timestamp, scope, payload hash and decision.
   - Editing a material field invalidates prior approval.

7. **Spending controls**
   - Policies may restrict amount, cumulative amount per period, wallet, user, token, destination address, counterparty, network and time window.
   - Evaluate policy during preparation and immediately before signing.
   - Denials must be explicit and audited; no AI override.

### Stage 3 acceptance criteria

- A user can import, review, approve and execute a Polygon USDC batch without duplicate sends.
- A recurring obligation generates exactly one due payment per occurrence across restarts.
- A payroll run uses the same approval, policy and finality pipeline as other payments.
- Changing an approved amount, address, token or network invalidates approval.
- Tests cover partial batch failure, restart recovery, double-click/double-worker execution, insufficient balance, policy denial and self-approval rejection.

## 5. Stage 4 — accounting, reporting and exports

Turn the Stage 1 ledger into a reproducible accounting subsystem.

### Features

1. **Accounting classifications**
   - Classify income, expense, transfer, swap, fee, payroll, invoice receipt, airdrop, acquisition, disposal and unknown activity.
   - Allow category, counterparty, project, client and invoice links.
   - Preserve original chain facts separately from editable accounting metadata.

2. **Internal transfer and swap matching**
   - Match transfers between the user's wallets without counting them as income/expense.
   - Link swap legs and fees while preserving each on-chain transaction.
   - Surface uncertain matches for review instead of guessing.

3. **Fiat valuation**
   - Store USD and INR price, source and timestamp for every relevant transaction.
   - Support deterministic historical backfill.
   - Never replace a historical value merely because the current price changed; revisions need provenance.

4. **Cost basis and P&L**
   - Implement lot records and disposal allocations.
   - Support FIFO first. Design the schema so another method can be added later.
   - Track acquisition cost, proceeds, fees, realised gain/loss and unrealised gain/loss using decimals.
   - Transfers between owned wallets move lots rather than realising gains.
   - Do not present calculations as tax advice. Reports must state the selected method, currency, date range and missing-data warnings.

5. **Income and expense reporting**
   - Filter by period, wallet, token, network, category, counterparty, client and project.
   - Include totals plus drill-down to source transactions.

6. **Accounting exports**
   - Export CSV and XLSX suitable for accountants.
   - Include stable IDs, hashes, timestamps, exact crypto amounts, fiat values, fees, categories, references, notes and provenance.
   - Prevent CSV formula injection.
   - Provide a data-quality report listing unpriced, uncategorised, duplicated, failed or potentially incomplete transactions.

### Stage 4 acceptance criteria

- The same transaction import and valuation input always produces the same report.
- Internal transfers do not inflate income or disposals.
- FIFO lot tests cover partial disposal, fees, transfer between owned wallets and missing historical prices.
- CSV/XLSX exports round-trip exact amounts and neutralise spreadsheet formulas.
- Every report total can be traced to transaction IDs and valuation records.

## 6. Stage 5 — token, treasury and advanced safety tools

### Features

1. **ERC-20 token creation on Polygon**
   - Provide audited templates for fixed-supply and owner-mintable/burnable tokens.
   - User selects name, symbol, decimals, initial supply, cap where applicable and owner wallet.
   - Compile from pinned source and bytecode; do not accept arbitrary Solidity through the normal token creator.
   - Show permissions and centralisation risks before deployment.
   - Simulate deployment, confirm exact parameters and fees, then sign locally.
   - Persist deployment address, deployer, bytecode/source version and transaction hash.

2. **Token management**
   - For tokens created through Sara, support supply viewing, transfer, mint and burn only when contract capabilities and caller authority permit them.
   - Detect current on-chain role/owner rather than trusting local records.
   - Reuse batch distribution for airdrops.

3. **Treasury management**
   - Aggregate balances by wallet, network, token and fiat exposure.
   - Detect low gas/stablecoin balances, concentration and configurable rebalancing needs.
   - Generate proposals first; never rebalance autonomously.

4. **Stablecoin routing**
   - Compare supported routes using delivered amount, source/destination chain, fees, gas, bridge/security assumptions, expected time and quote expiry.
   - Restrict execution to verified provider contracts and existing simulation/calldata validation.
   - Present a recommendation with reasons; require explicit confirmation.

5. **Wallet intelligence**
   - Answer questions such as largest payee, spend by category, recurring counterparties and unusual activity using deterministic ledger queries.
   - The LLM may explain query results but must not invent transactions or totals.
   - Provide source transaction links for every material answer.

6. **Address risk screening**
   - Add a provider-neutral screening adapter for sanctions, scam and abuse indicators.
   - Record provider, check time, result, evidence identifiers and expiry—not unsupported allegations.
   - Screen immediately before sends and contract calls. Fail closed for configured mandatory screening.
   - Support an audited manual review path, not an invisible bypass.

7. **Contract interaction assistant**
   - Read verified contracts and allowlist supported methods initially.
   - Decode calldata, approvals, token movements, recipients and native value.
   - Simulate every call and explain effects before confirmation.
   - Reject delegatecall/proxy ambiguity, unlimited approvals, unverified ABIs or unexplained asset movement unless a separately designed expert flow safely handles them.

### Stage 5 acceptance criteria

- Template bytecode and constructor arguments are reproducible and verified in tests.
- Mint/burn actions fail when the connected wallet lacks authority.
- Treasury recommendations never execute without the normal confirmation pipeline.
- Intelligence totals exactly match ledger queries.
- Risk and simulation failures block execution according to policy and are audited.

## 7. Stage 6 — Sara Names protocol and testnet

Build the complete names system on Polygon Amoy first. The production protocol should use **one authoritative Polygon registry contract**. Helper libraries, interfaces, mocks and deployment scripts are allowed, but ownership, uniqueness, registration, renewal, expiry, transfer and subname authority must not be fragmented across multiple production registries.

### 7.1 Protocol decisions

- Use an ASCII-only canonical namespace initially: lowercase `a-z`, digits and hyphen.
- A label must not begin or end with a hyphen. Enforce an explicit length range and reserve prohibited names.
- Normalize in the client before hashing, but the contract must reject non-canonical labels itself.
- Root examples: `rohas`, `c4lab`, `me-india`. Dots separate subnames and are not permitted inside a label.
- Use deterministic `namehash`/node identifiers and document the algorithm with cross-language test vectors.
- Root names are paid and time-limited. Subnames are controlled by the parent owner under explicit policy.
- Prefer a non-upgradeable, versioned contract. Do not introduce a proxy or upgrade authority without documenting the threat model and obtaining explicit project-owner approval.

### 7.2 `SaraNamesRegistry.sol`

Implement and test at least:

- commit/reveal registration to reduce mempool front-running;
- unique root-name registration;
- root owner and expiry;
- configurable registration and renewal duration bounds;
- renewal, including renewal by a third party without changing ownership;
- grace period rules;
- transfers;
- subname creation, transfer, revocation and parent-controlled permissions;
- per-name authorised record signer/delegate;
- record-version or epoch invalidation on ownership/signing-authority changes;
- reserved-name administration with transparent events;
- fixed USDC-denominated registration/renewal prices by length/tier, using Polygon's verified native USDC address supplied and validated at deployment;
- fee recipient and safe fee withdrawal/accounting;
- pause of new registration in an emergency while preserving ownership transfers, renewals where safe and public reads;
- enumeration/indexing events sufficient for an off-chain indexer;
- ERC-165/interface support and ERC-721 compatibility if roots/subnames are represented as NFTs.

Use well-reviewed, pinned OpenZeppelin components where appropriate. Apply checks-effects-interactions, `SafeERC20`, reentrancy protection, two-step administration and least privilege. Do not permit arbitrary seizure of registered names. Clearly document any unavoidable administrator powers.

### 7.3 Signed off-chain name records

Frequently changing addresses and payment preferences remain off-chain but must be cryptographically authorised.

Define a canonical, versioned EIP-712 record containing at least:

- registry chain ID and contract address;
- node/namehash;
- record version/epoch;
- sequence number or nonce;
- issued-at and expiry timestamps;
- addresses keyed by canonical network identifier;
- preferred network and token;
- optional content hash for the complete canonical JSON record.

Requirements:

- The recovered signer must equal the current on-chain owner or current authorised record signer.
- A transfer, revocation or signer change must invalidate old records.
- Reject expired, wrong-chain, wrong-contract, wrong-node, stale-sequence and non-canonical records.
- Define canonical JSON serialisation and publish fixtures/test vectors usable by Solidity, Python and JavaScript.
- Never treat the off-chain store as authoritative; clients verify signatures against current chain state.
- Permit cache use only with bounded TTL and revalidation after ownership events.

### 7.4 Multi-chain resolution and preferences

- One name may resolve to EVM, Solana, Tron and future networks.
- Use canonical network identifiers, preferably CAIP-2, and asset identifiers, preferably CAIP-19, while preserving user-friendly labels.
- Validate address format for the selected network before saving and before payment.
- Payment preferences may specify a preferred network/token, but the payer must see and confirm the actual resolved address, chain, asset and amount.
- Resolution must fail safely if a record is ambiguous, expired, stale, invalidly signed or unsupported.
- Display name ownership and resolution source in transaction confirmation.

### 7.5 Sara integration

Implement:

- name search and availability;
- commit/reveal registration flow;
- registration and renewal checkout;
- name portfolio with expiry warnings;
- transfer and subname management;
- local signing and publication of off-chain records;
- resolver API and local resolver cache;
- recipient resolution in send, invoice, payroll and batch-payment flows;
- clear collision handling between local address-book nicknames and Sara Names;
- audit events for registration, renewal, transfer, signer changes and record publication.

### 7.6 Contract toolchain and tests

Use Foundry unless the repository already has an established Solidity toolchain. Pin compiler and dependency versions. Include:

- unit tests for every state transition and revert condition;
- fuzz tests for label validation, pricing, durations and renewals;
- invariants for uniqueness, ownership, expiry and fee accounting;
- commit/reveal front-running and replay tests;
- transfer and record invalidation tests;
- malicious ERC-20/reentrancy tests where relevant;
- gas snapshots;
- Slither/static analysis configuration;
- Amoy deployment script, environment template and source-verification command;
- generated ABI copied into the backend/frontend through a reproducible script, not manual editing.

### Stage 6 acceptance criteria

- Full test suite and static analysis pass.
- Contract is deployed and source-verified on Polygon Amoy.
- Sara can register, renew, transfer and resolve a test name end to end.
- A single name resolves correctly to tested EVM, Solana and Tron records.
- Old signed records stop resolving after transfer or signer/version change.
- Subname authority cannot exceed the parent's defined rights.
- No production/mainnet deployment occurs in Stage 6.

## 8. Stage 7 — production hardening and Sara Names launch

### Features and release work

1. **Independent contract security review**
   - Produce a threat model covering registrar abuse, front-running, expiry/grace edge cases, signature replay, stale resolution, subname authority, USDC behaviour, fee withdrawal and admin-key compromise.
   - Run static analysis, fuzzing and invariant tests.
   - Resolve every high/critical finding and document accepted lower-risk findings.
   - Obtain an independent audit before handling meaningful public registration value.

2. **Production deployment**
   - Use a hardware-controlled multisig for administrative and fee-recipient roles.
   - Reconfirm Polygon chain ID and official USDC contract from authoritative sources at deployment time.
   - Deploy deterministically where practical, verify source and publish contract address, ABI, compiler settings, constructor arguments and deployment transaction.
   - Run a low-value canary registration and renewal before opening general registration.

3. **Revenue operations**
   - Launch transparent registration and renewal pricing.
   - Show price, duration, gas and refund behaviour before confirmation.
   - Track registration/renewal revenue separately in Sara accounting.
   - Provide expiry reminders without auto-renewing unless the user has separately created and approved a recurring payment policy.

4. **Reliability and recovery**
   - Add redundant Polygon RPC providers and indexed-event recovery from a saved block cursor.
   - Handle reorgs and finality before showing ownership changes as final.
   - Document database backup/restore, resolver-cache rebuilding and registry migration strategy.
   - Monitor registration, renewal, resolver errors, webhook backlog and reconciliation lag without collecting wallet secrets or unnecessary personal data.

5. **Product completion**
   - Connect Sara Names throughout payments, invoices, merchant payment pages, payroll and address book.
   - Add payment preference selection and safe fallback when a preferred route is unavailable.
   - Finish accessibility, responsive UI, clear empty/error states and user-facing recovery instructions.
   - Update API documentation, protocol specification, operator runbook and contributor documentation.

### Stage 7 release gates

- No unresolved critical/high security finding.
- Mainnet bytecode matches reviewed source and published build settings.
- Admin privileges and multisig signers are documented.
- End-to-end mainnet canary succeeds for registration, signed-record resolution, payment by name and renewal.
- Monitoring, event reindexing, backup/restore and incident procedures are exercised.
- All repository tests pass from a clean setup using documented commands.

## 9. Minimum API surface

Use existing API conventions and adjust exact paths only to avoid conflicts.

- `/api/counterparties`
- `/api/payment-batches` and `/api/payment-batches/{id}/items`
- `/api/payment-batches/{id}/approve`
- `/api/payment-batches/{id}/execute`
- `/api/schedules`
- `/api/payroll/people` and `/api/payroll/runs`
- `/api/spending-policies`
- `/api/accounting/transactions`, `/lots`, `/reports`, `/exports`
- `/api/treasury/overview` and `/api/treasury/routes`
- `/api/tokens/deployments`, `/mint`, `/burn`, `/transfer`
- `/api/risk/screen`
- `/api/contracts/read`, `/prepare`, `/simulate`
- `/api/names/availability`, `/commit`, `/register`, `/renew`, `/transfer`
- `/api/names/{name}`, `/records`, `/subnames`
- a deliberately public, rate-limited name-resolution endpoint returning only verified public records and proof metadata.

For each endpoint define Pydantic request/response models, authentication, authorisation, idempotency behaviour, validation limits, errors and audit events. Merchant-facing endpoints remain scoped to the merchant client that created the underlying resource.

## 10. Data and privacy requirements

- Collect the minimum counterparty/customer/payroll data needed for payments.
- Keep public payment pages and public name records separate from private customer/accounting data.
- Never expose customer email, internal notes, policy decisions or payroll data through public routes.
- Encrypt newly introduced sensitive local fields where disclosure of the SQLite database would create material harm.
- Provide deletion/deactivation semantics that preserve legally/accountingly necessary transaction evidence without pretending immutable chain data was deleted.
- Logs and error responses must redact credentials, webhook secrets, passphrases, private keys, signed raw transactions and sensitive provider responses.

## 11. Definition of done

A stage is done only when:

- all listed user flows work through the UI and API;
- blockchain operations use verified on-chain state and exact amounts;
- legacy databases migrate safely and idempotently;
- authorisation, audit, events, retries and finality handling are integrated;
- negative and concurrency tests exist, not only happy-path tests;
- documentation explains configuration and operational limitations;
- all old and new tests pass;
- no placeholder, hard-coded demo response or unresolved security-critical TODO remains.

When reporting completion, be precise. List implemented capabilities, migrations, tests and external dependencies. State anything incomplete plainly. Do not proceed to Polygon mainnet deployment or spend real funds without explicit human authorisation.
