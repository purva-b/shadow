"""Auth / identity-boundary tests.

The fix (brief #3): identity must come from a signed session cookie, never from
the request body, and dev-only endpoints must be gated. These test the signing
layer directly (no running server needed).
"""
from __future__ import annotations

import importlib


def _auth():
    return importlib.import_module("web.auth")


def test_signed_cookie_roundtrips():
    a = _auth()
    token = a.sign("12345")
    assert a.verify(token) == "12345"


def test_tampered_cookie_is_rejected():
    """A client editing the user id (without the secret) must be rejected —
    this is what stops impersonation via a forged cookie."""
    a = _auth()
    token = a.sign("12345")
    uid, _, sig = token.rpartition(".")
    forged = f"99999.{sig}"          # swap the id, keep the old signature
    assert a.verify(forged) is None


def test_unsigned_value_is_rejected():
    a = _auth()
    assert a.verify("99999") is None          # no signature at all
    assert a.verify("") is None


def test_dev_mode_flag(monkeypatch):
    a = _auth()
    monkeypatch.delenv("SHADOW_DEV", raising=False)
    assert a.dev_mode() is False
    monkeypatch.setenv("SHADOW_DEV", "1")
    assert a.dev_mode() is True
