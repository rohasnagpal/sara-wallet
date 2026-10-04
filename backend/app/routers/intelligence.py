from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.session_auth import require_session

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class ResolveBody(BaseModel):
    name: str


@router.post("/names/resolve", dependencies=[Depends(require_session)])
def resolve_name(body: ResolveBody):
    name = body.name.strip().lower()
    if name.endswith(".eth"):
        from app.tools.names.ens import resolve
        addr = resolve(name)
        return {"name": name, "address": addr, "chain": "evm", "resolved": bool(addr)}
    return {"name": name, "address": None, "chain": None, "resolved": False}
