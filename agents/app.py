"""AgentCore runtime entrypoint for Shadow.

One runtime, two modes:
  {"mode": "ingest", "file_id": "..."}
  {"mode": "ask", "user_id": "...", "question": "..."}

Works deployed on Bedrock AgentCore and also runs locally (python agents/app.py)
on port 8080 as a fallback when AgentCore deploy is unavailable (brief §13).
"""
from __future__ import annotations

import json
import os

from .answer import answer
from .ingest import ingest


def handler(payload: dict) -> dict:
    """Pure routing function — used by AgentCore and by the local fallback."""
    mode = (payload or {}).get("mode")
    if mode == "ingest":
        return ingest(payload["file_id"])
    if mode == "ask":
        return answer(payload["user_id"], payload["question"])
    return {"error": f"unknown mode: {mode}"}


# AgentCore wrapper (guarded so the module imports without the toolkit) --------
try:
    from bedrock_agentcore import BedrockAgentCoreApp  # type: ignore

    app = BedrockAgentCoreApp()

    @app.entrypoint
    def agentcore_entrypoint(payload):  # pragma: no cover - runtime path
        return handler(payload)

    _HAS_AGENTCORE = True
except Exception:  # toolkit not installed -> local fallback only
    app = None
    _HAS_AGENTCORE = False


def _run_local():  # pragma: no cover - local fallback server
    """Minimal HTTP server on :8080 mirroring the AgentCore invoke contract."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
            out = handler(payload)
            body = json.dumps(out, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass

    port = int(os.environ.get("PORT", "8080"))
    print(f"Shadow local runtime on http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    if _HAS_AGENTCORE and os.environ.get("AGENTCORE_RUN"):
        app.run()  # type: ignore
    else:
        _run_local()
