# Sara contracts

Foundry project for Sara's on-chain ERC-20 templates behind the wallet's
token creator (`CLAUDE_STAGES_3_TO_7.md` Stage 5.1).

(The Sara Names registry contract - `SaraNamesRegistry.sol` and its
tests/deploy script/security docs - now lives in a separate `bname` repo.
The wallet backend still talks to it as a client only:
`backend/app/tools/names/sara_names.py`, `backend/app/routers/names.py`,
and the already-exported `backend/app/tools/names/registry_abi.json`.)

## Toolchain

- Foundry (`forge`/`cast`/`anvil`), installed via `foundryup`.
- Solidity `0.8.28`, pinned in `foundry.toml` (`solc_version`), optimizer on
  (200 runs), `bytecode_hash = "none"` for deterministic bytecode across
  machines.
- OpenZeppelin Contracts `v5.7.0`, vendored under `lib/openzeppelin-contracts`
  (trimmed to just `contracts/` + `LICENSE` - its own test/docs/audits
  directories are dropped since they're not needed to compile against, and
  vendoring means Sara never depends on GitHub still hosting a given commit).

## Templates

- `src/FixedSupplyToken.sol` - the entire supply mints once, to a chosen
  owner, at deployment. No mint/burn/owner/pause capability exists
  afterwards - zero centralisation risk.
- `src/MintableBurnableCappedToken.sol` - owner may mint up to an immutable
  cap; any holder may burn their own balance (standard `ERC20Burnable`
  semantics - the owner cannot force-burn someone else's balance). Minting
  authority is a real, disclosed centralisation risk.

## Build, test, analyze, export

```
forge build
forge test              # unit tests for both templates
forge snapshot           # gas snapshot -> .gas-snapshot
python3 scripts/export_artifacts.py
```

`export_artifacts.py` copies each token template's ABI + bytecode +
compiler version + source SHA-256 into
`backend/app/tools/tokens/templates/<template_id>.json` (loaded by
`backend/app/services/token_factory.py`). This is the "generated ABI
copied into the backend through a reproducible script, not manual editing"
step the doc requires - re-run it (and commit the regenerated JSON) any
time a template's source file changes; never hand-edit those JSON files.
