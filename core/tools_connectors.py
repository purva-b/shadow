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
import threading

from strands import tool

import connectors
from . import acl, retrieve
from .store import store as _store

_local = threading.local()


def set_principal(principal: str) -> None:
    _local.principal = str(principal)


def _principal() -> str:
    return getattr(_local, "principal", "")


def _load(name: str, **kw):
    try:
        return connectors.load(name, **kw)
    except Exception:
        return None


def _dump(records) -> str:
    me = _principal()
    readable = acl.readable(records, me)
    withheld = acl.withheld(records, me)
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
    got = retrieve.recall(query, _principal(), _store, limit=6)
    return json.dumps({
        "readable": [{"source": r.source, "id": r.external_id, "title": r.title,
                      "summary": (r.body or "")[:240], "url": r.url}
                     for r in got.readable],
        "withheld_count": len(got.withheld),
    })


@tool
def github_search(query: str) -> str:
    """Search GitHub DIRECTLY with its own issue/PR search (better than the index
    for code, PRs, and issue state). Use when the question is about a PR, an
    issue, a code change, or a merge. Returns the same JSON shape as
    semantic_search, ACL-filtered for the asker."""
    conn = _load("github")
    if conn is None or not hasattr(conn, "search"):
        return json.dumps({"readable": [], "withheld_count": 0})
    return _dump(conn.search(query))


@tool
def slack_search(query: str) -> str:
    """Search Slack DIRECTLY with search.messages (better than the index for
    'what did the team say', recent discussion, who raised something). Needs a
    Slack user token with search:read. Returns the same JSON shape, ACL-filtered."""
    conn = _load("slack")
    if conn is None or not hasattr(conn, "search"):
        return json.dumps({"readable": [], "withheld_count": 0})
    return _dump(conn.search(query))


@tool
def confluence_search(query: str) -> str:
    """Search Confluence DIRECTLY with CQL (better than the index for wiki pages,
    specs, RFCs, runbooks). Use for 'where's the doc on X' / 'what's our policy
    for Y'. Returns the same JSON shape, ACL-filtered."""
    conn = _load("confluence")
    if conn is None or not hasattr(conn, "search"):
        return json.dumps({"readable": [], "withheld_count": 0})
    return _dump(conn.search(query))


# the toolset an agent gets to orchestrate across sources
CONNECTOR_TOOLS = [semantic_search, github_search, slack_search, confluence_search]
