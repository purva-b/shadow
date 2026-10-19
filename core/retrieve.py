"""Retrieval over connector records — recall + permission filter.

Reads normalized records from the store, recalls the ones relevant to a
question (exact issue-id and component match, plus keyword overlap on
title/body), ranks them, then applies the source-agnostic ACL filter so the
asker only sees what they may read. Returns both the readable hits and the
withheld set (for the 'one source was withheld' note and the trust panel).

This is the keyword/exact lane; semantic (embedding) recall is the drop-in
upgrade behind the same signature.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from connectors.base import SourceRecord

from . import acl

ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


@dataclass
class Retrieved:
    readable: list[SourceRecord]
    withheld: list[SourceRecord]


def _score(record: SourceRecord, qwords: set[str], issue_ids: set[str],
           components: set[str]) -> int:
    """Higher = more relevant. Exact issue-id match dominates, then component
    overlap, then keyword overlap on title+body."""
    s = 0
    rec_issues = {i.upper() for i in record.issue_ids}
    if issue_ids & rec_issues:
        s += 50
    if components & set(record.components):
        s += 10
    hay = f"{record.title} {record.body}".lower()
    s += sum(1 for w in qwords if w in hay)
    return s


def recall(question: str, principal: str, store, context: list | None = None,
           limit: int = 5, sources: list[str] | None = None) -> Retrieved:
    """Recall + ACL-filter records for `question` asked by `principal`."""
    text = question + " " + " ".join(str(c) for c in (context or []))
    issue_ids = {m.group(0).upper() for m in ISSUE_RE.finditer(text)}
    qwords = {w for w in re.findall(r"[a-z]{4,}", text.lower())}
    components = {w for w in qwords}   # component tags are plain words; intersect in _score

    candidates = store.all()
    if sources:
        candidates = [r for r in candidates if r.source in sources]

    scored = [(_score(r, qwords, issue_ids, components), r) for r in candidates]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [r for _, r in scored[: limit * 3]]   # over-recall, ACL will trim

    readable = acl.readable(top, principal)[:limit]
    withheld = acl.withheld(top, principal)
    return Retrieved(readable=readable, withheld=withheld)


def to_citation(record: SourceRecord, store) -> dict:
    """Project a record into the UI citation shape (same keys as the Box path)."""
    item = store.item_for(record)
    return {
        "file_id": f"{record.source}:{record.external_id}",
        "name": record.title,
        "source_type": record.source,            # "github", "slack", ...
        "date": record.occurred_on,
        "box_url": record.url,                   # UI field name; holds any deep link
        "summary": item.get("summary", "") or (record.body or "")[:200],
    }
