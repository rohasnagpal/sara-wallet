from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from app.db.session import init_db, SessionLocal
from app.db.models import Config
from app.routers import chat, wallets, market, portfolio, settings, address_book, intelligence
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _load_db_config()
    yield

app = FastAPI(title="SARA", version="1.0.0", lifespan=lifespan, redirect_slashes=False)

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

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/")
async def root():
    index_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    return FileResponse(os.path.abspath(index_path))
