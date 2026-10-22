"""AWS Lambda (Function URL) for the Box FILE.UPLOADED webhook.

Verifies the Box signature, writes an activity row, and invokes the AgentCore
runtime with {"mode":"ingest","file_id":...}. Includes handle(event) so the
same logic is testable locally without AWS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime

PRIMARY = os.environ.get("BOX_WEBHOOK_KEY_1", "")
SECONDARY = os.environ.get("BOX_WEBHOOK_KEY_2", "")
RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
MAX_SKEW_MIN = 10


def _sig(body: str, timestamp: str, key: str) -> str:
    msg = (body + timestamp).encode("utf-8")
    digest = hmac.new(key.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify(body: str, headers: dict) -> bool:
    ts = headers.get("box-delivery-timestamp") or headers.get("Box-Delivery-Timestamp", "")
    s1 = headers.get("box-signature-primary") or headers.get("Box-Signature-Primary", "")
    s2 = headers.get("box-signature-secondary") or headers.get("Box-Signature-Secondary", "")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if abs((datetime.now(UTC) - dt).total_seconds()) > MAX_SKEW_MIN * 60:
            return False
    except Exception:
        return False
    for key, sig in ((PRIMARY, s1), (SECONDARY, s2)):
        if key and sig and hmac.compare_digest(_sig(body, ts, key), sig):
            return True
    return False


def _invoke_agentcore(file_id: str) -> dict:
    if not RUNTIME_ARN:
        return {"invoked": False, "reason": "no AGENTCORE_RUNTIME_ARN"}
    import boto3

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    payload = json.dumps({"mode": "ingest", "file_id": file_id}).encode()
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN, payload=payload)
    return {"invoked": True, "status": resp.get("statusCode", 200)}


def handle(event_body: str, headers: dict) -> dict:
    if not verify(event_body, headers):
        return {"statusCode": 401, "body": "invalid signature"}
    data = json.loads(event_body or "{}")
    src = data.get("source", {}) or {}
    file_id = src.get("id")
    trigger = data.get("trigger", "")
    if trigger != "FILE.UPLOADED" or not file_id:
        return {"statusCode": 200, "body": "ignored"}
    # activity row (best-effort)
    try:
        from agents.activity import log_activity

        log_activity("Webhook", f"FILE.UPLOADED {src.get('name', file_id)}", file_id)
    except Exception:
        pass
    result = _invoke_agentcore(file_id)
    return {"statusCode": 200, "body": json.dumps({"ok": True, **result})}


def lambda_handler(event, context):  # AWS Lambda Function URL entrypoint
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return handle(body, headers)
