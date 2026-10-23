"""Tests for the per-connector OAuth layer, the token store (incl. refresh),
and the notification delivery hook — all hermetic, no live vendors.
"""
from __future__ import annotations

import os

import pytest


# ─────────────────────────── token store ───────────────────────────
@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point the token store at a scratch file so tests never touch the real one."""
    from core import token_store
    monkeypatch.setattr(token_store, "_LOCAL", tmp_path / "tokens.json")
    monkeypatch.delenv("SHADOW_USE_DDB_TOKENS", raising=False)
    return token_store


def test_token_store_save_get_delete(isolated_store):
    ts = isolated_store
    assert ts.get("u1", "slack") is None
    assert ts.connected("u1", "slack") is False
    ts.save("u1", "slack", {"access_token": "xoxb-1", "refresh_token": "r1"})
    assert ts.get("u1", "slack")["access_token"] == "xoxb-1"
    assert ts.connected("u1", "slack") is True
    # tokens are keyed per (user, connector)
    assert ts.get("u2", "slack") is None
    ts.delete("u1", "slack")
    assert ts.get("u1", "slack") is None


def test_token_store_list_for(isolated_store):
    ts = isolated_store
    ts.save("u1", "slack", {"access_token": "a"})
    ts.save("u1", "github", {"access_token": "b"})
    ts.save("u2", "slack", {"access_token": "c"})
    got = ts.list_for("u1")
    assert set(got) == {"slack", "github"}


def test_token_refresh_success(isolated_store, monkeypatch):
    ts = isolated_store
    ts.save("u1", "confluence", {"access_token": "old", "refresh_token": "r1",
                                 "scope": "read"})

    class _Resp:
        status_code = 200
        def json(self):
            return {"access_token": "new", "refresh_token": "r2", "scope": "read"}

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    out = ts.refresh("u1", "confluence", "https://token", "cid", "secret")
    assert out is not None and out["access_token"] == "new"
    # rotated refresh token persisted
    assert ts.get("u1", "confluence")["refresh_token"] == "r2"


def test_token_refresh_no_refresh_token(isolated_store):
    ts = isolated_store
    ts.save("u1", "slack", {"access_token": "a"})  # no refresh_token
    assert ts.refresh("u1", "slack", "https://token", "cid", "sec") is None


# ─────────────────────────── connector OAuth ───────────────────────────
def test_connector_configured_flag(monkeypatch):
    from core import config
    from web import connector_oauth
    monkeypatch.delenv("SLACK_OAUTH_CLIENT_ID", raising=False)
    config.reset_settings()
    assert connector_oauth.configured("slack") is False
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_SECRET", "sec")
    config.reset_settings()
    try:
        assert connector_oauth.configured("slack") is True
    finally:
        config.reset_settings()


def test_connect_route_guards(monkeypatch):
    """Unknown connector → 404; unconfigured → 404; logged out on a configured
    connector → 401 (auth precedes config now)."""
    from fastapi.testclient import TestClient

    from core import config
    from web.server import app
    c = TestClient(app)

    # unknown connector
    assert c.get("/connect/bogus", follow_redirects=False).status_code == 404
    # configured but logged OUT → 401
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_SECRET", "sec")
    config.reset_settings()
    try:
        r = c.get("/connect/slack", follow_redirects=False)
        assert r.status_code == 401
    finally:
        monkeypatch.delenv("SLACK_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("SLACK_OAUTH_CLIENT_SECRET", raising=False)
        config.reset_settings()


def test_connect_callback_rejects_bad_state(monkeypatch):
    from fastapi.testclient import TestClient

    from core import config
    from web.server import app
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_SECRET", "sec")
    config.reset_settings()
    try:
        c = TestClient(app)
        r = c.get("/connect/slack/callback?code=x&state=forged", follow_redirects=False)
        assert r.status_code in (400, 401)  # CSRF/state or session check fails
    finally:
        monkeypatch.delenv("SLACK_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("SLACK_OAUTH_CLIENT_SECRET", raising=False)
        config.reset_settings()


# ─────────────────────────── notification sender ───────────────────────────
def test_notify_sender_hook_fires(monkeypatch):
    from core import notify, notify_senders

    seen = []
    monkeypatch.setattr(notify, "_SENDER", None)
    monkeypatch.setattr(notify_senders, "_logging_sender",
                        lambda p, t, b: seen.append((p, t, b)))
    monkeypatch.delenv("SHADOW_SLACK_WEBHOOK_URL", raising=False)
    which = notify_senders.install()
    assert which == "logging"
    # the registered sender is callable and routes through our capture
    assert notify._SENDER is not None
    notify._SENDER("priya@x", "title", "body")
    assert seen == [("priya@x", "title", "body")]


def teardown_module(module):
    for k in ("SLACK_OAUTH_CLIENT_ID", "SLACK_OAUTH_CLIENT_SECRET",
              "SHADOW_SLACK_WEBHOOK_URL"):
        os.environ.pop(k, None)
    from core import config
    config.reset_settings()
