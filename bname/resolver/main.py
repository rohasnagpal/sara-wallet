import time
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, HTTPException, Response
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    upstream: str = Field(default="http://localhost:8000", validation_alias="BNAME_RESOLVER_UPSTREAM")
    default_ttl: int = Field(default=300, validation_alias="BNAME_RESOLVER_DEFAULT_TTL")
    max_ttl: int = Field(default=900, validation_alias="BNAME_RESOLVER_MAX_TTL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@dataclass
class CacheItem:
    expires_at: float
    payload: dict
    headers: dict[str, str]


settings = Settings()
cache: dict[str, CacheItem] = {}
app = FastAPI(title="bName Caching Resolver", version="0.1.0")


def _ttl_from_payload(payload: dict) -> int:
    ttl = int(payload.get("ttl") or settings.default_ttl)
    return max(30, min(ttl, settings.max_ttl))


@app.get("/health")
def health():
    return {"status": "ok", "upstream": settings.upstream}


@app.get("/v1/resolve/{name:path}")
async def resolve(name: str, response: Response):
    key = name.strip().lower().rstrip(".")
    cached = cache.get(key)
    now = time.time()
    if cached and cached.expires_at > now:
        for header, value in cached.headers.items():
            response.headers[header] = value
        response.headers["X-BName-Source"] = "cache"
        return cached.payload

    async with httpx.AsyncClient(timeout=10) as client:
        upstream_response = await client.get(f"{settings.upstream.rstrip('/')}/v1/resolve/{key}")
    if upstream_response.status_code == 404:
        raise HTTPException(status_code=404, detail="record not found")
    if upstream_response.status_code >= 400:
        raise HTTPException(status_code=upstream_response.status_code, detail=upstream_response.text)

    payload = upstream_response.json()
    ttl = _ttl_from_payload(payload)
    headers = {
        "Cache-Control": f"public, max-age={ttl}",
        "ETag": payload.get("record_hash", ""),
        "X-BName-Version": str(payload.get("version", "")),
        "X-BName-Zone-Version": str(payload.get("zone_version", "")),
        "X-BName-Anchor": (payload.get("anchor") or {}).get("type", "none"),
    }
    cache[key] = CacheItem(expires_at=now + ttl, payload=payload, headers=headers)
    for header, value in headers.items():
        response.headers[header] = value
    response.headers["X-BName-Source"] = "authoritative"
    return payload


@app.delete("/v1/cache/{name:path}")
def purge(name: str):
    key = name.strip().lower().rstrip(".")
    cache.pop(key, None)
    return {"status": "purged", "name": key}
