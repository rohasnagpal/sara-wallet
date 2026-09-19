"""Desktop launcher entrypoint for the PyInstaller-packaged build.

Not used when running Sara from source (`uvicorn main:app`) — this exists
only so a packaged, double-clicked binary has a real place to keep its
wallet database and config, and opens a browser on its own instead of
requiring a terminal.

Layout inside the user's OS data directory (created on first run):

    <user-data-dir>/
        .env.local      # optional advanced config; Settings screen covers
                         # the one required key (OpenRouter) without this
        backend/
            sara.db     # wallet database — cwd-relative default in
                         # app/core/config.py resolves here once we chdir

This mirrors the source tree's own relative layout (repo-root/.env.local,
repo-root/backend/ as cwd) so app/core/config.py's existing
`env_file="../.env.local"` and `DATABASE_URL`'s cwd-relative default work
completely unmodified, whether run from source or frozen.
"""
import os
import sys
import time
import shutil
import socket
import threading
import webbrowser
import urllib.error
import urllib.request

import platformdirs

APP_NAME = "Sara"
APP_AUTHOR = "SaraWallet"
HOST = "127.0.0.1"
PORT = 8888


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def _bundle_root() -> str:
    """Where this launcher's own code/data lives — sys._MEIPASS when
    frozen, this file's directory (backend/) otherwise."""
    if _frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _env_template_path() -> str:
    """The tracked .env template to seed a first-run .env.local from.
    Bundled at the archive root (see sara-wallet.spec); one level above
    backend/ in the source tree."""
    if _frozen():
        return os.path.join(_bundle_root(), ".env")
    return os.path.join(_bundle_root(), "..", ".env")


def _prepare_data_dir() -> str:
    """Create <user-data-dir>/backend, seed .env.local if missing, and
    return the backend/ path to chdir into."""
    root = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)
    backend_dir = os.path.join(root, "backend")
    os.makedirs(backend_dir, exist_ok=True)

    env_local = os.path.join(root, ".env.local")
    if not os.path.exists(env_local):
        template = _env_template_path()
        try:
            if os.path.isfile(template):
                shutil.copyfile(template, env_local)
        except OSError:
            pass  # best-effort — Settings screen can configure the
                  # required OpenRouter key at runtime regardless

    return backend_dir


def _health_ok(timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_is_free() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((HOST, PORT)) != 0


def _open_browser_once_ready() -> None:
    # Heavy first-run imports (litellm, web3, pandas, ...) and startup
    # migrations can take a few seconds — poll rather than guess a sleep.
    for _ in range(60):
        if _health_ok():
            webbrowser.open(f"http://{HOST}:{PORT}/")
            return
        time.sleep(0.5)


def main() -> None:
    if not _port_is_free():
        if _health_ok():
            print(f"Sara is already running at http://{HOST}:{PORT} — opening it.")
            webbrowser.open(f"http://{HOST}:{PORT}/")
            return
        print(
            f"Port {PORT} is already in use by something else on this machine.\n"
            "Close whatever's using it, then relaunch Sara."
        )
        sys.exit(1)

    backend_dir = _prepare_data_dir()
    os.chdir(backend_dir)

    # sys.path needs backend_dir's *source* counterpart (bundle root), not
    # the freshly-created user-data backend_dir, to actually find main.py.
    sys.path.insert(0, _bundle_root())

    # Imported only after chdir: app/core/config.py's Settings() reads
    # env_file="../.env.local" relative to the process cwd at import time.
    import uvicorn
    import main as sara_app  # backend/main.py — the existing FastAPI app

    threading.Thread(target=_open_browser_once_ready, daemon=True).start()
    print(f"Sara is starting at http://{HOST}:{PORT} — your browser will open automatically.")
    uvicorn.run(sara_app.app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
