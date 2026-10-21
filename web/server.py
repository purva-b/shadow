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

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse as _JR  # noqa: F401
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass

import json  # noqa: E402

from shadow_box import tools as T  # noqa: E402

# ids.json is tenant-specific (gitignored) and written by seed/setup_box.py. It
# is absent in CI and on a fresh deploy before seeding, so load it tolerantly:
# the server still imports and /health still answers; Box-backed endpoints
# degrade honestly until the tenant is seeded (rather than crashing at import).
_IDS_PATH = ROOT / "seed" / "ids.json"
try:
    IDS = json.loads(_IDS_PATH.read_text())
except (FileNotFoundError, ValueError):
    IDS = {"users": {}, "folders": {}, "file_ids": {}}
USERS = IDS.get("users", {})
FOLDERS = IDS.get("folders", {})
FILE_IDS = IDS.get("file_ids", {})

# Sidebar display metadata is DERIVED, not hardcoded: initials from the name,
# subtitle from the person's role on their Box people-card (cached). The
# switchable roster is every seeded App User, so a different tenant's users
# appear automatically.
def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p]
    return "".join(p[0].upper() for p in parts[:2]) or "?"


_SUBTITLE_CACHE: dict[str, str] = {}


def _subtitle_for(name: str) -> str:
    """Role text from the person's shadowPerson card; '' if unavailable.
    Cached so the sidebar stays cheap."""
    if name in _SUBTITLE_CACHE:
        return _SUBTITLE_CACHE[name]
    sub = ""
    try:
        from agents.answer import _people_cards

        for c in _people_cards():
            if c.get("name") == name:
                role = c.get("role", "")
                team = c.get("team", "")
                sub = f"{role} · {team}" if role and team else (role or team)
                break
    except Exception:
        sub = ""
    _SUBTITLE_CACHE[name] = sub
    return sub


def _user_meta(name: str) -> dict:
    return {"subtitle": _subtitle_for(name), "initials": _initials(name)}


SWITCHABLE = list(USERS.keys())

app = FastAPI(title="Shadow")

# the React/TS build dir (web/static-react, produced by `npm run build`).
# Defined up here so /health and the static mount below share one constant.
_REACT = Path(__file__).resolve().parent / "static-react"

# register source-specific hooks (e.g. Box live can_read probe for restricted
# Box records routed through the connector store)
try:
    from core import wiring as _wiring

    _wiring.install()
except Exception as _e:  # never block startup on wiring
    print("core wiring skipped:", _e)



def _uid(name: str) -> str:
    return USERS[name]


@app.get("/health")
def health():
    """Liveness + readiness probe for App Runner / ALB / ECS health checks.
    Cheap and dependency-free: it reports process health and whether the
    optional backends (Strands reasoning, DynamoDB activity) are reachable,
    without failing the check when they are not (honest degradation)."""
    strands_ok = False
    try:
        from core.llm import STRANDS_AVAILABLE

        strands_ok = bool(STRANDS_AVAILABLE)
    except Exception:
        strands_ok = False
    dynamo_ok = False
    try:
        from agents.activity import _table

        dynamo_ok = _table() is not None
    except Exception:
        dynamo_ok = False
    return {"status": "ok", "ui_built": (_REACT / "index.html").exists(),
            "strands": strands_ok, "dynamodb": dynamo_ok}


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


def _name_for(user_id: str) -> str:
    return next((n for n, uid in USERS.items() if str(uid) == str(user_id)), "")


@app.get("/api/me")
def me(request: Request):
    """The current session identity (or null). Identity is read from the signed
    session cookie ONLY — never from a request parameter."""
    from web.auth import current_user_id

    uid = current_user_id(request)
    name = _name_for(uid) if uid else ""
    return {
        "logged_in": bool(uid),
        "name": name or None,
        "user_id": uid,
        "meta": _user_meta(name) if name else {},
        # dev-login roster (only meaningful in dev mode)
        "options": [{"name": n, "user_id": _uid(n), **_user_meta(n)}
                    for n in SWITCHABLE],
    }


