"""Per-user, per-connector OAuth token store.

When a user connects Slack/GitHub/Confluence/Calendar, the grant is THEIRS, so
the token is keyed by (user_id, connector) — a later sync for that user reads
that user's own token and therefore sees only what that user can see.

Storage mirrors the activity feed's honest-degradation pattern:
  * DynamoDB table `shadow_connector_tokens` when reachable (prod);
  * a local JSON file otherwise (dev), under web/.connector_tokens.json which is
    gitignored — tokens are secrets and must never be committed.

The store is deliberately tiny: save / get / delete / list_for. Nothing here
refreshes tokens; a connector that needs a refresh does it at call time using
the stored refresh_token.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

TABLE = "shadow_connector_tokens"
_LOCAL = Path(__file__).resolve().parent.parent / "web" / ".connector_tokens.json"
_LOCK = Lock()


def _ddb_table():
    """Return the DynamoDB table resource, or None if unreachable/unconfigured."""
    if not os.environ.get("SHADOW_USE_DDB_TOKENS"):
        return None
    try:
        import boto3

        region = os.environ.get("AWS_REGION", "us-east-1")
        ddb = boto3.resource("dynamodb", region_name=region)
        return ddb.Table(TABLE)  # type: ignore[attr-defined]
    except Exception:
        return None


def _key(user_id: str, connector: str) -> str:
    return f"{user_id}::{connector}"


def _load_local() -> dict:
    try:
        return json.loads(_LOCAL.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _write_local(data: dict) -> None:
    _LOCAL.parent.mkdir(parents=True, exist_ok=True)
    tmp = _LOCAL.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=0))
    tmp.replace(_LOCAL)
    try:
        _LOCAL.chmod(0o600)  # tokens are secrets
    except OSError:
        pass


def save(user_id: str, connector: str, token: dict) -> None:
    t = _ddb_table()
    if t is not None:
        try:
            t.put_item(Item={"pk": _key(user_id, connector), "user_id": user_id,
                             "connector": connector, "token": json.dumps(token)})
            return
        except Exception:
            pass
    with _LOCK:
        data = _load_local()
        data[_key(user_id, connector)] = token
        _write_local(data)


def get(user_id: str, connector: str) -> dict | None:
    t = _ddb_table()
    if t is not None:
        try:
            item = t.get_item(Key={"pk": _key(user_id, connector)}).get("Item")
            if item and item.get("token"):
                return json.loads(item["token"])
        except Exception:
            pass
    with _LOCK:
        return _load_local().get(_key(user_id, connector))


def delete(user_id: str, connector: str) -> None:
    t = _ddb_table()
    if t is not None:
        try:
            t.delete_item(Key={"pk": _key(user_id, connector)})
        except Exception:
            pass
    with _LOCK:
        data = _load_local()
        if data.pop(_key(user_id, connector), None) is not None:
            _write_local(data)


def list_for(user_id: str) -> dict[str, dict]:
    """All connector tokens this user holds: {connector: token}."""
    out: dict[str, dict] = {}
    with _LOCK:
        for k, v in _load_local().items():
            uid, _, conn = k.partition("::")
            if uid == user_id:
                out[conn] = v
    # (DynamoDB listing omitted: the connections page reads per-connector via get)
    return out


def connected(user_id: str, connector: str) -> bool:
    tok = get(user_id, connector)
    return bool(tok and tok.get("access_token"))
