"""Box OAuth wiring tests (finish list: retire the dev login).

These prove the SEAM behaviour without any live Box call:

  * When a Box OAuth app is configured (BOX_OAUTH_CLIENT_ID present), the dev
    login endpoint refuses (403) and /api/me reports auth_mode="oauth" with an
    empty impersonation roster and a login_url — so the UI sends the user to
    /auth/login instead of letting them pick an identity.
  * When it is NOT configured, the dev login path is unchanged: /api/me reports
    auth_mode="dev" and /auth/login 404s (inert).
  * /auth/login, when enabled, 302-redirects to Box's authorize URL and sets a
    signed anti-CSRF state cookie.

The real token exchange and /users/me call are only reached on the callback with
a live `code`, which these tests never supply — so no network is needed.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.server import app

    return TestClient(app)


def _set_oauth(monkeypatch, enabled: bool):
    """Flip the config seam by setting/clearing the env the resolver reads, then
    dropping the cached settings so the next read re-resolves."""
    from core import config

    if enabled:
        monkeypatch.setenv("BOX_OAUTH_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("BOX_OAUTH_CLIENT_SECRET", "test-secret")
    else:
        monkeypatch.delenv("BOX_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("BOX_OAUTH_CLIENT_SECRET", raising=False)
    config.reset_settings()


def test_dev_login_refused_when_oauth_configured(client, monkeypatch):
    _set_oauth(monkeypatch, True)
    try:
        r = client.post("/api/login", json={"name": "Alex Rivera"})
        assert r.status_code == 403
        assert "oauth" in r.json()["error"].lower()
    finally:
        from core import config

        config.reset_settings()


def test_me_reports_oauth_mode_when_configured(client, monkeypatch):
    _set_oauth(monkeypatch, True)
    try:
        body = client.get("/api/me").json()
        assert body["auth_mode"] == "oauth"
        assert body["login_url"] == "/auth/login"
        # no impersonation switcher in oauth mode
        assert body["options"] == []
        assert body["logged_in"] is False
    finally:
        from core import config

        config.reset_settings()


def test_me_reports_dev_mode_when_not_configured(client, monkeypatch):
    _set_oauth(monkeypatch, False)
    try:
        body = client.get("/api/me").json()
        assert body["auth_mode"] == "dev"
        assert body["login_url"] is None
    finally:
        from core import config

        config.reset_settings()


def test_auth_login_404_when_oauth_disabled(client, monkeypatch):
    _set_oauth(monkeypatch, False)
    try:
        # the OAuth routes are inert until a Box OAuth app is configured
        r = client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 404
    finally:
        from core import config

        config.reset_settings()


def test_auth_login_redirects_to_box_when_enabled(client, monkeypatch):
    _set_oauth(monkeypatch, True)
    try:
        r = client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert loc.startswith("https://account.box.com/api/oauth2/authorize")
        assert "client_id=test-client-id" in loc
        assert "response_type=code" in loc
        assert "state=" in loc
        # the anti-CSRF state cookie is set (signed, httponly)
        assert any(
            c.name == "shadow_oauth_state" for c in client.cookies.jar
        ) or "shadow_oauth_state" in r.headers.get("set-cookie", "")
    finally:
        from core import config

        config.reset_settings()


def test_auth_callback_rejects_bad_state(client, monkeypatch):
    _set_oauth(monkeypatch, True)
    try:
        # no matching state cookie -> CSRF check fails, no token exchange attempted
        r = client.get(
            "/auth/callback?code=abc&state=forged", follow_redirects=False
        )
        assert r.status_code == 400
        assert "state" in r.json()["detail"].lower()
    finally:
        from core import config

        config.reset_settings()


# Guard: BOX_OAUTH_CLIENT_ID must not leak into other tests from this module.
def teardown_module(module):
    os.environ.pop("BOX_OAUTH_CLIENT_ID", None)
    os.environ.pop("BOX_OAUTH_CLIENT_SECRET", None)
    from core import config

    config.reset_settings()
