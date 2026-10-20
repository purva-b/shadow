"""Answer from connector records — the cross-source answer path.

Given a question asked by a principal, recall readable records from the store
and compose the same JSON shape the UI already binds to (answer_markdown,
citations[], decision_status, who_to_ask, restricted_note). The Box pipeline is
unchanged; this handles questions whose evidence lives in connector sources
(GitHub, Slack, ...). The server tries connector records first and falls back
to the Box pipeline when nothing relevant is found here.
"""
from __future__ import annotations

from . import retrieve
from .store import store as _default_store


def _components_from_records(records) -> list[str]:
    """Union of components across the recalled records, in a stable priority
    order so who-to-ask resolves the primary owner (same order the Box pipeline
    uses — both read config/workspace.yaml taxonomy)."""
    from . import taxonomy

    comps: list[str] = []
    for r in records:
        for c in r.components:
            if c not in comps:
                comps.append(c)
    return taxonomy.sort_by_priority(comps)


def _enrich_structured(readable, principal):
    """Compute who_to_ask + decision_status from the SHARED Box-backed helpers,
    so a cross-source answer keeps the same structured fields the Box pipeline
    produces (fixes the regression where routing Box through the store dropped
    who-to-ask and the decision chip). Degrades to None if Box is unreachable."""
    who = decision = None
    try:
        from agents import answer as box_answer
        from agents.common import ids

        components = _components_from_records(readable)
        # map the canonical principal back to a Box App User id for perm-scoped reads
        users = ids().get("users", {})
        local = principal.split("@")[0].lower()
        uid = next((u for n, u in users.items() if n.split()[0].lower() == local), None)
        if uid is None:
            return None, None
        cards = box_answer._people_cards()
        org = box_answer._org_chart()
        decisions = box_answer._decisions(components, str(uid))
        decision = box_answer._decision_status(decisions)
        cited_people = ",".join(p for r in readable for p in r.people)
        who = box_answer._who_to_ask(components, cards, org, cited_people)
    except Exception:
        pass
    return who, decision


def answer_from_connectors(principal: str, question: str, context=None,
                           store=None, sources=None) -> dict | None:
    """Return a UI answer dict, or None if no connector record is relevant
    (so the caller falls back to the Box pipeline)."""
    store = store or _default_store
    got = retrieve.recall(question, principal, store, context=context,
                          sources=sources)
    if not got.readable and not got.withheld:
        return None                       # nothing here -> let Box pipeline try

    # FAILURE / PARTIAL: relevant records exist but the asker can read none
    if not got.readable:
        note = ("I can't answer that from team memory — the matching source is in "
                "a restricted space your access doesn't include.")
        return {
            "answer_markdown": note, "citations": [],
            "decision_status": None, "who_to_ask": None, "restricted_note": note,
        }

    cits = [retrieve.to_citation(r, store) for r in got.readable]
    lead = cits[0]
    parts = [f"Here's what the team's sources have on this. {lead['summary']} [1]"]
    if len(cits) > 1:
        parts.append("It also comes up in:")
        parts.append("\n".join(
            f"- [{i+1}] {c['summary']} ({c['source_type']}, {c['date']})"
            for i, c in enumerate(cits[1:], start=1)))

    # UNIFIED: regain who-to-ask + decision status (the Box pipeline's features)
    who, decision = _enrich_structured(got.readable, principal)
    if who and who.get("name"):
        parts.append(f"**Who to ask:** {who['name']} — {who.get('reason','')}"
                     + (f" {who['moved_note']}" if who.get("moved_note") else ""))

    partial_note = None
    if got.withheld:
        partial_note = (f"{len(got.withheld)} source(s) on this are in a restricted "
                        "space your access doesn't include, so they're left out.")
        parts.append(partial_note)

    # strip the private 'summary' helper before returning citations to the UI
    clean = [{k: v for k, v in c.items() if k != "summary"} for c in cits]
    return {
        "answer_markdown": "\n\n".join(parts),
        "citations": clean,
        "decision_status": decision,
        "who_to_ask": who,
        "restricted_note": partial_note,
    }
