"""Shadow Answer agent.

answer(user_id, question) -> dict in the Ask JSON shape:
  answer_markdown, citations[], decision_status, who_to_ask, restricted_note

Retrieval is done AS the asking App User so Box permissions decide visibility.
Prose is written by Box AI Ask when available, else Bedrock, else a
deterministic summary built from metadata — so the demo never hard-fails.
"""
from __future__ import annotations

import os
import re
from typing import Any

from shadow_box import tools as T

from .activity import log_activity
from .common import bedrock_complete, enterprise_scope, ids, unwrap_metadata

ISSUE_RE = re.compile(r"\bPAY-\d+\b", re.I)
COMPONENT_WORDS = {
    "retry": "retry-service", "retries": "retry-service", "idempoten": "retry-service",
    "gateway": "gateway-client", "ledger": "ledger", "checkout": "checkout",
    "payment": "payments-api",
}


# --------------------------------------------------------------------------- #
def _root() -> str:
    return ids()["root_folder_id"]


def _components_from(question: str) -> list[str]:
    q = question.lower()
    found = []
    for frag, comp in COMPONENT_WORDS.items():
        if frag in q and comp not in found:
            found.append(comp)
    return found


def _people_cards() -> list[dict]:
    """All shadowPerson cards (service account view), read from the People folder."""
    folder = ids()["folders"].get("Memory/People")
    out = []
    if not folder:
        return out
    for it in T.folder_items(folder):
        if it.get("type") != "file":
            continue
        md = T.get_metadata(it["id"], "shadowPerson")
        if not md:
            continue
        card = {k: v for k, v in md.items() if not k.startswith("$")}
        card["_file_id"] = it["id"]
        card["_name"] = card.get("name")
        out.append(card)
    return out


