"""Shadow connector plugin interface.

Every source — Slack, Confluence, GitHub, Calendar, Box — implements the
`Connector` protocol and emits normalized `SourceRecord`s. Ingest, retrieval,
permission filtering, and the answer agent all operate on `SourceRecord`, so
they never need to know which system a record came from. Adding a connector is
a self-contained plugin, not a change to the core.

See ARCHITECTURE.md for the full design.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

Visibility = Literal["public", "internal", "restricted"]
Kind = Literal["meeting", "chat", "issue", "pr", "doc", "event", "org"]


@dataclass
class Acl:
    """Who may read a record, normalized across source systems.

    `allow`/`deny` hold canonical principals (resolved via the identity map),
    not a single system's native handles. `visibility=restricted` plus an empty
    `allow` means 'nobody but explicit collaborators' — the privacy case.
    """
    visibility: Visibility = "internal"
    allow: list[str] = field(default_factory=list)   # canonical principals
    deny: list[str] = field(default_factory=list)

    def admits(self, principal: str) -> bool:
        """True iff this principal may read the record."""
        if principal in self.deny:
            return False
        if self.visibility in ("public", "internal") and not self.allow:
            return True
        return principal in self.allow


@dataclass
class SourceRecord:
    """The one shape the whole core speaks. A connector's `fetch` returns this;
    everything downstream (ingest, retrieval, answer) consumes it."""
    source: str                       # "slack" | "github" | "box" | ...
    external_id: str                  # stable id in the origin system
    kind: Kind
    title: str
    body: str                         # plain text, already rendered/OCR'd
    occurred_on: str = ""             # ISO date (YYYY-MM-DD)
    people: list[str] = field(default_factory=list)      # canonical principals
    components: list[str] = field(default_factory=list)  # workspace-defined tags
    issue_ids: list[str] = field(default_factory=list)
    acl: Acl = field(default_factory=Acl)
    url: str = ""                     # deep link back to the origin

    def to_shadow_item(self) -> dict:
        """Project onto the existing shadowItem metadata shape, so the current
        Box-backed storage keeps working while connectors are added."""
        return {
            "sourceType": {"chat": "slack", "event": "meeting"}.get(self.kind, self.kind),
            "title": self.title,
            "occurredOn": self.occurred_on,
            "components": self.components,
            "issueIds": ",".join(self.issue_ids),
            "people": ",".join(self.people),
            "summary": "",            # filled by the Librarian during ingest
            "sensitivity": "restricted" if self.acl.visibility == "restricted" else "normal",
        }


@dataclass
class SourceRef:
    """A lightweight pointer `discover` yields and `fetch` resolves."""
    source: str
    external_id: str
    hint: dict = field(default_factory=dict)   # e.g. channel id, repo, page id


@dataclass
class WebhookSpec:
    """How a connector wants to be pushed to. Returned by `subscribe`."""
    path: str                         # "/api/webhook/<connector>"
    verify_header: str = ""           # signature header name, if any
    secret_env: str = ""              # env var holding the shared secret


@runtime_checkable
class Connector(Protocol):
    """Implement this to add a source. See box_connector.py for a worked example."""
    name: str

    def discover(self, since: datetime | None = None) -> Iterable[SourceRef]:
        """List items new or changed since a watermark (polling mode)."""
        ...

    def fetch(self, ref: SourceRef) -> SourceRecord:
        """Pull one item and normalize it."""
        ...

    def resolve_acl(self, ref: SourceRef) -> Acl:
        """Map the origin's native permissions onto the normalized Acl."""
        ...

    def subscribe(self) -> WebhookSpec | None:
        """Optional push registration; return None for poll-only connectors."""
        ...


@runtime_checkable
class Searchable(Protocol):
    """Optional capability: a connector that can use its source's OWN search API
    (GitHub issue search, Slack search.messages, Confluence CQL). Not every
    connector implements it (Box does not), so it is a SEPARATE protocol — the
    base Connector contract stays satisfiable without a native search. Callers
    check isinstance(conn, Searchable) before calling .search()."""

    def search(self, query: str, limit: int = 8) -> list[SourceRecord]:
        ...
