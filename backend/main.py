from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from contextlib import asynccontextmanager
from app.db.session import init_db, SessionLocal, engine
from app.db.models import Config
from app.routers import chat, wallets, market, portfolio, settings, address_book, intelligence, lock, tokens, payments
from app.tools.wallet import lock as lock_state
from app.core.session_auth import LAUNCH_TOKEN
import os
import secrets

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

def _clear_stale_master_key_row():
    """One-time cleanup: SARA_MASTER_KEY used to also be written to the
    Config table by the old settings flow. It's now managed exclusively via
    .env + the lock/unlock session, so drop any leftover row."""
    try:
        db = SessionLocal()
        row = db.query(Config).filter(Config.key == "SARA_MASTER_KEY").first()
        if row:
            db.delete(row)
            db.commit()
        db.close()
    except Exception:
        pass

def _add_column_if_missing(table: str, column: str, sql_type: str = "VARCHAR"):
    """create_all() only creates missing tables, not missing columns on
    existing ones — new nullable columns added to existing models need a
    one-time ALTER TABLE on any pre-existing DB."""
    from sqlalchemy import text, inspect
    import logging
    try:
        inspector = inspect(engine)
        if table not in inspector.get_table_names():
            return
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column not in cols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
    except Exception:
        # By the time this ALTER runs, the column-existence check above has
        # already ruled out the one "expected" failure (column already
        # there) — anything raised here is a genuine problem (disk full, DB
        # locked, permissions), and silently swallowing it means the column
        # never gets added, so every later read/write of it crashes deep in
        # unrelated code with a far more confusing error. Log it here where
        # the real cause is visible instead.
        logging.getLogger("sara.migrations").exception(
            "Failed to add column %s.%s — the app will likely error later wherever this column is used.",
            table, column,
        )
        raise

def _add_unique_index_if_missing(index_name: str, table: str, columns: list[str]):
    """create_all() only applies new __table_args__ constraints to tables it
    creates fresh, not ones that already exist — the PaymentRequest race-
    condition fix (models.py) added a unique constraint on
    (chain, network, matched_tx_hash) that a pre-existing DB needs this
    one-time index creation to actually get enforced."""
    from sqlalchemy import text, inspect
    import logging
    try:
        inspector = inspect(engine)
        if table not in inspector.get_table_names():
            return
        existing = {ix["name"] for ix in inspector.get_indexes(table)}
        if index_name not in existing:
            cols_sql = ", ".join(columns)
            with engine.begin() as conn:
                conn.execute(text(
                    f"CREATE UNIQUE INDEX {index_name} ON {table} ({cols_sql})"
                ))
    except Exception:
        # Most likely cause: rows already violating the constraint (e.g. a
        # duplicate matched_tx_hash from before this fix existed). Logging
        # rather than crashing startup means the app stays usable — the
        # constraint just won't be enforced yet, same failure mode as
        # _add_column_if_missing above, until the underlying data is fixed.
        logging.getLogger("sara.migrations").exception(
            "Failed to create unique index %s on %s — the race-condition fix "
            "for duplicate payment matches will not be enforced until this is resolved.",
            index_name, table,
        )
        raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _load_db_config()
    _clear_stale_master_key_row()
    _add_column_if_missing("transactions", "reference")
    _add_column_if_missing("payment_requests", "matched_tx_hash")
    _add_unique_index_if_missing(
        "uq_payment_requests_chain_network_txhash", "payment_requests",
        ["chain", "network", "matched_tx_hash"],
    )
    yield

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
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
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
_images_dir = os.path.join(os.path.dirname(__file__), "images")
if os.path.isdir(_images_dir):
    app.mount("/images", StaticFiles(directory=_images_dir), name="images")

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

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/")
async def root():
    # index.html is served (not returned as a static FileResponse) so the
    # per-launch session token can be injected fresh on every page load —
    # it lives only in this process's memory (app/core/session_auth.py),
    # never written to disk, so this is the only way the frontend gets it.
    index_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    html = open(os.path.abspath(index_path), encoding="utf-8").read()
    injected = f'<script>window.__SARA_SESSION__={LAUNCH_TOKEN!r};</script>\n'
    if "<head>" in html:
        html = html.replace("<head>", "<head>\n" + injected, 1)
    else:
        html = injected + html
    return HTMLResponse(html)