@app.post("/api/login")
async def login(req: Request, response: Response):
    """DEV login: establish a session as one of the seeded App Users. This is a
    stand-in for Box OAuth — identity is set server-side and carried in a signed
    cookie. Enabled only under SHADOW_DEV."""
    from web.auth import COOKIE, dev_mode, sign

    if not dev_mode():
        return JSONResponse({"error": "dev login disabled (set SHADOW_DEV=1)"},
                            status_code=404)
    body = await req.json()
    name = body.get("user") or body.get("name")
    if name not in USERS:
        return JSONResponse({"error": "unknown user"}, status_code=400)
    uid = _uid(name)
    response.set_cookie(COOKIE, sign(str(uid)), httponly=True, samesite="lax")
    return {"logged_in": True, "name": name, "user_id": uid,
            "meta": _user_meta(name)}


@app.post("/api/logout")
def logout(response: Response):
    from web.auth import COOKIE

    response.delete_cookie(COOKIE)
    return {"logged_in": False}


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
    from web.auth import current_user_id

    user_id = current_user_id(req)   # identity ONLY from the signed session
    if not user_id:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    body = await req.json()
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
    resolver can canonicalize it for connector ACL checks. The email domain
    comes from workspace config, not a hardcoded 'payments.example'."""
    name = next((n for n, uid in USERS.items() if str(uid) == str(user_id)), "")
    first = (name or "").split()[0].lower() if name else ""
    if not first:
        return name
    try:
        from core import taxonomy

        domain = taxonomy.canonical_domain() or "example.com"
    except Exception:
        domain = "example.com"
    return f"{first}@{domain}"


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
def notifications(request: Request):
    """Proactive notifications Shadow raised on new records, for the acting-as
    person (newest first)."""
    try:
        from core import notify
        from web.auth import current_user_id
        uid = current_user_id(request)
        principal = _principal_for(uid) if uid else ""
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
        # newest file in Meetings by CREATION DATE (not folder list order, which
        # is name-sorted and is not chronological).

        url = (f"https://api.box.com/2.0/folders/"
               f"{FOLDERS['Sources/Meetings']}/items"
               f"?fields=id,name,type&sort=date&direction=DESC&limit=1")
        try:
            r = T._SESSION.get(url, headers=T._headers(), timeout=30)
            entries = [it for it in r.json().get("entries", []) if it["type"] == "file"]
            if entries:
                file_id = entries[0]["id"]
        except Exception:
            file_id = None
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
    if not folder:          # unseeded tenant (no ids.json) -> nothing to list
        return {"rows": out}
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
    from web.auth import dev_mode
    if not dev_mode():
        return JSONResponse({"error": "simulate disabled (set SHADOW_DEV=1)"},
                            status_code=404)
    dec = FILE_IDS["Memory/Decisions/retry-idempotency-keys.md"]
    dana = next(iter(USERS.values()))  # dev-sim fallback assignee
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
def embed(file_id: str, request: Request):
    from web.auth import current_user_id
    uid = current_user_id(request)
    if not uid:
        return JSONResponse({"url": None, "error": "not logged in"}, status_code=401)
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
# the React/TS build (web/static-react, produced by `npm run build`); _REACT is
# defined near the top so /health can share it.
if (_REACT / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_REACT), html=True), name="static")
else:  # no build present — API still serves; surface a clear hint at root
    @app.get("/")
    def _no_build():
        return JSONResponse(
            {"error": "UI not built — run `cd web/ui && npm run build`"},
            status_code=503)


if __name__ == "__main__":
    import uvicorn

    # Bind localhost by default (safe for local dev); a container/host sets
    # SHADOW_HOST=0.0.0.0 to accept traffic from the platform's load balancer.
    host = os.environ.get("SHADOW_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8099")))
