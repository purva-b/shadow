"""Runtime config + secrets resolution.

In production the Box CCG credentials, the Bedrock model id, and the session
secret must NOT live in a committed file or a baked image layer — they come from
AWS Secrets Manager. Locally they come from `.env`. This module is the single
seam that resolves either, so the rest of the app just calls `settings()` and
never cares where a value came from.

Resolution order for each key:
  1. an already-set process env var (explicit override, e.g. in docker run -e);
  2. the JSON secret at SHADOW_SECRETS_ARN in Secrets Manager, if that env is
     set and the secret is reachable;
  3. .env / the ambient environment (dotenv is loaded elsewhere at startup).

Best-effort and fail-open on the SM fetch: if the secret is unreachable the app
still boots on whatever env it has (honest degradation), and Box/Bedrock
features that truly need a missing value degrade at call time with a clear
error — the process does not crash at import.

OAuth seam: `oauth_enabled()` reports whether a real Box OAuth app is configured
(BOX_OAUTH_CLIENT_ID present). It is the flag the login flow will branch on when
OAuth replaces the Week-1 dev login; until then it returns False and the dev
login stays in force. Nothing here implements the OAuth dance yet — it is the
documented seam the finish list calls for.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

# the keys this app reads as secrets/config
SECRET_KEYS = (
    "BOX_CLIENT_ID",
    "BOX_CLIENT_SECRET",
    "BOX_ENTERPRISE_ID",
    "BEDROCK_MODEL_ID",
    "SHADOW_SESSION_SECRET",
    # OAuth seam (unused until the OAuth login flow lands):
    "BOX_OAUTH_CLIENT_ID",
    "BOX_OAUTH_CLIENT_SECRET",
    # per-connector OAuth apps (dormant until each is registered + set):
    "SLACK_OAUTH_CLIENT_ID",
    "SLACK_OAUTH_CLIENT_SECRET",
    "GITHUB_OAUTH_CLIENT_ID",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "ATLASSIAN_OAUTH_CLIENT_ID",
    "ATLASSIAN_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
)


def _load_secrets_manager() -> dict[str, str]:
    """Fetch the JSON secret at SHADOW_SECRETS_ARN. Returns {} on any problem —
    missing env, no creds, unreachable, malformed — so the caller fails open to
    the environment."""
    arn = os.environ.get("SHADOW_SECRETS_ARN", "")
    if not arn:
        return {}
    try:
        import boto3

        region = os.environ.get("AWS_REGION", "us-east-1")
        sm = boto3.client("secretsmanager", region_name=region)
        raw = sm.get_secret_value(SecretId=arn).get("SecretString", "") or "{}"
        data = json.loads(raw)
        return {k: str(v) for k, v in data.items() if isinstance(k, str)}
    except Exception as e:  # never block startup on secrets fetch
        print(f"[config] Secrets Manager fetch skipped: {e}", flush=True)
        return {}


@lru_cache(maxsize=1)
def settings() -> dict[str, str]:
    """Resolved config: env override > Secrets Manager > ambient env. Cached for
    the process; call reset_settings() in tests to re-resolve."""
    sm = _load_secrets_manager()
    out: dict[str, str] = {}
    for key in SECRET_KEYS:
        # 1) explicit env override always wins; 2) then SM; 3) then ambient env
        val = os.environ.get(key)
        if val is None:
            val = sm.get(key)
        if val is None:
            val = os.environ.get(key, "")
        out[key] = val or ""
    return out


def reset_settings() -> None:
    """Drop the cached settings so the next settings() re-resolves (tests)."""
    settings.cache_clear()


def get(key: str, default: str = "") -> str:
    return settings().get(key, "") or default


def oauth_enabled() -> bool:
    """True when a real Box OAuth app is configured (the seam that will replace
    the Week-1 dev login). False until then — dev login stays in force."""
    return bool(get("BOX_OAUTH_CLIENT_ID"))
