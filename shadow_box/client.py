"""Box CCG client factory for Shadow.

Two kinds of client:
  * get_client()               -> service account (box_subject_type=enterprise)
  * get_client(user_id="123")  -> acts AS an App User (box_subject_type=user)

The service account runs background/ingest work and administers the enterprise.
Every *answer* path must use get_client(user_id=<app user id>) so that Box
permissions — not prompt text — decide what the asker can read.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from box_sdk_gen import BoxCCGAuth, BoxClient, CCGConfig

try:  # load .env for local dev; never committed
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover - dotenv optional in prod
    pass


def _cfg_base() -> dict:
    cid = os.environ["BOX_CLIENT_ID"]
    csec = os.environ["BOX_CLIENT_SECRET"]
    if not cid or not csec:
        raise RuntimeError("BOX_CLIENT_ID / BOX_CLIENT_SECRET not set")
    return {"client_id": cid, "client_secret": csec}


@lru_cache(maxsize=1)
def _enterprise_id() -> str:
    eid = os.environ.get("BOX_ENTERPRISE_ID", "")
    if not eid:
        raise RuntimeError("BOX_ENTERPRISE_ID not set")
    return eid


@lru_cache(maxsize=1)
def get_service_client() -> BoxClient:
    """Service account client (enterprise subject).

    Cached per process: box-sdk-gen's BoxCCGAuth caches and auto-refreshes the
    access token internally, so the win is NOT rebuilding the auth object (and
    discarding its token cache) on every call. One client, tokens refreshed on
    expiry by the SDK."""
    base = _cfg_base()
    auth = BoxCCGAuth(
        config=CCGConfig(
            client_id=base["client_id"],
            client_secret=base["client_secret"],
            enterprise_id=_enterprise_id(),
        )
    )
    return BoxClient(auth=auth)


@lru_cache(maxsize=64)
def get_user_client(user_id: str) -> BoxClient:
    """Client that acts AS a specific App User (user-subject token).

    Cached per user id (bounded LRU): reusing the auth object keeps the SDK's
    per-user token cache alive across calls, so an answer that makes many Box
    reads AS the same asker fetches one token, not one per read. Tokens still
    refresh on expiry inside the SDK."""
    base = _cfg_base()
    auth = BoxCCGAuth(
        config=CCGConfig(
            client_id=base["client_id"],
            client_secret=base["client_secret"],
            enterprise_id=_enterprise_id(),
        )
    )
    # with_user_subject returns a new auth bound to that user
    user_auth = auth.with_user_subject(user_id)
    return BoxClient(auth=user_auth)


def get_client(user_id: str | None = None) -> BoxClient:
    """Service client when user_id is None, else an as-user client."""
    if user_id:
        return get_user_client(str(user_id))
    return get_service_client()


def client_as(user_id: str) -> BoxClient:
    """Explicit alias used by the answer path."""
    return get_user_client(str(user_id))
