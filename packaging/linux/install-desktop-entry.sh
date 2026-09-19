#!/usr/bin/env bash
# Registers Sara in your desktop's application menu, so it shows up as a
# normal double-clickable app instead of needing `./sara-wallet` from a
# terminal every time. Run this once after unzipping the release build:
#
#   ./install-desktop-entry.sh
#
# Safe to re-run (e.g. after moving the unzipped folder) — it always
# regenerates the entry pointing at wherever this script currently lives.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXECUTABLE="$SCRIPT_DIR/sara-wallet"

if [ ! -x "$EXECUTABLE" ]; then
    echo "Expected to find an executable 'sara-wallet' next to this script at:" >&2
    echo "  $EXECUTABLE" >&2
    echo "Did you move this script out of the unzipped release folder?" >&2
    exit 1
fi

APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DEST="$APPS_DIR/sara-wallet.desktop"
mkdir -p "$APPS_DIR"

sed "s|__SARA_WALLET_EXEC__|$EXECUTABLE|" "$SCRIPT_DIR/sara-wallet.desktop" > "$DEST"
chmod 644 "$DEST"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" || true

echo "Installed. Sara AI Wallet should now appear in your application menu"
echo "(pointing at $EXECUTABLE)."
