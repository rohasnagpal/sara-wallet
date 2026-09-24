#!/bin/sh
# ============================================================================
# Sara Wallet installer - macOS (Apple Silicon) and Linux (x86_64 / arm64)
#
# Read this before you run it. What it does, in order:
#   1. Downloads a pinned copy of `uv` (a small Python package manager) from
#      github.com/astral-sh/uv and checks it against a SHA-256 hash written
#      into this file.
#   2. Downloads this Sara release's source code and checks it against the
#      SHA-256 hash written into this file. A mismatch aborts the install.
#   3. Uses uv to fetch a private copy of Python 3.12 and installs Sara's
#      reviewed, version-locked dependencies (backend/requirements-lock.txt)
#      as prebuilt wheels only - nothing is compiled, no build scripts run.
#   4. Writes a launcher (and, on macOS, a small Sara.app in ~/Applications
#      that this script creates itself, so macOS has nothing to quarantine).
#
# Everything is kept in ~/.sara-wallet. It does not use sudo, does not touch
# your shell profile or PATH, and does not touch your wallet data, which is
# stored separately. Run with --uninstall to remove it again.
# ============================================================================
set -eu

# --- Filled in by install/build_installers.py when a release is published. --
SARA_VERSION="@@SARA_VERSION@@"
SARA_SOURCE_URL="@@SARA_SOURCE_URL@@"
SARA_SOURCE_SHA256="@@SARA_SOURCE_SHA256@@"
# ---------------------------------------------------------------------------

# Pinned uv build. Hashes are from astral-sh/uv's own release files and were
# re-computed from the downloaded archives before being written here.
UV_VERSION="0.12.18"
UV_SHA256_AARCH64_DARWIN="cf40e0c6a202190ccd9e0406dcfdd5b2d6668a9a5c779b17948963df32aafe5b"
UV_SHA256_X86_64_LINUX="89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16"
UV_SHA256_AARCH64_LINUX="afb6291f3f0a6b4521fc67b947822506c41dde5b60d2189dd8f3695b2ac8c9e7"

PORT=8888
ROOT="${SARA_HOME:-$HOME/.sara-wallet}"
CURL_PROTO="=https"

case "$SARA_VERSION$SARA_SOURCE_URL$SARA_SOURCE_SHA256" in
  *@@*)
    echo "This is the unfilled installer template, not a release installer." >&2
    echo "Download the installer from https://github.com/rohasnagpal/sara-wallet/releases" >&2
    exit 2
    ;;
esac

PAUSE=0
case "$0" in *.command) PAUSE=1 ;; esac
work=""

cleanup() {
  rc=$?
  if [ -n "$work" ] && [ -d "$work" ]; then rm -rf "$work"; fi
  if [ "$PAUSE" = 1 ]; then
    printf '\nPress Return to close this window. '
    read -r _ </dev/tty 2>/dev/null || true
  fi
  exit "$rc"
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  say "Usage: $0 [--uninstall]"
  say "  (no arguments)  install or update Sara ${SARA_VERSION}"
  say "  --uninstall     remove the installed program; your wallet data is kept"
}

UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

os="$(uname -s)"
arch="$(uname -m)"
# A terminal running under Rosetta reports x86_64 even on Apple Silicon.
if [ "$os" = "Darwin" ] && [ "$arch" = "x86_64" ] \
   && [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then
  arch="arm64"
fi

case "$os/$arch" in
  Darwin/arm64)
    uv_triple="aarch64-apple-darwin"; uv_sha="$UV_SHA256_AARCH64_DARWIN"
    data_dir="$HOME/Library/Application Support/Sara" ;;
  Linux/x86_64)
    uv_triple="x86_64-unknown-linux-gnu"; uv_sha="$UV_SHA256_X86_64_LINUX"
    data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/Sara" ;;
  Linux/aarch64|Linux/arm64)
    uv_triple="aarch64-unknown-linux-gnu"; uv_sha="$UV_SHA256_AARCH64_LINUX"
    data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/Sara" ;;
  Darwin/x86_64)
    die "Intel Macs aren't supported by this installer yet: one of Sara's required libraries (ckzg) publishes no prebuilt Intel-Mac build, and this installer never compiles code. Use the Docker or run-from-source instructions in the README instead." ;;
  *)
    die "Unsupported system: $os/$arch. See the README for Docker and run-from-source instructions." ;;
