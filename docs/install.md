# Installing Sara

The easiest way is the one-click installer attached to each
[release](https://github.com/rohasnagpal/sara-wallet/releases). It needs no
Python, no terminal knowledge and no administrator rights.

| You have | Download | Then |
|---|---|---|
| **Mac (Apple Silicon: M1 or newer)** | `Install-Sara-Mac.zip` | Unzip, right-click `Install-Sara.command` → **Open** → **Open** |
| **Windows 10/11 (64-bit)** | `Install-Sara-Windows.zip` | Right-click the zip → **Extract All…**, double-click `Install-Sara.bat` |
| **Linux (x86_64 or arm64)** | `install.sh` | `sh install.sh` |

The first install takes a few minutes (it downloads a private copy of Python
and Sara's libraries). When it finishes, Sara starts and opens in your
browser. Afterwards:

- **Mac:** open **Sara** from `~/Applications` or Launchpad.
- **Windows:** open **Sara** from the Start Menu. Leave its window open while you use Sara; closing it stops Sara.
- **Linux:** run `~/.sara-wallet/sara`.

**Update:** download the installer from a newer release and run it. Your
wallets are untouched.
**Uninstall:** run the installer again with `--uninstall` (Windows:
`Install-Sara.bat -Uninstall` from a terminal). Your wallet data is never
deleted by an install, update or uninstall.

## Why you'll see a warning, and what to do

Sara's installers are not code-signed yet (signing certificates cost money
and need a registered organisation; it's planned). So:

- **macOS** says the file "can't be opened because Apple cannot check it for
  malicious software". Right-click → **Open** → **Open** (once). On recent
  macOS versions you may instead need **System Settings → Privacy & Security →
  Open Anyway**. The `Sara` app the installer then creates on your Mac is
  made locally, so it opens without any warning.
- **Windows** may show "Windows protected your PC" or "publisher could not be
  verified". Choose **More info → Run anyway**.

Don't run an installer you can't read. That's the reason these are plain
scripts and not opaque programs: open `Install-Sara.command` or
`Install-Sara.ps1` in any text editor and read exactly what it does. The
header lists every step.

## What the installer does (and doesn't)

1. Downloads a **pinned version of [uv](https://github.com/astral-sh/uv)** (a
   small Python package manager) and checks it against a SHA-256 hash written
   into the script.
2. Downloads **that release's source code** and checks it against a SHA-256
   hash written into the script. If the hash doesn't match, it stops and
   installs nothing. (The hash is filled in when the release is published, so
   the script you hold can only ever install the exact code it was built for.)
3. Has uv fetch a private Python 3.12, then installs Sara's
   [reviewed lockfile](../backend/requirements-lock.txt) as **prebuilt wheels
   only** — nothing is compiled and no package build scripts run.
4. Creates a launcher (and on macOS a small `Sara.app`, and on Windows a Start
   Menu shortcut).

It does **not** use `sudo` or administrator rights, change `PATH`, edit your
shell profile or the registry, or send anything anywhere. Everything lives in
one folder:

| | Program | Your wallet data (separate, never deleted) |
|---|---|---|
| macOS | `~/.sara-wallet` | `~/Library/Application Support/Sara` |
| Linux | `~/.sara-wallet` | `~/.local/share/Sara` |
| Windows | `%LOCALAPPDATA%\SaraWalletApp` | `%LOCALAPPDATA%\SaraWallet\Sara` |

Two honest limits: the lockfile pins versions but doesn't pin per-file
hashes, and the installer is unsigned, so the hash-check-before-use design
above is what protects you, plus the fact that you can read every line.

## Not supported by the installer

- **Intel Macs** and **Windows on ARM.** One of Sara's required libraries
  (`ckzg`, needed by `eth-account`) publishes no prebuilt build for them, and
  the installer deliberately never compiles code. Use Docker or run from
  source (see the [README](../README.md#quick-start)).
- Linux distributions without glibc (e.g. Alpine).

## Back up your recovery phrase

The first time you create a wallet, Sara shows a 24-word recovery phrase.
Write it down and keep it offline. It is the only way to recover your wallets
if this computer is lost. See [security-model.md](security-model.md).
