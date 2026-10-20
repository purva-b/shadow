"""Session identity for Shadow (dev-login flavor).

The old design kept a single process-global `acting_as` and let `/api/ask` read
`user_id` straight from the request body. That is two security bugs: every user
shared one identity, and a caller could impersonate anyone by passing a
`user_id`. Here identity comes from a SIGNED SESSION COOKIE only — the server
decides who you are, the client cannot assert it.

This is a DEV login: `POST /api/login {name}` picks one of the seeded demo App
Users and signs a cookie. It is a stand-in for Box OAuth (deferred): the shape —
"identity is established server-side and carried in a signed session" — is the
same, so swapping in OAuth later changes only how the cookie is minted, not any
call site that reads it.

The cookie is signed with HMAC-SHA256 over the user id using SHADOW_SESSION_SECRET
(a dev default is provided so local runs work; set a real secret in any shared
deployment). Signed, not encrypted — the user id is not a secret, but the
signature stops a client forging a different one.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import HTTPException, Request

COOKIE = "shadow_session"
_SECRET = os.environ.get("SHADOW_SESSION_SECRET", "dev-only-insecure-secret").encode()


def dev_mode() -> bool:
    """Dev-only endpoints (login-as, simulate) are enabled only under this flag."""
    return os.environ.get("SHADOW_DEV", "").lower() in ("1", "true", "yes")


def sign(user_id: str) -> str:
    """value.signature — the cookie payload."""
    mac = hmac.new(_SECRET, user_id.encode(), hashlib.sha256).hexdigest()
    return f"{user_id}.{mac}"


def verify(cookie_value: str) -> str | None:
    """Return the user id iff the signature is valid, else None."""
    if not cookie_value or "." not in cookie_value:
        return None
    user_id, _, sig = cookie_value.rpartition(".")
    expected = hmac.new(_SECRET, user_id.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return user_id
    return None


def current_user_id(request: Request) -> str | None:
    """The logged-in App User id from the signed session cookie, or None."""
    return verify(request.cookies.get(COOKIE, ""))


def require_user_id(request: Request) -> str:
    """FastAPI dependency: the session user id, or 401. Identity NEVER comes from
    the request body — only from the signed cookie."""
    uid = current_user_id(request)
    if not uid:
        raise HTTPException(status_code=401, detail="not logged in")
    return uid
