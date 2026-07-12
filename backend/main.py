from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.db.session import init_db, SessionLocal
from app.db.models import Config
from app.routers import chat, wallets, market, portfolio, settings, address_book, intelligence, lock
from app.tools.wallet import lock as lock_state
import os

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _load_db_config()
    _clear_stale_master_key_row()
    yield

app = FastAPI(title="SARA", version="1.0.0", lifespan=lifespan, redirect_slashes=False)

@app.middleware("http")
async def extend_unlock_session(request, call_next):
    # Any request extends an already-unlocked session — not just ones that
    # touch the encryption key directly (send/swap/perp/wallet create).
    lock_state.touch()
    return await call_next(request)

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

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/")
async def root():
    index_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    return FileResponse(os.path.abspath(index_path))
