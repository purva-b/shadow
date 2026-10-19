"""Shadow web backend — FastAPI serving the single-page UI + JSON API.

Endpoints:
  GET/POST /api/acting-as          current App User (default Alex)
  GET      /api/sources            Box folder counts + live flag
  POST     /api/ask                answer(user_id, question)
  POST     /api/ingest             ingest newest (or given) Meetings file
  GET      /api/activity           activity feed (DynamoDB or JSONL)
  GET      /api/decisions          decision records for the memory view
  POST     /api/simulate/confirm-task   complete Dana's task + confirm decision
  GET      /api/embed              expiring embed link for a file (as current user)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

from shadow_box import tools as T  # noqa: E402

import json  # noqa: E402

IDS = json.loads((ROOT / "seed" / "ids.json").read_text())
USERS = IDS["users"]
FOLDERS = IDS["folders"]
FILE_IDS = IDS["file_ids"]

# user display metadata for the sidebar
USER_META = {
    "Alex Rivera": {"subtitle": "New hire · day 6", "initials": "AR"},
    "Priya Nair": {"subtitle": "Senior engineer", "initials": "PN"},
    "Dana Kim": {"subtitle": "Engineering manager", "initials": "DK"},
}
SWITCHABLE = ["Alex Rivera", "Priya Nair", "Dana Kim"]

app = FastAPI(title="Shadow")

# register source-specific hooks (e.g. Box live can_read probe for restricted
# Box records routed through the connector store)
try:
    from core import wiring as _wiring

    _wiring.install()
except Exception as _e:  # never block startup on wiring
    print("core wiring skipped:", _e)

_state = {"acting_as": "Alex Rivera"}


def _uid(name: str) -> str:
    return USERS[name]


# --------------------------------------------------------------------------- #
def _agents():
    """Import agents lazily so the server still boots if agents import fails."""
    try:
        from agents import answer as answer_mod
        from agents import ingest as ingest_mod

        return answer_mod, ingest_mod
    except Exception as e:  # pragma: no cover
        print("agents import failed:", e)
        return None, None


@app.get("/api/acting-as")
def get_acting_as():
    name = _state["acting_as"]
    return {
        "name": name, "user_id": _uid(name),
        "meta": USER_META.get(name, {}),
        "options": [{"name": n, "user_id": _uid(n), **USER_META.get(n, {})}
                    for n in SWITCHABLE],
    }


@app.post("/api/acting-as")
async def set_acting_as(req: Request):
    body = await req.json()
    name = body.get("user") or body.get("name")
    if name in USERS:
        _state["acting_as"] = name
    return get_acting_as()


@app.get("/api/sources")
def sources():
    def count(key):
        try:
            return T.folder_item_count(FOLDERS[key])
        except Exception:
            return 0
    issues = count("Sources/Issues") + count("Sources/PRs")
    live = bool(os.environ.get("BOX_WEBHOOK_ID"))
    # connector records synced into the store (GitHub/Slack/...)
    connectors_count: dict[str, int] = {}
    try:
        from core.store import store as _store

        connectors_count = _store.count_by_source()
    except Exception:
        connectors_count = {}
    return {
        "meetings": count("Sources/Meetings"),
        "slack": count("Sources/Slack"),
        "issues_prs": issues,
        "docs": count("Sources/Docs"),
        "live": live,
        "connectors": connectors_count,
    }


@app.post("/api/ask")
async def ask(req: Request):
    body = await req.json()
    user_id = str(body.get("user_id") or _uid(_state["acting_as"]))
    question = (body.get("question") or "").strip()
    context = body.get("context") or []  # prior questions in this conversation
    if not isinstance(context, list):
        context = []

    # 1) Try connector records (GitHub/Slack/...) first. The asker's principal
    #    is the acting-as person's email, which the identity map resolves to the
    #    canonical principal used in connector ACLs.
    principal = _principal_for(user_id)
    try:
        # agentic path: a Strands Agent orchestrates the native source searches
        # (GitHub/Slack/Confluence) + the semantic index. Opt-in (slower).
        from core import agent_answer

        if agent_answer.is_enabled():
            hit = agent_answer.answer_agentic(principal, question, context=context)
            if hit is not None:
                return hit

        from core.answer_connectors import answer_from_connectors

        hit = answer_from_connectors(principal, question, context=context)
        if hit is not None:
            return hit
    except Exception as e:  # never let the connector path break the Box answer
        print("connector answer path error:", e)

    # 2) Fall back to the Box pipeline (unchanged).
    answer_mod, _ = _agents()
    if answer_mod is None:
        return JSONResponse(_stub_answer(question), status_code=200)
    try:
        result = answer_mod.answer(user_id, question, context=context)
    except Exception as e:  # pragma: no cover
        result = _stub_answer(question, error=str(e))
    return result


def _principal_for(user_id: str) -> str:
    """Map the acting-as Box App User id -> that person's email, so the identity
    resolver can canonicalize it for connector ACL checks."""
    name = next((n for n, uid in USERS.items() if str(uid) == str(user_id)),
                _state["acting_as"])
    first = (name or "").split()[0].lower() if name else ""
    return f"{first}@payments.example" if first else name


@app.post("/api/sync/{connector}")
async def sync_connector(connector: str, limit: int = 50):
    """Pull a connector's records into the store so Shadow can answer across it.
    e.g. POST /api/sync/github  (needs GITHUB_TOKEN + GITHUB_REPO in env).
    Pass ?limit=N to raise the per-sync cap (default 50)."""
    try:
        from core.sync import sync

        # Box needs its folder-id map to enumerate the corpus; other connectors
        # read their config from env.
        kwargs = {"folders": FOLDERS} if connector == "box" else {}
        return sync(connector, limit=limit, **kwargs)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/webhook/{connector}")
async def webhook(connector: str, req: Request):
    """Receive a push event from a connector's origin system, verify it, fetch
    the changed item, and ingest it live. Each connector declares its webhook
    contract (path, signature header, secret env) via subscribe(). This makes
    proactive-on-real-events real instead of poll-only.

    Payload shape is connector-specific; we only need enough to build a
    SourceRef (an external id / hint), then the connector's own fetch()
    normalizes it. Signature verification runs when the secret env is set.
    """
    import hashlib
    import hmac
    import os as _os

    raw = await req.body()
    try:
        import connectors as _conns
        from core import ingest as _core_ingest
        from core.store import store as _store

        if connector not in _conns.available():
            return {"ok": False, "error": f"unknown connector {connector!r}"}
        kwargs = {"folders": FOLDERS} if connector == "box" else {}
        conn = _conns.load(connector, **kwargs)
        spec = conn.subscribe()

        # verify the signature when the connector declares one and the secret
        # is configured (otherwise accept — dev/local).
        if spec and spec.verify_header and spec.secret_env:
            secret = _os.environ.get(spec.secret_env, "")
            sig = req.headers.get(spec.verify_header, "")
            if secret:
                mac = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                if not (sig and hmac.compare_digest(mac, sig.split("=")[-1])):
                    return JSONResponse({"ok": False, "error": "bad signature"},
                                        status_code=401)

        body = await req.json() if raw else {}
        # Build a SourceRef from the payload. Connectors expose an id in common
        # fields; fall back to a generic 'id'. The connector's fetch does the rest.
        from connectors.base import SourceRef

        ext_id = str(body.get("external_id") or body.get("id")
                     or (body.get("resource") or {}).get("id") or "")
        if not ext_id:
            return {"ok": False, "error": "no item id in payload"}
        ref = SourceRef(source=connector, external_id=ext_id, hint=body.get("hint", {}))
        record = conn.fetch(ref)
        result = _core_ingest.ingest_record(record, store=_store)
        return {"ok": True, "connector": connector, "ingested": ext_id,
                "notifications": result.get("notifications", [])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/notifications")
def notifications():
    """Proactive notifications Shadow raised on new records, for the acting-as
    person (newest first)."""
    try:
        from core import notify

        principal = _principal_for(_uid(_state["acting_as"]))
        return {"rows": notify.recent(limit=20, principal=principal),
                "all": notify.recent(limit=20)}
    except Exception as e:
        return {"rows": [], "all": [], "error": str(e)}


@app.post("/api/ingest")
async def ingest(req: Request):
    body = await req.json()
    file_id = body.get("file_id")
    _, ingest_mod = _agents()
    if not file_id:
        # newest file in Meetings
        items = [it for it in T.folder_items(FOLDERS["Sources/Meetings"])
                 if it["type"] == "file"]
        if items:
            file_id = items[-1]["id"]
    if ingest_mod is None or not file_id:
        return {"ok": False, "reason": "no file / agents unavailable"}
    try:
        return ingest_mod.ingest(file_id)
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": str(e)}


@app.get("/api/activity")
def activity():
    try:
        from agents.activity import read_activity

        return {"rows": read_activity(100)}
    except Exception:
        return {"rows": []}


@app.get("/api/decisions")
def decisions():
    folder = FOLDERS.get("Memory/Decisions")
    out = []
    try:
        for it in T.folder_items(folder):
            if it["type"] != "file":
                continue
            md = T.get_metadata(it["id"], "shadowDecision") or {}
            out.append({
                "file_id": it["id"], "name": it["name"],
                "decision": md.get("decision", ""),
                "status": md.get("status", ""),
                "owners": md.get("owners", ""),
                "confirmed": md.get("confirmed", ""),
                "box_url": T.box_web_url(it["id"]),
            })
    except Exception:
        pass
    return {"rows": out}


@app.post("/api/simulate/confirm-task")
def simulate_confirm():
    dec = FILE_IDS["Memory/Decisions/retry-idempotency-keys.md"]
    dana = USERS["Dana Kim"]
    closed = 0
    try:
        for t in T.list_task_assignments(dec):
            if t.get("resolution_state") != "completed":
                aid = t.get("assigned_to", {}).get("id", dana)
                T.complete_task(t["id"], as_user=aid)
                closed += 1
        T.apply_metadata(dec, "shadowDecision", {"confirmed": "yes"})
        try:
            from agents.activity import log_activity

            log_activity("Librarian", "Dana confirmed the decision", dec,
                         "confirmed=yes")
        except Exception:
            pass
        return {"ok": True, "closed": closed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/embed")
def embed(file_id: str):
    uid = _uid(_state["acting_as"])
    try:
        link = T.embed_link(file_id, as_user=uid)
        return {"url": link}
    except Exception:
        return {"url": None}


def _stub_answer(question: str, error: str = "") -> dict:
    """Fallback used only if the agents module cannot be imported."""
    return {
        "answer_markdown": "Shadow is still warming up. Try again in a moment."
                           + (f" ({error})" if error else ""),
        "citations": [], "decision_status": None, "who_to_ask": None,
        "restricted_note": None,
    }


# static SPA (mounted last so /api wins) ------------------------------------- #
# prefer the React/TS build (web/static-react); fall back to the vanilla UI.
_REACT = Path(__file__).resolve().parent / "static-react"
_VANILLA = Path(__file__).resolve().parent / "static"
STATIC = _REACT if (_REACT / "index.html").exists() else _VANILLA
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8099")))
