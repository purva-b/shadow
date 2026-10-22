"""Activity feed writer: DynamoDB `shadow_activity` if reachable, else JSONL.

The web backend reads whichever source has rows. Keeping both paths means the
live feed works even before the DynamoDB table exists.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

JSONL = Path(__file__).resolve().parent.parent / "web" / "activity_log.jsonl"
TABLE = "shadow_activity"

_dynamo = None
_dynamo_tried = False


def _table():
    global _dynamo, _dynamo_tried
    if _dynamo_tried:
        return _dynamo
    _dynamo_tried = True
    try:
        import boto3

        region = os.environ.get("AWS_REGION", "us-east-1")
        ddb = boto3.resource("dynamodb", region_name=region)
        t = ddb.Table(TABLE)  # type: ignore[attr-defined]  # boto3 resource is dynamically typed
        t.load()  # raises if missing / no creds
        _dynamo = t
    except Exception:
        _dynamo = None
    return _dynamo


def log_activity(agent: str, action: str, box_file_id: str = "", detail: str = "") -> dict:
    row = {
        "ts": f"{time.time():.6f}",
        "agent": agent,
        "action": action,
        "box_file_id": box_file_id or "",
        "detail": detail or "",
    }
    t = _table()
    if t is not None:
        try:
            t.put_item(Item=row)
            return row
        except Exception:
            pass
    # fallback: append JSONL
    JSONL.parent.mkdir(parents=True, exist_ok=True)
    with JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def read_activity(limit: int = 100) -> list[dict]:
    rows: list[dict] = []
    t = _table()
    if t is not None:
        try:
            rows = t.scan().get("Items", [])
        except Exception:
            rows = []
    if not rows and JSONL.exists():
        rows = [json.loads(line) for line in JSONL.read_text().splitlines() if line.strip()]
    rows.sort(key=lambda r: float(r.get("ts", 0)), reverse=True)
    return rows[:limit]
