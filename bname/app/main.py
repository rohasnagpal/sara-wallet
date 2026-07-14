from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.routers import anchors, names, public, redirects

settings = get_settings()

app = FastAPI(
    title="Sara bName Registry API",
    description="DNS-style Web3 names, records, redirects, versioning, and optional on-chain anchoring.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(names.router)
app.include_router(anchors.router)
app.include_router(redirects.router)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}