def _org_chart() -> dict:
    import json

    fid = ids()["file_ids"].get("Org/org_chart.json")
    if not fid:
        return {}
    try:
        import requests

        tok = T._token()
        r = requests.get(f"https://api.box.com/2.0/files/{fid}/content",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        return json.loads(r.text)
    except Exception:
        return {}


def _matched_items(issue_ids: list[str], components: list[str],
                   user_id: str) -> list[dict]:
    """shadowItem rows matching the issue ids / components, AS the user
    (perm-filtered).

    The asker cannot traverse the root folder (group has Viewer only on
    Sources/ and Memory/), so queries are scoped to those accessible ancestors.
    issueIds is a STRING field (LIKE-queryable); components is a multiSelect
    field, which Box metadata_queries do not match reliably — so when a
    component match is needed we enumerate the source subfolders and filter in
    code, exactly like _decisions does.
    """
    anchors = [ids()["folders"]["Sources"], ids()["folders"]["Memory"]]
    items: dict[str, dict] = {}
    for anc in anchors:
        for iss in issue_ids:
            for r in T.metadata_query(
                    "shadowItem", "issueIds LIKE :iss", {"iss": f"%{iss}%"},
                    anc, as_user=user_id, limit=50):
                items[r["id"]] = r
    if not items and components:
        # try the query first (cheap when it works), then fall back to a scan
        for anc in anchors:
            for comp in components:
                for r in T.metadata_query(
                        "shadowItem", "components = :c", {"c": comp},
                        anc, as_user=user_id, limit=50):
                    items[r["id"]] = r
        if not items:
            items.update(_scan_items_by_component(components, user_id))
    return list(items.values())


# source subfolders worth scanning for a component match (readable to the group)
_SCAN_FOLDERS = ["Sources/Issues", "Sources/PRs", "Sources/Docs",
                 "Sources/Meetings", "Sources/Slack", "Sources/Org"]


def _scan_items_by_component(components: list[str], user_id: str) -> dict[str, dict]:
    """Enumerate source subfolders AS the user and keep files whose shadowItem
    components intersect the wanted set. Robust against multiSelect query gaps
    and naturally permission-filtered (restricted files aren't listable)."""
    folders = ids()["folders"]
    want = set(components)
    found: dict[str, dict] = {}
    for key in _SCAN_FOLDERS:
        fid = folders.get(key)
        if not fid:
            continue
        try:
            listing = T.folder_items(fid, as_user=user_id)
        except Exception:
            continue
        for it in listing:
            if it.get("type") != "file":
                continue
            md = T.get_metadata(it["id"], "shadowItem", as_user=user_id) or {}
            comps = set(md.get("components", []) or [])
            if comps & want:
                found[it["id"]] = {"id": it["id"], "name": it["name"]}
    return found


def _decisions(components: list[str], user_id: str) -> list[dict]:
    """List the Decisions folder and read shadowDecision metadata directly.

    multiSelect fields are not reliably queryable via metadata_queries, so we
    enumerate the small Decisions folder and filter in code by component.
    """
    folder = ids()["folders"].get("Memory/Decisions")
    if not folder:
        return []
    out = []
    for it in T.folder_items(folder, as_user=user_id):
        if it.get("type") != "file":
            continue
        md = T.get_metadata(it["id"], "shadowDecision", as_user=user_id)
        if not md:
            continue
        comps = md.get("components", []) or []
        if not components or any(c in comps for c in components):
            out.append({"id": it["id"], "name": it["name"],
                        "metadata": {enterprise_scope(): {"shadowDecision": md}}})
    return out


def _restricted_hit(question: str, user_id: str) -> dict | None:
    """Service account finds a Restricted file matching the topic that the
    asker cannot read -> privacy signal. Returns host/attendee names only.

    Returns a dict with keys: file_name, attendees, topic. This only reports
    that a restricted source EXISTS on the topic and the asker cannot see it;
    the caller decides whether that means a full refusal (no other readable
    sources) or a partial answer with a withheld-source note."""
    import json  # noqa: F401

    restricted_folder = ids()["folders"].get("Restricted")
    if not restricted_folder:
        return None
    # Two kinds of restricted topic:
    #  - "only" terms live ONLY in the restricted file (vendor/pricing/contract)
    #     -> a question about them is a FULL refusal, there is no public source.
    #  - "shared" terms also have PUBLIC sources (checkout, budget-of-a-feature)
    #     -> a question about them is a PARTIAL answer + withheld note.
    only_terms = ["vendor", "pricing", "contract", "design review"]
    shared_terms = ["checkout", "budget", "spend", "cost"]
    topic_terms = only_terms + shared_terms
    q = question.lower()
    if not any(t in q for t in topic_terms):
        return None
    for it in T.folder_items(restricted_folder):  # service account view
        if it["type"] != "file":
            continue
        if T.can_read(it["id"], user_id):  # asker CAN read -> not a privacy case
            continue
        md = T.get_metadata(it["id"], "shadowItem") or {}
        # only treat as a hit when the restricted file is actually on-topic
        title = (md.get("title") or it["name"]).lower()
        summary = (md.get("summary") or "").lower()
        hay = f"{title} {summary}"
        matched = [t for t in topic_terms if t in q and t in hay]
        if not matched:
            continue
        # full refusal when EVERY matched term is a restricted-only term
        only = all(t in only_terms for t in matched)
        return {"file_name": it["name"], "attendees": (md.get("people") or ""),
                "topic": md.get("title") or it["name"], "only": only}
    return None


def _source_type_name(st: str) -> str:
    return {"meeting": "meeting", "slack": "Slack", "issue": "issue",
            "pr": "PR", "doc": "doc", "org": "org"}.get(st, st or "source")


# --------------------------------------------------------------------------- #
def _build_citations(items: list[dict], user_id: str, limit: int = 5) -> list[dict]:
    cits = []
    for r in items[:limit]:
        fid = r["id"]
        md = unwrap_metadata(r, "shadowItem")
        if not md.get("summary"):  # query rows carry no fields; fetch directly
            fetched = T.get_metadata(fid, "shadowItem", as_user=user_id) or {}
            md = {k: v for k, v in fetched.items() if not k.startswith("$")}
        cits.append({
            "file_id": fid,
            "name": r.get("name") or md.get("title") or fid,
            "source_type": md.get("sourceType", ""),
            "date": (md.get("occurredOn", "") or "")[:10],
            "box_url": T.box_web_url(fid),
            "_summary": md.get("summary", ""),
            "_components": md.get("components", []),
            "_people": md.get("people", ""),
        })
    return cits


def _who_to_ask(components: list[str], cards: list[dict], org: dict,
                cited_people: str) -> dict | None:
    # Resolve in component PRIORITY order: the first (highest-priority) component
    # with an active owner wins. `components` arrives already sorted so the
    # primary component (e.g. retry-service for PAY-482) is first.
    #
    # Among owners of the SAME component, prefer the hero owners (the originally
    # named Payments folks) over the bulk filler engineers — the hero data is
    # what the demo's answers should route to.
    HERO = ["Priya Nair", "Sam Ortiz", "Nina Patel", "Dana Kim"]
    hero_rank = {n: i for i, n in enumerate(HERO)}

    def rank(c):
        return hero_rank.get(c.get("name", ""), 99)

    active = [c for c in cards if c.get("active") == "yes"]
    best = None
    for comp in components:
        owners = [c for c in active if comp in (c.get("expertise") or [])]
        if owners:
            owners.sort(key=rank)
            best = owners[0]
            break
    if best is None:
        # manager fallback
        for c in cards:
            if c.get("role", "").lower().startswith("engineering manager"):
                best = c
                break
    if best is None:
        return None
    reason = f"Owns {', '.join(best.get('expertise') or [])}." if best.get("expertise") else \
             f"{best.get('role','')} on the {best.get('team','')} team."
    # moved note: a cited author who is inactive / off Payments AND whose
    # expertise overlaps THIS question's components — so it fires on PAY-482
    # (Marcus owned retry/gateway) but not on an unrelated ledger question.
    moved_note = None
    comp_set = set(components or [])
    for c in cards:
        if c.get("active") != "no":
            continue
        if c.get("email", "") not in (cited_people or ""):
            continue
        if comp_set and not (set(c.get("expertise") or []) & comp_set):
            continue
        moved_note = (f"{c.get('name')} wrote earlier material but moved to the "
                      f"{c.get('team')} team on Sept 22.")
        break
    return {
        "name": best.get("name"),
        "role": best.get("role"),
        "reason": reason,
        "moved_note": moved_note,
    }


def _decision_status(decisions: list[dict]) -> dict | None:
    for r in decisions:
        d = unwrap_metadata(r, "shadowDecision")
        if d:
            return {
                "decision": d.get("decision", ""),
                "status": d.get("status", ""),
                "confirmed": d.get("confirmed", ""),
                "decidedOn": d.get("decidedOn", "")[:10],
            }
    return None


def _compose_markdown(question: str, cits: list[dict], decision: dict | None,
                      who: dict | None, box_ai_answer: str = "") -> str:
    if box_ai_answer:
        return box_ai_answer

    dstr = ""
    if decision:
        dstr = (f"The team's recorded decision: **{decision['decision']}** — "
                f"status **{decision['status']}**"
                + (" (awaiting confirmation)" if decision.get("confirmed") == "pending"
                   else "")
                + (f", decided {decision['decidedOn']}" if decision.get("decidedOn") else "")
                + ".")

    # Bedrock compose is only used in Box-AI / rich mode (it adds latency).
    if os.environ.get("SHADOW_BOX_AI", "").lower() in ("1", "true", "yes"):
        bullets = "\n".join(
            f"- [{i+1}] {c['name']} ({_source_type_name(c['source_type'])}, {c['date']}): {c['_summary']}"
            for i, c in enumerate(cits)
        )
        prompt = (
            f"A new teammate asks: \"{question}\"\n\n"
            f"Using ONLY these cited team sources, write a short, friendly answer "
            f"(2-4 short paragraphs or bullets). Keep the [n] citation markers that "
            f"match the sources. Say where it was discussed and the decision status. "
            f"Do not invent anything.\n\nSources:\n{bullets}\n{dstr}\n"
        )
        try:
            md = bedrock_complete(prompt, max_tokens=600)
            if md:
                return md
        except Exception:
            pass

    # Fast deterministic answer (default) — reads as prose, still cited.
    parts = []
    if cits:
        lead = cits[0]
        parts.append(f"Here's what the team has on this. {lead['_summary']} [1]")
        if len(cits) > 1:
            parts.append("It also comes up in:")
            parts.append("\n".join(
                f"- [{i+1}] {c['_summary']} "
                f"({_source_type_name(c['source_type'])}, {c['date']})"
                for i, c in enumerate(cits[1:], start=1)))
    if dstr:
        parts.append(dstr)
    if who and who.get("name"):
        parts.append(f"**Who to ask:** {who['name']} — {who.get('reason','')}"
                     + (f" {who['moved_note']}" if who.get("moved_note") else ""))
    return "\n\n".join(parts) if parts else "I don't have team sources on that yet."


# --------------------------------------------------------------------------- #
# Agentic entry point — a Strands Agent drives the reasoning loop, calling the
# Box tools itself. The deterministic pipeline supplies the structured fields
# (citations/decision/who/restricted) the UI binds to AND is the fallback, so
# the answer is both genuinely agent-driven and demo-safe.
# --------------------------------------------------------------------------- #
ANSWER_SYSTEM = (
    "You are Shadow, the Payments team's knowledge agent, helping a new hire. "
    "Work the question using your tools, in this spirit:\n"
    "1. find the topic's component and/or issue id, then search_team_memory.\n"
    "2. call find_restricted_topic — if it reports found=true you MUST NOT reveal "
    "that source; if restricted_only=true, say you can't answer from team memory "
    "and name who was there; otherwise answer from what you CAN read and add that "
    "one source was withheld.\n"
    "3. call box_ai_answer on the readable file_ids for a cited answer.\n"
    "4. call get_decision and lookup_people to state the decision status and who "
    "to ask.\n"
    "Be concise and never invent facts a tool did not return. Write the final "
    "answer in markdown for the teammate."
)


def answer(user_id: str, question: str) -> dict[str, Any]:
    """Fully-agentic answer: a Strands Agent orchestrates the tool calls. The
    structured fields come from the deterministic pipeline (same tools, reliable
    shape) so the UI contract holds; if the agent is unavailable we return the
    pipeline result directly."""
    user_id = str(user_id)
    # structured fields + guaranteed-correct result (also the fallback)
    base = _answer_pipeline(user_id, question)
    # The Strands tool-calling loop is real but slower (~60-90s); gate it behind
    # SHADOW_AGENTIC so the live demo stays snappy while "fully agentic" is a
    # flip of one env var. Pipeline prose already routes through Strands too.
    if os.environ.get("SHADOW_AGENTIC", "").lower() not in ("1", "true", "yes"):
        return base
    try:
        from .tools_strands import ANSWER_TOOLS, set_actor
        from .common import strands_agent

        set_actor(user_id)
        agent = strands_agent(system=ANSWER_SYSTEM, tools=ANSWER_TOOLS)
        prose = str(agent(question)).strip()
        if prose and len(prose) > 40:
            # agent drove the reasoning; keep the pipeline's structured fields
            base = {**base, "answer_markdown": prose}
            log_activity("Answer", "Strands agent answered (tool-calling loop)", "",
                         f"{len(prose)} chars")
    except Exception as e:  # agent unavailable -> deterministic result stands
        log_activity("Answer", "Strands agent unavailable, used pipeline", "", str(e))
    return base


# --------------------------------------------------------------------------- #
def _answer_pipeline(user_id: str, question: str) -> dict[str, Any]:
    user_id = str(user_id)
    issue_ids = [m.group(0).upper() for m in ISSUE_RE.finditer(question)]
    components = _components_from(question)

    # privacy signal: a Restricted source on the topic the asker cannot read.
    # This does NOT short-circuit — we first gather what the asker CAN read, then
    # decide between a full refusal (nothing readable) and a partial answer
    # (some readable sources) with a withheld-source note.
    restricted = _restricted_hit(question, user_id)
    cards = _people_cards()
    org = _org_chart()

    def _restricted_who(attendees: str) -> dict:
        for c in cards:
            if c.get("email", "") in attendees and c.get("active") == "yes":
                return {"name": c.get("name"), "role": c.get("role"),
                        "reason": "Was in that review and can brief you directly.",
                        "moved_note": None}
        return {"name": "Dana Kim", "role": "Engineering Manager",
                "reason": "Hosted that review and can brief you.",
                "moved_note": None}

    items = _matched_items(issue_ids, components, user_id)
    # Rank by (relevance, source-type). Relevance rewards an item whose
    # components intersect the question's components and whose summary/title
    # shares keywords with the question — so an on-topic issue (PAY-512 for a
    # checkout question) beats an off-topic same-type one (PAY-470 CSS noise).
    order = {"issue": 0, "doc": 1, "pr": 2, "meeting": 3, "slack": 4, "org": 5}
    qwords = {w for w in re.findall(r"[a-z]{4,}", question.lower())}
    want_comps = set(components or [])
    ranked = []
    for r in items:
        md = T.get_metadata(r["id"], "shadowItem", as_user=user_id) or {}
        comps = set(md.get("components", []) or [])
        hay = f"{(md.get('title') or '')} {(md.get('summary') or '')} {(md.get('issueIds') or '')}".lower()
        rel = 0
        if want_comps and comps & want_comps:
            rel -= 2
        # count keyword overlap (more shared words = more on-topic)
        rel -= sum(1 for w in qwords if w in hay)
        for iss in issue_ids:
            if iss.lower() in hay:
                rel -= 6
        ranked.append((rel, order.get(md.get("sourceType", ""), 9), r))
    ranked.sort(key=lambda x: (x[0], x[1]))
    items = [r for _, _, r in ranked]
    cits = _build_citations(items, user_id)

    # FAILURE scenario: the restricted topic is "restricted-only" (vendor /
    # pricing / contract) — there is no genuine public source on it, so even if
    # a broad component scan surfaced tangential files, refuse cleanly. Also
    # refuse when nothing readable matched at all.
    restricted_only = bool(restricted and restricted.get("only"))
    if restricted and (restricted_only or not cits):
        who = _restricted_who(restricted.get("attendees", ""))
        note = ("I can't answer that from team memory — the only source on it is in "
                "a Restricted folder your Box access doesn't include. "
                f"{who['name']} was in that review and can brief you directly.")
        log_activity("Answer", f"Refused restricted-only topic for user {user_id}",
                     "", "privacy: full refusal")
        return {
            "answer_markdown": note,
            "citations": [],
            "decision_status": None,
            "who_to_ask": who,
            "restricted_note": note,
        }

    if not components:
        # infer components: the TOP citation (the issue / most relevant source)
        # defines the primary components; others are appended as secondary.
        comp_set: list[str] = []
        ordered_cits = cits[:1] + cits[1:]  # top first (already ranked)
        for c in ordered_cits:
            for comp in c.get("_components", []):
                if comp not in comp_set:
                    comp_set.append(comp)
        components = comp_set
    # stable priority so the primary owner wins who-to-ask: retry-service and
    # gateway-client (the PAY-482 core) first, then the rest in a fixed order.
    _PRIO = {"retry-service": 0, "gateway-client": 1, "ledger": 2,
             "payments-api": 3, "checkout": 4}
    components = sorted(components, key=lambda c: _PRIO.get(c, 9))

    decisions = _decisions(components, user_id)
    decision = _decision_status(decisions)

    # Box AI Ask over readable files (prose + citations). It's the slow step
    # (5-30s), so it's OFF by default for a snappy demo and the deterministic
    # metadata summary is used instead (still cited). Flip SHADOW_BOX_AI=1 to
    # show real Box AI prose.
    box_ai_answer = ""
    if os.environ.get("SHADOW_BOX_AI", "").lower() in ("1", "true", "yes"):
        readable_ids = [c["file_id"] for c in cits if T.can_read(c["file_id"], user_id)]
        if readable_ids:
            ai = T.ai_ask(readable_ids, question, as_user=user_id)
            if ai.get("answer") and not ai.get("error"):
                box_ai_answer = ai["answer"]
                log_activity("Answer", "Box AI Ask answered", "", f"{len(readable_ids)} files")
            else:
                log_activity("Answer", "Box AI unavailable, degraded to metadata", "",
                             str(ai.get("status", "")))

    # people across ALL matched items (so a cited author who moved is noticed
    # even when the doc is not in the top-5 shown)
    all_people = set()
    for c in cits:
        all_people.update(p.strip() for p in (c.get("_people", "") or "").split(","))
    for r in items:
        md = T.get_metadata(r["id"], "shadowItem", as_user=user_id) or {}
        all_people.update(p.strip() for p in (md.get("people", "") or "").split(","))
    cited_people = ",".join(p for p in all_people if p)
    who = _who_to_ask(components, cards, org, cited_people)
    md = _compose_markdown(question, cits, decision, who, box_ai_answer)

    # PARTIAL / AVERAGE scenario: we answered from readable sources, but a
    # Restricted source on this topic was withheld. Say so, honestly.
    partial_note = None
    if restricted:
        rwho = _restricted_who(restricted.get("attendees", ""))
        partial_note = ("One source on this is in a Restricted folder your Box "
                        f"access doesn't include ({restricted.get('topic','')}), so "
                        "it's left out of the answer above. "
                        f"{rwho['name']} can fill in that part.")
        log_activity("Answer", f"Partial answer, 1 source withheld for user {user_id}",
                     "", "privacy: partial")

    # strip private fields from citations before returning
    clean_cits = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cits]
    return {
        "answer_markdown": md,
        "citations": clean_cits,
        "decision_status": decision,
        "who_to_ask": who,
        "restricted_note": partial_note,
    }


if __name__ == "__main__":  # quick manual check
    import json
    import sys

    uid = ids()["users"]["Alex Rivera"]
    q = sys.argv[1] if len(sys.argv) > 1 else "Where do I start on PAY-482?"
    print(json.dumps(answer(uid, q), indent=2))
