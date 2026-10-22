"""Box OAuth 2.0 (authorization-code) login — the real replacement for the
Week-1 dev login.

The dev login picked a seeded App User and signed a cookie with no proof of who
the caller actually is. That was always a stand-in: the *shape* it established —
"identity is minted server-side and carried in a signed session cookie" — is
exactly what OAuth needs, so turning it on changes only HOW the cookie is minted,
never any call site that reads it (`web.auth.current_user_id` et al. are
untouched).

The flow, standard three-legged OAuth:

  1. GET /auth/login
       We build Box's authorize URL with our client id, the registered redirect
       URI, and a random `state` (stored in a short, signed, httponly cookie to
       defend against CSRF), then 302 the browser to Box.
  2. Box authenticates the user and redirects back to
       GET /auth/callback?code=...&state=...
       We check `state` against the cookie, exchange `code` for an access token
       at Box's token endpoint, call /users/me as that token to learn the real
       Box user id, mint our own signed session cookie for that id (the same
       `web.auth.sign`), and 302 the browser home.
  3. Every later request reads identity from that signed cookie, exactly as
       before. No Box token is ever stored client-side.

This module is INERT until a Box OAuth app is configured: `enabled()` is the one
flag the server branches on, and it is true only when BOX_OAUTH_CLIENT_ID is set
(resolved through core.config, so it can come from Secrets Manager in prod). When
it is false the dev login stays in force and none of these routes do anything.

Register the Box OAuth app (Custom App → "User Authentication (OAuth 2.0)") with
redirect URI `<PUBLIC_URL>/auth/callback` (dev: http://127.0.0.1:8099/auth/callback),
and supply BOX_OAUTH_CLIENT_ID / BOX_OAUTH_CLIENT_SECRET.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import urllib.parse

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from core.config import get, oauth_enabled
from web.auth import COOKIE, sign

AUTHORIZE_URL = "https://account.box.com/api/oauth2/authorize"
TOKEN_URL = "https://api.box.com/oauth2/token"
USERS_ME_URL = "https://api.box.com/2.0/users/me"

_STATE_COOKIE = "shadow_oauth_state"

router = APIRouter()


def enabled() -> bool:
    """True when a real Box OAuth app is configured (delegates to the config
    seam). The server uses this to retire the dev login automatically."""
    return oauth_enabled()


def _redirect_uri() -> str:
    """The registered OAuth callback. Defaults to the local dev server; set
    SHADOW_PUBLIC_URL in a deployment so the callback matches the live host."""
    base = os.environ.get("SHADOW_PUBLIC_URL", "http://127.0.0.1:8099").rstrip("/")
    return f"{base}/auth/callback"


def _sign_state(state: str) -> str:
    """Sign the CSRF state with the session secret so a client can't forge it.
    Reuses web.auth's HMAC construction (value.signature)."""
    return sign(state)


def _verify_state(cookie_value: str, returned_state: str) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    value, _, sig = cookie_value.rpartition(".")
    from web.auth import _SECRET

    expected = hmac.new(_SECRET, value.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected) and hmac.compare_digest(
        value, returned_state
    )


@router.get("/auth/login")
def auth_login():
    """Begin the OAuth dance: 302 the browser to Box's authorize page with a
    signed anti-CSRF state cookie. 404 when OAuth isn't configured."""
    if not enabled():
        raise HTTPException(status_code=404, detail="OAuth not configured")
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": get("BOX_OAUTH_CLIENT_ID"),
        "redirect_uri": _redirect_uri(),
        "state": state,
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    resp = RedirectResponse(url, status_code=302)
    # short-lived, httponly, not readable by JS — defends the callback
    resp.set_cookie(
        _STATE_COOKIE, _sign_state(state), max_age=600, httponly=True, samesite="lax"
    )
    return resp


@router.get("/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = ""):
    """Box redirects here after the user approves. Verify state, exchange the
    code for a token, read the real Box user id, mint our signed session cookie,
    and 302 home."""
    if not enabled():
        raise HTTPException(status_code=404, detail="OAuth not configured")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")
    if not _verify_state(request.cookies.get(_STATE_COOKIE, ""), state):
        raise HTTPException(status_code=400, detail="invalid state (CSRF check failed)")

    # 1) exchange the authorization code for an access token
    token_resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": get("BOX_OAUTH_CLIENT_ID"),
            "client_secret": get("BOX_OAUTH_CLIENT_SECRET"),
            "redirect_uri": _redirect_uri(),
        },
        timeout=15,
    )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="token exchange failed")
    access_token = token_resp.json().get("access_token", "")
    if not access_token:
        raise HTTPException(status_code=502, detail="no access token returned")

    # 2) learn WHO this is, as Box sees them — identity is never client-asserted
    me_resp = requests.get(
        USERS_ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if me_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="could not read Box user")
    box_user_id = str(me_resp.json().get("id", ""))
    if not box_user_id:
        raise HTTPException(status_code=502, detail="Box user id missing")

    # 3) mint OUR signed session cookie for that id (same contract as dev login)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(COOKIE, sign(box_user_id), httponly=True, samesite="lax")
    resp.delete_cookie(_STATE_COOKIE)
    return resp
