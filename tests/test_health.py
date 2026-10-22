"""Health-probe test (Week 5 deploy).

The container / App Runner readiness check hits GET /health. It must return 200
with a status payload and never hard-fail on a missing optional backend
(DynamoDB, Strands) — honest degradation. Uses FastAPI's TestClient, no network.
"""
from __future__ import annotations


def test_health_returns_ok():
    from fastapi.testclient import TestClient

    from web.server import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # the optional-backend flags are present and boolean (present even when the
    # backend is unreachable — the probe degrades, it does not 500)
    assert isinstance(body["strands"], bool)
    assert isinstance(body["dynamodb"], bool)
    assert isinstance(body["ui_built"], bool)
