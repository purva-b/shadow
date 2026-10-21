#!/usr/bin/env python3
"""Container entrypoint for Shadow.

1. Best-effort, idempotent provisioning of the DynamoDB activity table — so the
   deployed app uses DynamoDB rather than the JSONL fallback. It never blocks
   startup: if AWS creds or permissions are missing, the app still boots and the
   activity feed degrades to JSONL (honest degradation).
2. Launch uvicorn bound to $SHADOW_HOST:$PORT (the Dockerfile sets 0.0.0.0 and
   the platform sets $PORT).

Kept dependency-light and import-safe so `python deploy/entrypoint.py` is the
whole container command.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _provision_dynamo() -> None:
    try:
        from agents.dynamodb_setup import ensure_table

        state = ensure_table()
        print(f"[entrypoint] dynamodb activity table: {state}", flush=True)
    except Exception as e:  # never block startup on provisioning
        print(f"[entrypoint] dynamodb provisioning skipped: {e} "
              "(activity feed will use the JSONL fallback)", flush=True)


def main() -> None:
    _provision_dynamo()

    import uvicorn

    host = os.environ.get("SHADOW_HOST", "0.0.0.0")  # noqa: S104 — container bind is intentional
    port = int(os.environ.get("PORT", "8080"))
    print(f"[entrypoint] serving Shadow on {host}:{port}", flush=True)
    # import the app lazily (after sys.path + provisioning) so a bad import
    # surfaces in logs rather than at module load.
    from web.server import app

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
