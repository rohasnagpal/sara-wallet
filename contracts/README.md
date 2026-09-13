# Sara contracts

Foundry project for Sara's on-chain templates: the two ERC-20 templates
behind Sara's token creator (`CLAUDE_STAGES_3_TO_7.md` Stage 5.1) and the
Sara Names registry (Stage 6).

## Toolchain

- Foundry (`forge`/`cast`/`anvil`), installed via `foundryup`.
- Solidity `0.8.28`, pinned in `foundry.toml` (`solc_version`), optimizer on
  (200 runs), `bytecode_hash = "none"` for deterministic bytecode across
  machines.
- OpenZeppelin Contracts `v5.7.0`, vendored under `lib/openzeppelin-contracts`
  (trimmed to just `contracts/` + `LICENSE` — its own test/docs/audits
  directories are dropped since they're not needed to compile against, and
  vendoring means Sara never depends on GitHub still hosting a given commit).

## Templates

- `src/FixedSupplyToken.sol` — the entire supply mints once, to a chosen
  owner, at deployment. No mint/burn/owner/pause capability exists
  afterwards — zero centralisation risk.
- `src/MintableBurnableCappedToken.sol` — owner may mint up to an immutable
  cap; any holder may burn their own balance (standard `ERC20Burnable`
  semantics — the owner cannot force-burn someone else's balance). Minting
  authority is a real, disclosed centralisation risk.

## Sara Names registry (Stage 6)

`src/SaraNamesRegistry.sol` — one authoritative Polygon registry for Sara
Names. Commit/reveal root registration (fixed USDC price by label-length
tier), a 90-day grace period on expiry before a name is registrable by
anyone else, third-party renewal without changing ownership, parent-owner-
controlled subnames, and a per-node record signer/epoch that off-chain
EIP-712 signed records (addresses, payment preferences — see
`backend/app/tools/names/eip712_records.py`) are checked against. Roots and
subnames are plain custom-owned, **not** ERC-721 — a deliberate simplicity
choice; ERC-165 is still supported for a custom interface ID. Full design
rationale and every accepted static-analysis finding: `SECURITY.md`.

Prepared for deployment to **Polygon Amoy testnet only** — see `script/DeployAmoy.s.sol`,
which refuses to run against any chain ID other than `80002`. No mainnet
deployment happens in this stage.

## Build, test, analyze, export

```
forge build
forge test              # unit + fuzz + invariant + cross-language namehash vectors
forge snapshot           # gas snapshot -> .gas-snapshot
.slither-venv/bin/slither src/SaraNamesRegistry.sol --config-file slither.config.json
python3 scripts/export_artifacts.py
```

(`.slither-venv` is a local, dev-only Python venv — `python3 -m venv
.slither-venv && .slither-venv/bin/pip install slither-analyzer` — kept
separate from `backend/.venv` since Slither is a contracts-only tool.)

`export_artifacts.py` copies each token template's ABI + bytecode +
compiler version + source SHA-256 into
`backend/app/tools/tokens/templates/<template_id>.json` (loaded by
`backend/app/services/token_factory.py`), and the registry's ABI + compiler
version + source SHA-256 into `backend/app/tools/names/registry_abi.json`
(loaded by `backend/app/tools/names/sara_names.py` — no bytecode needed
there, since Sara never deploys the registry itself at runtime, only talks
to the address `DeployAmoy.s.sol` produced). This is the "generated ABI
copied into the backend through a reproducible script, not manual editing"
step the doc requires — re-run it (and commit the regenerated JSON) any
time a contract source file changes; never hand-edit those JSON files.

## Deploying to Amoy

```
cp .env.example .env   # fill in DEPLOYER_PRIVATE_KEY (funded via a public Amoy faucet), AMOY_USDC_ADDRESS, POLYGONSCAN_API_KEY
source .env
forge script script/DeployAmoy.s.sol:DeployAmoy \
  --rpc-url "$AMOY_RPC_URL" --broadcast --verify \
  --etherscan-api-key "$POLYGONSCAN_API_KEY" -vvvv
```

`AMOY_USDC_ADDRESS` in `.env.example` (`0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582`)
was confirmed on-chain before use (queried `name()`/`symbol()`/`decimals()`
against Amoy directly — returned `USDC`/`USDC`/`6`), not copied from an
unverified source.
