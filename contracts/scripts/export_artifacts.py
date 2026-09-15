#!/usr/bin/env python3
"""Copies compiled ABI + bytecode for Sara's ERC-20 templates from Foundry's
`out/` build output into the backend, where app/services/token_factory.py
loads them at deployment time.

This is the "reproducible script, not manual editing" step
CLAUDE_STAGES_3_TO_7.md requires for generated ABI/bytecode (Stage 5.1). Run
after `forge build`:

    cd contracts && forge build && python3 scripts/export_artifacts.py

(The Sara Names registry contract and its own ABI-export script now live in
a separate repo - see backend/app/tools/names/sara_names.py's module
docstring.)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parent.parent
BACKEND_TEMPLATES_DIR = CONTRACTS_DIR.parent / "backend" / "app" / "tools" / "tokens" / "templates"

# (contract source file, contract name, output template id)
TEMPLATES = (
    ("FixedSupplyToken.sol", "FixedSupplyToken", "fixed_supply"),
    ("MintableBurnableCappedToken.sol", "MintableBurnableCappedToken", "mintable_burnable_capped"),
)


def export_one(source_file: str, contract_name: str, template_id: str) -> None:
    artifact_path = CONTRACTS_DIR / "out" / source_file / f"{contract_name}.json"
    if not artifact_path.exists():
        raise SystemExit(f"Missing build artifact {artifact_path} - run `forge build` first.")
    data = json.loads(artifact_path.read_text())
    source_path = CONTRACTS_DIR / "src" / source_file
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

    out = {
        "template_id": template_id,
        "contract_name": contract_name,
        "compiler_version": data["metadata"]["compiler"]["version"],
        "source_file": source_file,
        "source_sha256": source_hash,
        "abi": data["abi"],
        "bytecode": data["bytecode"]["object"],
        "method_identifiers": data.get("methodIdentifiers", {}),
    }
    BACKEND_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKEND_TEMPLATES_DIR / f"{template_id}.json"
    dest.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"wrote {dest.relative_to(CONTRACTS_DIR.parent)} ({len(out['bytecode'])} bytecode chars, source sha256={source_hash[:12]}...)")


def main() -> None:
    for source_file, contract_name, template_id in TEMPLATES:
        export_one(source_file, contract_name, template_id)


if __name__ == "__main__":
    main()
