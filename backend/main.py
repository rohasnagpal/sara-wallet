from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
from app.db.session import init_db, SessionLocal, engine
from app.db.models import Config
from app.routers import chat, wallets, market, portfolio, settings, address_book, intelligence, lock, tokens, payments, proofs, system, ledger, safety, payment_batches, schedules, payroll, spending_policies, accounting, treasury, risk, names, x402, x402_paywall, aave
from app.tools.wallet import lock as lock_state
from app.core.session_auth import LAUNCH_TOKEN
from app.core.access import ensure_local_owner
from app.core.events import process_pending
from app.db.migrations import run_migrations
import os
import sys
import secrets
import asyncio
import logging

def _resource_path(name: str) -> str:
    """Resolve a bundled resource by logical name, PyInstaller-aware.

    Frozen builds compile main.py into a PYZ archive, so __file__-relative
    lookups (the source-tree layout below) point at a synthetic path with no
    file on disk. sys._MEIPASS is the real, extracted bundle directory
    PyInstaller sets at runtime — sara-wallet.spec's `datas` places both
    index.html and images/ directly under it, so frozen lookups are always
    one level shallower than their source-tree equivalents.
    """
    frozen = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    if name == "index.html":
        return os.path.join(sys._MEIPASS, "index.html") if frozen \
            else os.path.join(os.path.dirname(__file__), "..", "index.html")
    if name == "images":
        return os.path.join(sys._MEIPASS, "images") if frozen \
            else os.path.join(os.path.dirname(__file__), "images")
    if name == "fonts":
        return os.path.join(sys._MEIPASS, "fonts") if frozen \
            else os.path.join(os.path.dirname(__file__), "fonts")
    raise ValueError(f"Unknown bundled resource: {name!r}")

def _load_db_config():
    """Override os.environ with any keys saved in the Config table."""
    try:
        db = SessionLocal()
        rows = db.query(Config).all()
        for row in rows:
            if row.value:
                os.environ[row.key] = row.value
        db.close()
    except Exception:
        pass

def _clear_legacy_config_rows():
    """Remove settings that are no longer supported or user-configurable."""
    try:
        db = SessionLocal()
        legacy_keys = ("SARA_MASTER_KEY", "HELIUS_RPC", "TRONGRID_API_KEY", "CRYPTOPANIC_API_KEY")
        rows = db.query(Config).filter(Config.key.in_(legacy_keys)).all()
        for row in rows:
            db.delete(row)
        if rows:
            db.commit()
        db.close()
    except Exception:
        pass


def _run_foundation_cycle() -> None:
    from app.services.transaction_monitor import check_transactions
    from app.services.balance_monitor import check_balance_monitors
    from app.services.activity_indexer import index_wallet_activity
    from app.services.schedules import materialize_due_schedules
    from app.services.token_factory import check_pending_deployments
    from app.services.names_indexer import sync_events as sync_name_events, check_expiring_names
    from app.tools.payments.reconcile import reconcile_pending_requests
    db = SessionLocal()
    try:
        check_transactions(db)
        index_wallet_activity(db)
        reconcile_pending_requests(db)
        check_balance_monitors(db)
        materialize_due_schedules(db)
        check_pending_deployments(db)
        try:
            sync_name_events(db)
            check_expiring_names(db)
        except Exception:
            # Sara Names being unconfigured or the Amoy RPC being briefly
            # down must never stop the rest of the foundation cycle
            # (transaction confirmation, reconciliation, etc.) from running.
            logging.getLogger("sara.foundation").warning("Sara Names background sync failed this cycle", exc_info=True)
        process_pending(db)
    finally:
        db.close()


async def _foundation_worker() -> None:
    from app.core.config import settings
    while True:
        await asyncio.sleep(max(5, settings.TRANSACTION_POLL_SECONDS))
        try:
            await asyncio.to_thread(_run_foundation_cycle)
        except Exception:
            # A temporary database/provider failure must not permanently kill
            # finality tracking for the remainder of the app process.
            logging.getLogger("sara.foundation").exception("Foundation cycle failed; retrying")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_migrations(engine)
    _load_db_config()
    _clear_legacy_config_rows()
    db = SessionLocal()
    try:
        ensure_local_owner(db)
    finally:
        db.close()
    from app.services.alerts import register_alert_handlers
    register_alert_handlers()
    worker = asyncio.create_task(_foundation_worker())
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

