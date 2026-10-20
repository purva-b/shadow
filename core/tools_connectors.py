"""Strands @tools over each platform's NATIVE search + the unified index.

These let an agent reach each source the way that source is best searched:
GitHub's own issue/code search, Slack's search.messages, Confluence's CQL — plus
our cross-source semantic index. The agent decides which to call for a given
question. Every tool returns records already ACL-filtered for the current asker,
so permissions hold no matter which tool the agent picks.

The asker (canonical principal) is bound per-request via set_principal().
"""
from __future__ import annotations

import json

from strands import tool

import connectors
from . import acl, retrieve
from .store import store as _store

_PRINCIPAL = {"v": ""}


# Strands may run a tool on its own worker thread, so the trace buffer is a
# process-global list (reset per request by set_principal), not thread-local.
# One request at a time per process is the model here (the agentic path is
# serialized), so a shared buffer is correct and simplest.
_TRACE: list = []
# per-request dedupe: (tool, query) -> prior JSON result, so the agent asking the
# same tool the same thing twice costs no second call (saves an LLM round-trip).
_SEEN: dict = {}


def set_principal(principal: str) -> None:
    _PRINCIPAL["v"] = str(principal)
    _TRACE.clear()
    _SEEN.clear()


def _cached(tool_name: str, query: str):
    return _SEEN.get((tool_name, query.strip().lower()))


def _cache(tool_name: str, query: str, result: str) -> str:
    _SEEN[(tool_name, query.strip().lower())] = result
    return result


def _principal() -> str:
    return _PRINCIPAL["v"]


def _record(tool_name: str, query: str, n_readable: int, n_withheld: int) -> None:
    """Record one tool invocation so the UI can show WHAT the agent searched."""
    _TRACE.append({"tool": tool_name, "query": query,
                   "found": n_readable, "withheld": n_withheld})


def get_trace() -> list:
    """Return the tool-call trace recorded since the last set_principal()."""
    return list(_TRACE)


def _load(name: str, **kw):
    try:
        return connectors.load(name, **kw)
    except Exception:
        return None


def _dump(records, tool_name: str = "", query: str = "") -> str:
    me = _principal()
    readable = acl.readable(records, me)
    withheld = acl.withheld(records, me)
    if tool_name:
        _record(tool_name, query, len(readable), len(withheld))
    return json.dumps({
        "readable": [{"source": r.source, "id": r.external_id, "title": r.title,
                      "summary": (r.body or "")[:240], "url": r.url}
                     for r in readable[:8]],
        "withheld_count": len(withheld),
    })


@tool
def semantic_search(query: str) -> str:
    """Search the UNIFIED cross-source index (every synced source at once) by
    MEANING. Best first call for a general question — it already blends Box,
    GitHub and any other synced source and ranks by relevance. Returns JSON
    {readable:[{source,id,title,summary,url}], withheld_count}. Only records the
    current asker may read appear in `readable`."""
    hit = _cached("semantic_search", query)
    if hit is not None:
        _record("semantic_search", query + " (cached)", -1, -1)
        return hit
    got = retrieve.recall(query, _principal(), _store, limit=6)
    _record("semantic_search", query, len(got.readable), len(got.withheld))
    return _cache("semantic_search", query, json.dumps({
        "readable": [{"source": r.source, "id": r.external_id, "title": r.title,
                      "summary": (r.body or "")[:240], "url": r.url}
                     for r in got.readable],
        "withheld_count": len(got.withheld),
    }))


@tool
def github_search(query: str) -> str:
    """Search GitHub DIRECTLY with its own issue/PR search (better than the index
    for code, PRs, and issue state). Use when the question is about a PR, an
    issue, a code change, or a merge. Returns the same JSON shape as
    semantic_search, ACL-filtered for the asker."""
    hit = _cached("github_search", query)
    if hit is not None:
        _record("github_search", query + " (cached)", -1, -1)
        return hit
    conn = _load("github")
    if conn is None or not hasattr(conn, "search"):
        return json.dumps({"readable": [], "withheld_count": 0})
    return _cache("github_search", query, _dump(conn.search(query), "github_search", query))


@tool
def slack_search(query: str) -> str:
    """Search Slack DIRECTLY with search.messages (better than the index for
    'what did the team say', recent discussion, who raised something). Needs a
    Slack user token with search:read. Returns the same JSON shape, ACL-filtered."""
    hit = _cached("slack_search", query)
    if hit is not None:
        _record("slack_search", query + " (cached)", -1, -1)
        return hit
    conn = _load("slack")
    if conn is None or not hasattr(conn, "search"):
        return json.dumps({"readable": [], "withheld_count": 0})
    return _cache("slack_search", query, _dump(conn.search(query), "slack_search", query))


@tool
def confluence_search(query: str) -> str:
    """Search Confluence DIRECTLY with CQL (better than the index for wiki pages,
    specs, RFCs, runbooks). Use for 'where's the doc on X' / 'what's our policy
    for Y'. Returns the same JSON shape, ACL-filtered."""
    hit = _cached("confluence_search", query)
    if hit is not None:
        _record("confluence_search", query + " (cached)", -1, -1)
        return hit
    conn = _load("confluence")
    if conn is None or not hasattr(conn, "search"):
        return json.dumps({"readable": [], "withheld_count": 0})
    return _cache("confluence_search", query, _dump(conn.search(query), "confluence_search", query))


# the toolset an agent gets to orchestrate across sources
CONNECTOR_TOOLS = [semantic_search, github_search, slack_search, confluence_search]
