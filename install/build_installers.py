#!/usr/bin/env python3
"""Turns the installer templates in this folder into per-release installers.

The templates (install.sh, Install-Sara.ps1) deliberately refuse to run until
three placeholders are filled in - the release version, and the URL and
SHA-256 of that release's source archive. This script fills them in, so each
installer carries the exact hash of the code it will install, and a user can
read that hash in the script they're about to run.

Outputs (in --out):
    install.sh                     Linux / macOS, run from a terminal
    Install-Sara-Mac.zip           contains Install-Sara.command (executable)
    Install-Sara-Windows.zip       contains Install-Sara.bat + Install-Sara.ps1
    SHA256SUMS                     hashes of every file above and both sources

Used by .github/workflows/release-installers.yml; standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLACEHOLDER = re.compile(r"@@[A-Z0-9_]+@@")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace(f"@@{key}@@", value)
    leftover = PLACEHOLDER.findall(out)
    if leftover:
        raise SystemExit(f"unfilled placeholders remain: {sorted(set(leftover))}")
    return out


def crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add_file(zf: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # unix, so the mode bits below are honoured
    info.external_attr = (0o100000 | mode) << 16
    zf.writestr(info, data)


def build(version: str, tar_url: str, tar_sha: str, zip_url: str, zip_sha: str, out: Path) -> list[Path]:
    for label, digest in (("tar sha256", tar_sha), ("zip sha256", zip_sha)):
        if not SHA256.match(digest):
            raise SystemExit(f"{label} must be 64 lowercase hex characters, got {digest!r}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
        raise SystemExit(f"unsafe version string: {version!r}")
    out.mkdir(parents=True, exist_ok=True)

    unix = fill(
        (HERE / "install.sh").read_text(),
        {"SARA_VERSION": version, "SARA_SOURCE_URL": tar_url, "SARA_SOURCE_SHA256": tar_sha},
    ).replace("\r\n", "\n")
    windows_ps1 = fill(
        (HERE / "Install-Sara.ps1").read_text(),
        {"SARA_VERSION": version, "SARA_SOURCE_URL": zip_url, "SARA_SOURCE_SHA256": zip_sha},
    )
    windows_bat = (HERE / "Install-Sara.bat").read_text()

    written: list[Path] = []

    sh_path = out / "install.sh"
    sh_path.write_bytes(unix.encode("utf-8"))
    sh_path.chmod(0o755)
    written.append(sh_path)

    mac_zip = out / "Install-Sara-Mac.zip"
    with zipfile.ZipFile(mac_zip, "w") as zf:
        add_file(zf, "Install-Sara.command", unix.encode("utf-8"), 0o755)
    written.append(mac_zip)

    win_zip = out / "Install-Sara-Windows.zip"
    with zipfile.ZipFile(win_zip, "w") as zf:
        add_file(zf, "Install-Sara.bat", crlf(windows_bat), 0o644)
        add_file(zf, "Install-Sara.ps1", crlf(windows_ps1), 0o644)
    written.append(win_zip)

    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True, help="release tag, e.g. alpha-17")
    ap.add_argument("--tar", required=True, type=Path, help="the release's .tar.gz source archive")
    ap.add_argument("--tar-url", required=True)
    ap.add_argument("--zip", required=True, type=Path, help="the release's .zip source archive")
    ap.add_argument("--zip-url", required=True)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    written = build(
        args.version,
        args.tar_url, sha256_file(args.tar),
        args.zip_url, sha256_file(args.zip),
        args.out,
    )
    lines = [f"{sha256_file(p)}  {p.name}" for p in [*written, args.tar, args.zip]]
    (args.out / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