esac

# Wallet data must never live inside the folder an uninstall deletes.
case "$data_dir/" in
  "$ROOT"/*) die "The install folder ($ROOT) contains the wallet-data folder ($data_dir). Refusing to continue so wallet data can't be deleted." ;;
esac
case "$ROOT/" in
  "$data_dir"/*) die "The install folder ($ROOT) is inside the wallet-data folder ($data_dir). Refusing to continue." ;;
esac

sara_running() { curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  if [ -d "$ROOT" ] && [ ! -f "$ROOT/.sara-wallet-root" ]; then
    die "$ROOT exists but wasn't created by this installer, so it won't be deleted."
  fi
  if sara_running; then die "Sara is running. Close it first, then run this again."; fi
  rm -rf "$ROOT"
  app="$HOME/Applications/Sara.app"
  if [ -f "$app/Contents/Info.plist" ] && grep -q "com.sarawallet.launcher" "$app/Contents/Info.plist" 2>/dev/null; then
    rm -rf "$app"
  fi
  say "Sara has been removed."
  say "Your wallet data was NOT deleted. It is still at:"
  say "  $data_dir"
  say "Keep it - together with your recovery phrase it is the only way to recover your wallets."
  exit 0
fi

# ------------------------------------------------------------------ install
command -v curl >/dev/null 2>&1 || die "curl is required but wasn't found."
command -v tar >/dev/null 2>&1 || die "tar is required but wasn't found."

if sara_running; then
  die "Something is already running on port $PORT (probably Sara). Close it first, then run this installer again."
fi

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else die "Neither shasum nor sha256sum was found, so downloads can't be verified. Not continuing."
  fi
}

verify() {
  actual="$(sha256_of "$1")"
  if [ "$actual" != "$2" ]; then
    die "Checksum mismatch for $3.
  expected: $2
  got:      $actual
The download was corrupted or tampered with. Nothing has been installed."
  fi
}

fetch() {
  curl -fL --proto "$CURL_PROTO" --proto-redir "$CURL_PROTO" --tlsv1.2 \
    --retry 3 --connect-timeout 20 --progress-bar -o "$2" "$1" \
    || die "Couldn't download $1"
}

mkdir -p "$ROOT"
touch "$ROOT/.sara-wallet-root"
rm -rf "$ROOT"/.tmp.* 2>/dev/null || true   # leftovers from an interrupted run
work="$(mktemp -d "$ROOT/.tmp.XXXXXX")"

say "Installing Sara $SARA_VERSION into $ROOT"
say ""

# ---- uv
UV="$ROOT/bin/uv"
if [ -x "$UV" ] && "$UV" --version 2>/dev/null | grep -q "^uv $UV_VERSION "; then
  say "[1/4] uv $UV_VERSION already present."
else
  say "[1/4] Downloading uv $UV_VERSION (Python package manager)..."
  fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$uv_triple.tar.gz" "$work/uv.tar.gz"
  verify "$work/uv.tar.gz" "$uv_sha" "uv $UV_VERSION"
  mkdir -p "$work/uv" "$ROOT/bin"
  tar -xzf "$work/uv.tar.gz" -C "$work/uv"
  cp "$work/uv/uv-$uv_triple/uv" "$ROOT/bin/uv.new"
  chmod 755 "$ROOT/bin/uv.new"
  mv -f "$ROOT/bin/uv.new" "$UV"
fi

# ---- Sara source + dependencies
TARGET="$ROOT/versions/$SARA_VERSION"
VENV_PY="$TARGET/.venv/bin/python"

if [ -f "$TARGET/.installed" ]; then
  say "[2/4] Sara $SARA_VERSION source already installed."
  say "[3/4] Dependencies already installed."
else
  rm -rf "$TARGET"
  say "[2/4] Downloading Sara $SARA_VERSION..."
  fetch "$SARA_SOURCE_URL" "$work/sara.tar.gz"
  verify "$work/sara.tar.gz" "$SARA_SOURCE_SHA256" "Sara $SARA_VERSION"
  mkdir -p "$work/src" "$ROOT/versions"
  tar -xzf "$work/sara.tar.gz" -C "$work/src"
  [ "$(ls "$work/src" | wc -l | tr -d ' ')" = "1" ] || die "Unexpected layout in the Sara download."
  mv "$work/src/$(ls "$work/src")" "$TARGET"
  [ -f "$TARGET/backend/desktop_launcher.py" ] && [ -f "$TARGET/backend/requirements-lock.txt" ] \
    || die "The Sara download is missing expected files."

  say "[3/4] Installing Python 3.12 and Sara's dependencies (a few minutes the first time)..."
  export UV_PYTHON_INSTALL_DIR="$ROOT/python"
  export UV_CACHE_DIR="$ROOT/cache"
  "$UV" venv --no-config --managed-python --python 3.12 "$TARGET/.venv" \
    || die "Couldn't set up Python 3.12."
  "$UV" pip install --no-config --python "$VENV_PY" --only-binary :all: \
    -r "$TARGET/backend/requirements-lock.txt" \
    || die "Couldn't install Sara's dependencies."
  "$UV" pip check --python "$VENV_PY" || die "Installed dependencies are inconsistent."
  "$VENV_PY" -c "import fastapi, uvicorn, web3, eth_account, mnemonic, platformdirs" \
    || die "Installed Python environment failed its import check."
  touch "$TARGET/.installed"
fi

# ---- launcher
say "[4/4] Creating the launcher..."
cat > "$ROOT/sara.new" <<EOF
#!/bin/sh
exec "$VENV_PY" "$TARGET/backend/desktop_launcher.py" "\$@"
EOF
chmod 755 "$ROOT/sara.new"
mv -f "$ROOT/sara.new" "$ROOT/sara"

if [ "$os" = "Darwin" ]; then
  app="$HOME/Applications/Sara.app"
  mkdir -p "$HOME/Applications"
  rm -rf "$app.new"
  mkdir -p "$app.new/Contents/MacOS"
  cat > "$app.new/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Sara</string>
  <key>CFBundleDisplayName</key><string>Sara</string>
  <key>CFBundleIdentifier</key><string>com.sarawallet.launcher</string>
  <key>CFBundleExecutable</key><string>Sara</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
EOF
  cat > "$app.new/Contents/MacOS/Sara" <<EOF
#!/bin/sh
exec "$ROOT/sara" >>"$ROOT/sara.log" 2>&1
EOF
  chmod 755 "$app.new/Contents/MacOS/Sara"
  rm -rf "$app"
  mv "$app.new" "$app"
fi

# ---- tidy up: older versions (wallet data lives elsewhere, so this is safe)
for old in "$ROOT"/versions/*; do
  [ -d "$old" ] || continue
  [ "$old" = "$TARGET" ] || rm -rf "$old"
done

rm -rf "$work"
work=""

py_data_dir="$("$VENV_PY" -c "import platformdirs; print(platformdirs.user_data_dir('Sara', 'SaraWallet'))" 2>/dev/null || echo "$data_dir")"

say ""
say "Sara $SARA_VERSION is installed."
say ""
say "  Your wallet data:  $py_data_dir"
say "  (never touched by updates or --uninstall)"
say ""
say "  The first time you create a wallet, Sara shows a 24-word recovery"
say "  phrase. Write it down and keep it offline - it is the only way to"
say "  recover your wallets."
say ""
if [ "$os" = "Darwin" ]; then
  say "  To start Sara later: open Sara from ~/Applications (or Launchpad)."
else
  say "  To start Sara later: $ROOT/sara"
fi
say "  To update: run the installer from a newer release."
say "  To remove: run this installer again with --uninstall."
say ""

[ "${SARA_NO_LAUNCH:-0}" = "1" ] && exit 0

if [ "$os" = "Darwin" ]; then
  say "Starting Sara - your browser will open automatically..."
  open "$HOME/Applications/Sara.app"
  i=0
  while [ "$i" -lt 90 ]; do
    if sara_running; then say "Sara is running at http://127.0.0.1:$PORT"; exit 0; fi
    i=$((i + 1))
    sleep 1
  done
  say "Sara is still starting. If your browser doesn't open, check $ROOT/sara.log"
  exit 0
fi

say "Starting Sara (press Ctrl+C to stop it)..."
PAUSE=0
trap - EXIT
exec "$ROOT/sara"
