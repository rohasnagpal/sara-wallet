"""Per-launch shared secret gating every mutating/sensitive endpoint.

Without this, any local caller — another local process, or a malicious
webpage's blind cross-origin form submission that CORS's preflight can't
catch (simple requests are sent by the browser regardless of CORS; only
reading the response is blocked) — could hit endpoints like wallet send
directly, with no confirmation step and no proof it's actually this app's
own frontend. TrustedHost/CORS restrict *which origins a browser will let
read a response*; neither stops a same-machine, non-browser HTTP client, or
a browser's own "simple request" CSRF, from reaching the endpoint at all.

The token is generated fresh in memory on every process start (never
persisted, never logged) and handed to the frontend only via the HTML this
same server renders (see main.py's root()). A custom header can't be set on
a cross-origin "simple request" without turning it into a preflighted one,
which the existing CORS allowlist then rejects for any untrusted origin —
so this closes that gap even for callers CORS alone couldn't stop.
"""
import secrets
from fastapi import Header, HTTPException

LAUNCH_TOKEN = secrets.token_urlsafe(32)


def require_session(x_sara_session: str = Header(default="")) -> None:
    if not x_sara_session or not secrets.compare_digest(x_sara_session, LAUNCH_TOKEN):
        raise HTTPException(401, "Missing or invalid session token.")
