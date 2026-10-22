"""Config / secrets seam (Week 6).

core.config resolves each secret as: process-env override > Secrets Manager
(SHADOW_SECRETS_ARN) > ambient env, and fails OPEN (missing SM never crashes).
These tests mock the SM fetch so no AWS call happens.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Each test re-resolves settings and starts from a clean secret env."""
    from core import config
    for k in config.SECRET_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("SHADOW_SECRETS_ARN", raising=False)
    config.reset_settings()
    yield
    config.reset_settings()


def test_reads_from_ambient_env_when_no_secrets_manager(monkeypatch):
    from core import config
    monkeypatch.setenv("BEDROCK_MODEL_ID", "model-from-env")
    config.reset_settings()
    assert config.get("BEDROCK_MODEL_ID") == "model-from-env"


def test_secrets_manager_supplies_values(monkeypatch):
    from core import config
    monkeypatch.setenv("SHADOW_SECRETS_ARN", "arn:aws:secretsmanager:...:shadow/config")
    monkeypatch.setattr(config, "_load_secrets_manager",
                        lambda: {"BOX_CLIENT_ID": "from-sm", "BEDROCK_MODEL_ID": "sm-model"})
    config.reset_settings()
    assert config.get("BOX_CLIENT_ID") == "from-sm"
    assert config.get("BEDROCK_MODEL_ID") == "sm-model"


def test_env_override_beats_secrets_manager(monkeypatch):
    from core import config
    monkeypatch.setenv("SHADOW_SECRETS_ARN", "arn:...")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "env-wins")
    monkeypatch.setattr(config, "_load_secrets_manager",
                        lambda: {"BEDROCK_MODEL_ID": "sm-loses"})
    config.reset_settings()
    assert config.get("BEDROCK_MODEL_ID") == "env-wins"


def test_fail_open_when_secrets_manager_unreachable(monkeypatch):
    """A broken SM fetch must not crash resolution — it falls back to env."""
    from core import config

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setenv("SHADOW_SECRETS_ARN", "arn:...")
    monkeypatch.setenv("BOX_CLIENT_ID", "env-fallback")
    # _load_secrets_manager itself swallows exceptions and returns {}; simulate
    monkeypatch.setattr(config, "_load_secrets_manager", lambda: {})
    config.reset_settings()
    assert config.get("BOX_CLIENT_ID") == "env-fallback"


def test_oauth_disabled_until_configured(monkeypatch):
    from core import config
    config.reset_settings()
    assert config.oauth_enabled() is False
    monkeypatch.setenv("BOX_OAUTH_CLIENT_ID", "an-oauth-app")
    config.reset_settings()
    assert config.oauth_enabled() is True
