"""Strands @tool wrappers over Shadow's Box operations.

These turn the Box layer (shadow_box.tools) into tools a Strands Agent can CALL
during its own reasoning loop — so the agent decides what to do (search team
memory, check what the asker can read, ask Box AI, look up who to ask, create a
task) rather than Python code sequencing it. Every retrieval tool runs AS the
asking user, so Box permissions — not the agent — decide visibility.

The asker's user id is bound per-request via set_actor() so the tool signatures
stay clean for the model (it never has to pass a user id).
"""
from __future__ import annotations

import json
import threading

from strands import tool

from shadow_box import tools as T

from .common import ids

# per-thread actor binding (the App User the agent is answering AS)
_local = threading.local()


def set_actor(user_id: str) -> None:
    _local.user_id = str(user_id)


def _actor() -> str:
    return getattr(_local, "user_id", "")


def _folders() -> dict:
    return ids()["folders"]


@tool
def search_team_memory(component: str = "", issue_id: str = "") -> str:
    """Search the Payments team's Box memory for source files about a component
    (one of: retry-service, payments-api, ledger, gateway-client, checkout) or a
    specific issue id (e.g. PAY-482). Runs AS the current user, so only files
    they are allowed to see are returned. Returns a JSON list of
    {file_id, name, source_type, date, summary}.

    Use this FIRST to find what the team knows about the question's topic."""
    uid = _actor()
    anchors = [_folders()["Sources"], _folders()["Memory"]]
    hits: dict[str, dict] = {}
    # issue id is a reliably-queryable string field
    if issue_id:
        for anc in anchors:
            for r in T.metadata_query("shadowItem", "issueIds LIKE :i",
                                      {"i": f"%{issue_id}%"}, anc,
                                      as_user=uid, limit=50):
                hits[r["id"]] = r
    # component match: enumerate readable source folders (multiSelect isn't
    # reliably queryable) and filter in code
    if component and not hits:
        scan = ["Sources/Issues", "Sources/PRs", "Sources/Docs",
                "Sources/Meetings", "Sources/Slack", "Sources/Org"]
        for key in scan:
            fid = _folders().get(key)
            if not fid:
                continue
            try:
                listing = T.folder_items(fid, as_user=uid)
            except Exception:
                continue
            for it in listing:
                if it.get("type") != "file":
                    continue
                md = T.get_metadata(it["id"], "shadowItem", as_user=uid) or {}
                if component in (md.get("components") or []):
                    hits[it["id"]] = {"id": it["id"], "name": it["name"]}
    out = []
    for fid, r in list(hits.items())[:8]:
        md = T.get_metadata(fid, "shadowItem", as_user=uid) or {}
        out.append({
            "file_id": fid,
            "name": r.get("name") or md.get("title") or fid,
            "source_type": md.get("sourceType", ""),
            "date": (md.get("occurredOn", "") or "")[:10],
            "summary": md.get("summary", ""),
        })
    return json.dumps(out)


@tool
def find_restricted_topic(topic: str) -> str:
    """Check whether a Restricted source on this topic exists that the current
    user CANNOT read — the privacy boundary — WITHOUT leaking anything about it.
    Returns JSON {found: bool, restricted_only: bool} ONLY. It deliberately does
    NOT return the file's title, topic, or attendees: revealing those would leak
    the existence and contents of a document the asker is not allowed to see.
    When found is true, tell the asker a related source exists they can't access
    and point them at a readable owner — never anything from the restricted file.
    restricted_only=true means no public source exists, so the answer is a refusal."""
    uid = _actor()
    folder = _folders().get("Restricted")
    if not folder:
        return json.dumps({"found": False})
    only_terms = ["vendor", "pricing", "contract", "design review"]
    q = topic.lower()
    for it in T.folder_items(folder):  # service-account view (finds the file)
        if it["type"] != "file":
            continue
        if T.can_read(it["id"], uid):  # asker can read -> not restricted for them
            continue
        md = T.get_metadata(it["id"], "shadowItem") or {}
        hay = f"{(md.get('title') or it['name'])} {md.get('summary','')}".lower()
        matched = [t for t in (only_terms + ["checkout", "budget", "spend", "cost"])
                   if t in q and t in hay]
        if matched:
            # return ONLY the boolean signals — nothing about the file crosses out
            return json.dumps({
                "found": True,
                "restricted_only": all(t in only_terms for t in matched),
            })
    return json.dumps({"found": False})


@tool
def box_ai_answer(file_ids: list[str], question: str) -> str:
    """Ask Box AI to answer `question` grounded in the given file_ids, AS the
    current user (Box permissions apply). Returns JSON {answer, citations}.
    Pass only file_ids returned by search_team_memory. Use this to get a cited,
    source-grounded answer rather than writing one from memory."""
    uid = _actor()
    readable = [f for f in file_ids if T.can_read(f, uid)][:5]
    if not readable:
        return json.dumps({"answer": "", "citations": [], "error": "none readable"})
    res = T.ai_ask(readable, question, as_user=uid)
    return json.dumps({"answer": res.get("answer", ""),
                       "citations": res.get("citations", []),
                       "error": res.get("error", "")})


@tool
def lookup_people(component: str = "") -> str:
    """Look up the Payments team's people cards, optionally filtered to owners of
    a component. Returns JSON list of {name, role, team, active, expertise,
    moved_note}. Use this to decide WHO the asker should talk to, and to notice
    when a past author has moved teams."""
    folder = _folders().get("Memory/People")
    out = []
    if folder:
        for it in T.folder_items(folder):
            if it.get("type") != "file":
                continue
            md = T.get_metadata(it["id"], "shadowPerson")
            if not md:
                continue
            exp = md.get("expertise") or []
            if component and component not in exp:
                continue
            out.append({"name": md.get("name"), "role": md.get("role"),
                        "team": md.get("team"), "active": md.get("active"),
                        "expertise": exp})
    return json.dumps(out)


@tool
def get_decision(component: str = "") -> str:
    """Read the team's recorded decisions for a component. Returns JSON list of
    {decision, status, confirmed, decidedOn}. Use this to state the current
    decision status (paused / decided / awaiting confirmation)."""
    uid = _actor()
    folder = _folders().get("Memory/Decisions")
    out = []
    if folder:
        for it in T.folder_items(folder, as_user=uid):
            if it.get("type") != "file":
                continue
            md = T.get_metadata(it["id"], "shadowDecision", as_user=uid)
            if not md:
                continue
            if component and component not in (md.get("components") or []):
                continue
            out.append({"decision": md.get("decision", ""),
                        "status": md.get("status", ""),
                        "confirmed": md.get("confirmed", ""),
                        "decidedOn": (md.get("decidedOn", "") or "")[:10]})
    return json.dumps(out)


ANSWER_TOOLS = [search_team_memory, find_restricted_topic, box_ai_answer,
                lookup_people, get_decision]
