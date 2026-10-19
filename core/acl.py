"""Permission filter — the privacy promise, enforced across every source.

Retrieval recalls candidate records; `readable()` drops any the asker may not
read. The decision uses the normalized `Acl` on each record, so it works
identically for Slack, GitHub, Confluence, Calendar and Box.

Two layers:
  1. the record's normalized Acl (allow/deny/visibility), resolved to canonical
     principals via core.identity — the fast, source-agnostic check.
  2. an optional per-source live probe (e.g. Box `can_read`, which reflects live
     collaborations) for `visibility=restricted` records, where membership is
     authoritative and may have changed since ingest.
"""
from __future__ import annotations

from typing import Callable, Iterable

from connectors.base import Acl, SourceRecord

from . import identity

# a connector can register a live permission probe for its source, keyed by
# source name: probe(external_id, principal) -> bool. Used only to confirm a
# `restricted` record the static Acl did not already admit.
_LIVE_PROBES: dict[str, Callable[[str, str], bool]] = {}


def register_probe(source: str, probe: Callable[[str, str], bool]) -> None:
    _LIVE_PROBES[source] = probe


def can_read(record: SourceRecord, principal: str) -> bool:
    """True iff `principal` (any handle; resolved to canonical) may read this
    record."""
    me = identity.resolve(principal)
    acl = record.acl or Acl()

    # normalize the record's allow/deny to canonical principals once
    allow = set(identity.resolve_all(acl.allow))
    deny = set(identity.resolve_all(acl.deny))
    if me in deny:
        return False
    if acl.visibility in ("public", "internal") and not allow:
        return True
    if me in allow:
        return True

    # restricted (or allow-listed) and not statically admitted -> try a live
    # per-source probe if the connector registered one (Box can_read).
    probe = _LIVE_PROBES.get(record.source)
    if probe is not None:
        try:
            return bool(probe(record.external_id, principal))
        except Exception:
            return False
    return False


def readable(records: Iterable[SourceRecord], principal: str) -> list[SourceRecord]:
    """Filter a candidate set down to what `principal` may read."""
    return [r for r in records if can_read(r, principal)]


def withheld(records: Iterable[SourceRecord], principal: str) -> list[SourceRecord]:
    """The complement — records on-topic but NOT readable. Powers the
    'one source was withheld' note and the trust panel."""
    return [r for r in records if not can_read(r, principal)]
