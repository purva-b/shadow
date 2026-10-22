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

from . import acl, embeddings

ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


@dataclass
class Retrieved:
    readable: list[SourceRecord]
    withheld: list[SourceRecord]


def _keyword_score(record: SourceRecord, qwords: set[str], issue_ids: set[str],
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


def _semantic_score(qvec, record: SourceRecord) -> float:
    """Cosine similarity between the question and the record's title+body.
    0.0 when embeddings are unavailable, so the keyword lane carries it."""
    if qvec is None:
        return 0.0
    rvec = embeddings.embed(f"{record.title}\n{record.body}")
    return embeddings.cosine(qvec, rvec) if rvec else 0.0


def recall(question: str, principal: str, store, context: list | None = None,
           limit: int = 5, sources: list[str] | None = None) -> Retrieved:
    """Recall + ACL-filter records for `question` asked by `principal`.

    Ranking blends an exact-match boost (issue-id, component) with SEMANTIC
    similarity (Titan embeddings). Exact issue-id still dominates; semantic
    similarity replaces the brittle keyword count so 'gateway vendor pricing'
    ranks the pricing PR above a checkout PR that merely contains 'gateway'.
    Falls back to pure keyword scoring when embeddings are unavailable.
    """
    text = question + " " + " ".join(str(c) for c in (context or []))
    issue_ids = {m.group(0).upper() for m in ISSUE_RE.finditer(text)}
    qwords = set(re.findall(r"[a-z]{4,}", text.lower()))
    components = set(qwords)

    qvec = embeddings.embed(text) if embeddings.available() else None

    # Candidate selection. When the backend can rank vectors itself (pgvector),
    # push nearest-neighbour recall into the engine — Postgres returns the top-N
    # by cosine distance instead of us scanning every record in Python. We still
    # re-score in Python below to apply the exact issue-id / component boosts and
    # the relevance floor, but only over the small candidate set the DB returned.
    # When the backend cannot (jsonl, the demo default) OR embeddings are
    # unavailable, fall back to the full scan.
    if qvec is not None and store.supports_vector():
        # over-recall from the DB so the Python re-rank + ACL trim have headroom.
        candidates = store.vector_recall(qvec, k=limit * 6, sources=sources)
    else:
        candidates = store.all()
        if sources:
            candidates = [r for r in candidates if r.source in sources]

    # Relevance floors so an OFF-TOPIC question returns nothing rather than the
    # least-irrelevant record. Semantic cosine is never exactly 0, so a bare
    # ">0" gate would admit everything; require a real similarity / keyword hit.
    SEM_FLOOR = 0.35      # cosine below this is "not about the same thing"
    scored = []
    for r in candidates:
        kw = _keyword_score(r, qwords, issue_ids, components)
        sem = _semantic_score(qvec, r)
        # a record qualifies only if it has an exact/keyword hit OR clears the
        # semantic floor; otherwise it is not on-topic at all.
        relevant = kw > 0 or sem >= SEM_FLOOR
        if not relevant:
            continue
        # exact issue-id boost (kw>=50) must win; otherwise semantic leads,
        # scaled to the same order of magnitude as the component/keyword lane.
        blended = kw + (sem if sem >= SEM_FLOOR else 0.0) * 20.0
        if blended > 0:
            scored.append((blended, r))
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
