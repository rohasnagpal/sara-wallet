# Security Policy

Sara Wallet is self-custodial software that handles private keys and signs
transactions on your behalf. We take vulnerabilities in that code seriously
and appreciate responsible disclosure.

## Supported Versions

Sara Wallet is pre-1.0 alpha software distributed only as source, with a
single active line of development on `main` — there are no maintained
release branches or LTS versions.

| Version           | Supported          |
| ------------------ | ------------------- |
| `main` (latest)    | ✅ Yes               |
| Tagged alpha builds (`alpha-*`) | ❌ No, superseded by `main` |

If you're running anything other than the current `main`, update before
reporting — the issue may already be fixed.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately using [GitHub's private vulnerability reporting](https://github.com/rohasnagpal/sara-wallet/security/advisories/new)
("Report a vulnerability" button under the repo's Security tab). This opens
a private advisory visible only to you and the maintainer, and lets us
collaborate on a fix before anything is public.

Include, as applicable:
- A description of the vulnerability and its potential impact
- Steps to reproduce, or a proof-of-concept
- Affected file(s)/commit
- Any suggested fix or mitigation you have in mind

We aim to acknowledge new reports within **5 business days** and to give you
a status update (triage result, expected timeline) within **14 days**.

## What Not to Report Publicly

Do not open public issues, discussions, PRs, or social media posts describing
any of the following before it's fixed and disclosed:
- Ways to extract, leak, or exfiltrate private keys or seed phrases
- Ways to bypass the wallet lock/passphrase or session expiry
- Remote code execution, injection, or auth-bypass vulnerabilities
- Ways to forge, replay, or tamper with signed transactions
- Any working exploit against a deployed instance of Sara Wallet

General security *hardening suggestions* with no working exploit (e.g. "this
dependency has a newer version," "this header is missing") are fine as
regular issues or PRs.

## Key-Handling Principles

These are the invariants any vulnerability report or fix is judged against:
- Private keys and seed phrases are encrypted (AES-256-GCM) at rest and are
  never logged, printed, or included in error messages or telemetry
- Key material never leaves your device — Sara Wallet does not transmit keys
  or seed phrases to any server, including its own backend's remote calls
- The decrypted key/session only exists in memory for an unlocked session,
  which auto-expires after inactivity
- Key-handling logic lives in `backend/app/tools/wallet/encrypt.py` and
  `lock.py` so it can be reviewed in one place — see [CONTRIBUTING.md](CONTRIBUTING.md#security)
  if you're contributing changes there

A vulnerability that violates any of the above is treated as critical.

## Disclosure Policy

We follow coordinated disclosure:
1. You report privately (see above).
2. We confirm, fix, and prepare a release/commit.
3. Once a fix is available, we publish a GitHub Security Advisory crediting
   the reporter (unless you prefer to stay anonymous) and describing the
   issue at a level of detail appropriate to protect users who haven't
   updated yet.
4. If we're unresponsive for more than 90 days, you're free to disclose
   publicly — we'd just ask you to make a good-faith effort to reach us
   first through multiple channels.

## Audit Status

Sara Wallet has **not** undergone a third-party professional security audit.
It is alpha software built and reviewed by its contributors and the open
source community, not an audited financial product. See [DISCLAIMER.md](DISCLAIMER.md)
for the full terms under which the software is provided. Treat it
accordingly — do not store amounts you cannot afford to lose, and review the
code yourself before relying on it.

## Bug Bounty

There is currently **no bug bounty program** and no monetary reward is
offered for vulnerability reports. We do publicly credit reporters (with
permission) in the resulting security advisory.

## Dependency & Supply-Chain Security Process

- Runtime dependencies are pinned exactly in `backend/requirements-lock.txt`;
  `backend/requirements.txt` is the open-ended developer manifest the lock
  file is generated from
- Every push and pull request runs CI (`.github/workflows/security.yml`)
  that installs the locked dependencies, runs `pip check` for consistency,
  runs the backend security regression test suite, and runs
  [`pip-audit`](https://github.com/pypa/pip-audit) against the lock file,
  failing the build on any known CVE
- New dependencies require discussion (open an issue first) before being
  added — see [CONTRIBUTING.md](CONTRIBUTING.md)
- If `pip-audit` flags a dependency, the fix is to bump the pinned version
  in `requirements-lock.txt` to a patched release, verify it resolves
  cleanly and tests still pass, then commit the lock file change
