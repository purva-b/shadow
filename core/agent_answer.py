"""Agentic cross-source answer.

A Strands Agent decides which source to search for a given question — GitHub's
own search for a PR, Slack's search for a discussion, Confluence's CQL for a
spec, or the unified semantic index for a general ask — calls those tools, loops
as needed, and composes a cited answer. This is the "make the whole system
agentic" path: the agent orchestrates, Python no longer sequences the sources.

Permissions are preserved because every tool returns ACL-filtered records for
the asker. The deterministic connector answer (core.answer_connectors) remains
the fallback and the structured-field source (who-to-ask / decision), so the UI
contract holds and the answer never hard-fails.

Gated behind SHADOW_AGENTIC (the loop is slower); the fast path stays default.
"""
from __future__ import annotations

import os

AGENT_SYSTEM = (
    "You are Shadow, a team's knowledge agent. Answer the user's question using "
    "your search tools. Choose the RIGHT tool for the question:\n"
    "- semantic_search: a general question, or when unsure — searches every "
    "synced source at once by meaning.\n"
    "- github_search: PRs, issues, code changes, merge/review state.\n"
    "- slack_search: what the team said, recent discussion, who raised something.\n"
    "- confluence_search: wiki pages, specs, RFCs, runbooks, policies.\n"
    "Call ONE tool first; call a second ONLY if the first clearly missed the "
    "source the question needs, and never the same tool twice. Every tool "
    "returns only what the asker is allowed to read, plus a withheld_count — "
    "if results are empty but withheld_count>0, say a source exists but their "
    "access doesn't include it (never reveal it). Write a concise, cited answer "
    "from what the tools returned; never invent facts a tool did not return. If "
    "nothing relevant comes back, say so plainly."
)


def is_enabled() -> bool:
    """Agentic is the DEFAULT now. Set SHADOW_AGENTIC=0 (or false/no) to force
    the fast deterministic path."""
    return os.environ.get("SHADOW_AGENTIC", "1").lower() not in ("0", "false", "no")


def answer_agentic(principal: str, question: str, context=None) -> dict | None:
    """Agent-orchestrated answer. Returns a UI answer dict, or None to let the
    caller fall back to the deterministic path.

    Latency: the deterministic base (structured fields: who-to-ask / decision)
    and the agent loop (prose) are INDEPENDENT, so they run concurrently —
    total ≈ max(base, loop) instead of base + loop.
    """
    import concurrent.futures as _fut

    try:
        from agents.common import strands_agent

        from . import tools_connectors
        from .answer_connectors import answer_from_connectors

        q = question
        if context:
            q = f"{question}\n\n(conversation so far: {' | '.join(map(str, context))})"

        def _base():
            return answer_from_connectors(principal, question, context=context)

        def _loop():
            # bind the principal inside THIS thread (tools_connectors uses a
            # thread-local actor)
            tools_connectors.set_principal(principal)
            agent = strands_agent(system=AGENT_SYSTEM,
                                  tools=tools_connectors.CONNECTOR_TOOLS)
            prose = str(agent(q)).strip()
            return prose, tools_connectors.get_trace()

        with _fut.ThreadPoolExecutor(max_workers=2) as ex:
            f_base = ex.submit(_base)
            f_loop = ex.submit(_loop)
            base = f_base.result()
            prose, trace = f_loop.result()

        if prose and len(prose) > 40:
            if base is None:
                base = {"answer_markdown": prose, "citations": [],
                        "decision_status": None, "who_to_ask": None,
                        "restricted_note": None, "agent_trace": trace}
            else:
                # agent drove the reasoning + prose; keep the deterministic
                # structured fields (citations, who-to-ask, decision, withheld)
                base = {**base, "answer_markdown": prose, "agent_trace": trace}
        return base
    except Exception:
        # any agent failure -> let the caller use the deterministic path
        return None
