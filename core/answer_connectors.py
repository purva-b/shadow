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
        "decision_status": None,
        "who_to_ask": None,
        "restricted_note": partial_note,
    }