app = FastAPI(title="SARA", version="1.0.0", lifespan=lifespan, redirect_slashes=False)

@app.middleware("http")
async def extend_unlock_session(request, call_next):
    # Any *authenticated* request extends an already-unlocked session — not
    # just ones that touch the encryption key directly (send/swap/perp/
    # wallet create). Requiring a valid launch token here (not just routing
    # through require_session, which runs later in the dependency chain and
    # wouldn't stop this middleware from touching first) matters: an
    # unauthenticated caller pinging any ungated endpoint (even /health)
    # used to keep resetting the inactivity timer, so the auto-lock timeout
    # would never actually fire regardless of whether anyone real was
    # active. Exception: the frontend's own background portfolio poll
    # (every 2 minutes, regardless of whether anyone's at the keyboard)
    # tags itself so it's excluded even though it IS authenticated —
    # otherwise leaving the tab open unattended would keep the session
    # "active" forever.
    token = request.headers.get("X-Sara-Session", "")
    is_authenticated = bool(token) and secrets.compare_digest(token, LAUNCH_TOKEN)
    if is_authenticated and request.headers.get("X-Sara-Background") != "1":
        lock_state.touch()
    return await call_next(request)

# script-src needs 'unsafe-inline': the frontend's UI is built entirely on
# inline onclick="..." handlers (hundreds of them), so blocking inline
# scripts outright would break the whole app short of converting every one
# to addEventListener — out of scope here. That means this CSP does NOT
# stop an injected onerror=/onclick=-style payload from *running* (the
# addMsg() fix in index.html is what prevents injection in the first
# place); what it does add is a real backstop if some other injection is
# ever found: no exfiltrating data to a non-self origin (connect-src/
# img-src), no loading an externally-hosted script, no <base> tag hijack,
# no framing this page from another site.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

# Reject requests whose Host header isn't localhost/127.0.0.1 — closes DNS
# rebinding, where an attacker-controlled domain with a short-TTL DNS record
# resolves to 127.0.0.1 so the browser connects to this server while the
# Origin-based CORS check below sees an origin it never actually restricts
# against (the attacker's own domain, not localhost). Origin and Host are
# independent headers; this validates the one CORS doesn't.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1"],
)

# Restrict cross-origin requests to localhost only (blocks malicious websites
# from calling the API while the server is running on the user's machine)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8888",
        "http://127.0.0.1:8888",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve crypto logo images
_images_dir = _resource_path("images")
if os.path.isdir(_images_dir):
    app.mount("/images", StaticFiles(directory=_images_dir), name="images")

# Serve the UI fonts from here rather than Google Fonts, so opening Sara
# makes no request to a third party (see docs/privacy.md).
_fonts_dir = _resource_path("fonts")
if os.path.isdir(_fonts_dir):
    app.mount("/fonts", StaticFiles(directory=_fonts_dir), name="fonts")

app.include_router(chat.router, prefix="/api")
app.include_router(wallets.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(portfolio.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(address_book.router, prefix="/api")
app.include_router(intelligence.router, prefix="/api")
app.include_router(lock.router, prefix="/api")
app.include_router(tokens.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(proofs.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(ledger.router, prefix="/api")
app.include_router(safety.router, prefix="/api")
app.include_router(payment_batches.router, prefix="/api")
app.include_router(schedules.router, prefix="/api")
app.include_router(payroll.router, prefix="/api")
app.include_router(spending_policies.router, prefix="/api")
app.include_router(accounting.router, prefix="/api")
app.include_router(treasury.router, prefix="/api")
app.include_router(risk.router, prefix="/api")
app.include_router(names.router, prefix="/api")
app.include_router(x402.router, prefix="/api")
app.include_router(x402_paywall.router, prefix="/api")
app.include_router(aave.router, prefix="/api")

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/")
async def root():
    # index.html is served (not returned as a static FileResponse) so the
    # per-launch session token can be injected fresh on every page load —
    # it lives only in this process's memory (app/core/session_auth.py),
    # never written to disk, so this is the only way the frontend gets it.
    index_path = _resource_path("index.html")
    html = open(os.path.abspath(index_path), encoding="utf-8").read()
    injected = f'<script>window.__SARA_SESSION__={LAUNCH_TOKEN!r};</script>\n'
    if "<head>" in html:
        html = html.replace("<head>", "<head>\n" + injected, 1)
    else:
        html = injected + html
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )
