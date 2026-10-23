"""Per-connector OAuth — let a signed-in user connect Slack, GitHub, Confluence
(Atlassian) and Google Calendar to Shadow with a real three-legged OAuth dance,
the same shape as the Box user login in web/oauth.py.

Design, mirroring web/oauth.py so there is one mental model:

  * Each provider is described by a small PROVIDERS entry: its authorize URL,
    token URL, scopes, and the env vars holding its client id/secret. A provider
    is INERT until those creds are set — `configured(name)` is the single flag
    the UI branches on, exactly like `oauth_enabled()` for Box. Nothing here
    invents credentials; you register each OAuth app in that vendor's console
    and supply <PROVIDER>_OAUTH_CLIENT_ID / _SECRET (resolved through
    core.config, so they can come from Secrets Manager in prod).

  * GET /connect/{name}      → build the provider authorize URL with a signed
                               anti-CSRF state (bound to the logged-in user) and
                               302 the browser to the provider.
  * GET /connect/{name}/callback → verify state, exchange code → token, and
                               store the token for THIS user + connector.

  * Tokens are saved per (user_id, connector) by core.token_store, so a later
    sync for that user uses that user's own grant. No token is stored client
    side; the browser only ever carries the signed session cookie.

Identity: the user must already be signed in (Box session cookie). We never let
a connector grant stand in for who you are — it only authorizes Shadow to READ
that source on your behalf.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import urllib.parse
from dataclasses import dataclass

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from core import token_store
from core.config import get
from web.auth import _SECRET, COOKIE, require_user_id, verify

router = APIRouter()
_STATE_COOKIE = "shadow_connect_state"


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    authorize_url: str
    token_url: str
    scopes: str
    id_env: str          # env/secret holding the OAuth client id
    secret_env: str      # env/secret holding the OAuth client secret
    # how the provider returns the usable token in its token response json
    token_field: str = "access_token"
    extra_authorize: dict | None = None


PROVIDERS: dict[str, Provider] = {
    "slack": Provider(
        name="slack", label="Slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        # user token so Shadow reads only what this user can read
        scopes="search:read,channels:history,channels:read,users:read",
        id_env="SLACK_OAUTH_CLIENT_ID", secret_env="SLACK_OAUTH_CLIENT_SECRET",
        token_field="authed_user",  # slack nests the user token; handled below
    ),
    "github": Provider(
        name="github", label="GitHub",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scopes="repo read:org",
        id_env="GITHUB_OAUTH_CLIENT_ID", secret_env="GITHUB_OAUTH_CLIENT_SECRET",
    ),
    "confluence": Provider(
        name="confluence", label="Confluence",
        authorize_url="https://auth.atlassian.com/authorize",
        token_url="https://auth.atlassian.com/oauth/token",
        scopes="read:confluence-content.all read:confluence-space.summary offline_access",
        id_env="ATLASSIAN_OAUTH_CLIENT_ID", secret_env="ATLASSIAN_OAUTH_CLIENT_SECRET",
        extra_authorize={"audience": "api.atlassian.com", "prompt": "consent"},
    ),
    "calendar": Provider(
        name="calendar", label="Google Calendar",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes="https://www.googleapis.com/auth/calendar.readonly",
        id_env="GOOGLE_OAUTH_CLIENT_ID", secret_env="GOOGLE_OAUTH_CLIENT_SECRET",
        extra_authorize={"access_type": "offline", "prompt": "consent"},
    ),
}


def configured(name: str) -> bool:
    """True when this connector's OAuth app creds are present — the flag the UI
    uses to show 'Connect' (real OAuth) vs 'Setup required' (creds missing)."""
    p = PROVIDERS.get(name)
    return bool(p and get(p.id_env) and get(p.secret_env))


def _public_base() -> str:
    import os
    return os.environ.get("SHADOW_PUBLIC_URL", "http://127.0.0.1:8099").rstrip("/")


def _redirect_uri(name: str) -> str:
    return f"{_public_base()}/connect/{name}/callback"


def _sign(value: str) -> str:
    mac = hmac.new(_SECRET, value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def _verify(cookie_value: str, expected_value: str) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    value, _, sig = cookie_value.rpartition(".")
    good = hmac.new(_SECRET, value.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and hmac.compare_digest(value, expected_value)


@router.get("/connect/{name}")
def connect(name: str, request: Request):
    """Begin connecting `name` for the signed-in user. 404 if the provider is
    unknown or its OAuth app is not configured; 401 if not signed in."""
    p = PROVIDERS.get(name)
    if not p:
        raise HTTPException(status_code=404, detail="unknown connector")
    user_id = require_user_id(request)  # must be signed in (401 before config check)
    if not configured(name):
        raise HTTPException(status_code=404, detail=f"{p.label} OAuth not configured")

    # state binds this flow to the user + connector and defends against CSRF
    nonce = secrets.token_urlsafe(16)
    state = f"{name}:{user_id}:{nonce}"
    params = {
        "response_type": "code",
        "client_id": get(p.id_env),
        "redirect_uri": _redirect_uri(name),
        "scope": p.scopes,
        "state": state,
        **(p.extra_authorize or {}),
    }
    url = f"{p.authorize_url}?{urllib.parse.urlencode(params)}"
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, _sign(state), max_age=600, httponly=True, samesite="lax")
    return resp


@router.get("/connect/{name}/callback")
def connect_callback(name: str, request: Request, code: str = "", state: str = ""):
    """Provider redirects here; verify state, exchange code → token, persist it
    for (user, connector), then 302 back to the connections page."""
    p = PROVIDERS.get(name)
    if not p or not configured(name):
        raise HTTPException(status_code=404, detail="unknown or unconfigured connector")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")
    if not _verify(request.cookies.get(_STATE_COOKIE, ""), state):
        raise HTTPException(status_code=400, detail="invalid state (CSRF check failed)")
    # the signed-in user must match the state's user
    user_id = verify(request.cookies.get(COOKIE, ""))
    state_user = state.split(":")[1] if state.count(":") >= 2 else ""
    if not user_id or user_id != state_user:
        raise HTTPException(status_code=401, detail="session/user mismatch")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": get(p.id_env),
        "client_secret": get(p.secret_env),
        "redirect_uri": _redirect_uri(name),
    }
    r = requests.post(p.token_url, data=data,
                      headers={"Accept": "application/json"}, timeout=15)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"{p.label} token exchange failed")
    payload = r.json()

    # extract the usable token (Slack nests the user token under authed_user)
    if name == "slack":
        token = (payload.get("authed_user") or {}).get("access_token", "")
    else:
        token = payload.get("access_token", "")
    if not token:
        raise HTTPException(status_code=502, detail=f"{p.label} returned no token")

    token_store.save(user_id, name, {
        "access_token": token,
        "refresh_token": payload.get("refresh_token", ""),
        "scope": payload.get("scope", p.scopes),
        "raw": {k: v for k, v in payload.items() if k != "access_token"},
    })
    resp = RedirectResponse("/connections", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.post("/connect/{name}/disconnect")
def disconnect(name: str, request: Request):
    """Forget this user's token for `name`."""
    user_id = require_user_id(request)
    token_store.delete(user_id, name)
    return {"connected": False, "connector": name}
