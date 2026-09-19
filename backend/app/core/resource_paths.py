"""Frozen-build-aware resolution for read-only files bundled with the app
(ABIs, contract-template JSON, images) — separate from app/core/config.py's
env/database paths, which are per-install and cwd-relative instead (see
backend/desktop_launcher.py).

Source runs resolve relative to this file's own location (backend/); a
PyInstaller-frozen build compiles modules like this one into a PYZ archive,
where __file__ has no corresponding file on disk, so frozen builds resolve
against sys._MEIPASS instead — sara-wallet.spec's `datas` mirrors the same
backend/<parts> layout there.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/app/core/ -> backend/


def backend_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS, *parts)
    return _BACKEND_DIR.joinpath(*parts)
