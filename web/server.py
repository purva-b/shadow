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
    return {
        "meetings": count("Sources/Meetings"),
        "slack": count("Sources/Slack"),
        "issues_prs": issues,
        "docs": count("Sources/Docs"),
        "live": live,
    }


@app.post("/api/ask")
async def ask(req: Request):
    body = await req.json()
    user_id = str(body.get("user_id") or _uid(_state["acting_as"]))
    question = (body.get("question") or "").strip()
    context = body.get("context") or []  # prior questions in this conversation
    if not isinstance(context, list):
        context = []
    answer_mod, _ = _agents()
    if answer_mod is None:
        return JSONResponse(_stub_answer(question), status_code=200)
    try:
        result = answer_mod.answer(user_id, question, context=context)
    except Exception as e:  # pragma: no cover
        result = _stub_answer(question, error=str(e))
    return result


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
