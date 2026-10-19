"""Confluence connector — docs/wiki as a source.

Normalizes Confluence pages into SourceRecords. Permission maps from page/space
restrictions: a page with read restrictions becomes `restricted` with the
restricted users/groups as the allow-list; otherwise `internal`.

Config (env, never committed):
  CONFLUENCE_BASE_URL   https://<site>.atlassian.net/wiki
  CONFLUENCE_EMAIL      the Atlassian account email
  CONFLUENCE_API_TOKEN  an API token (id.atlassian.com)
  CONFLUENCE_SPACE      optional space key to scope the sync

Degrades gracefully: with no base url/token, discover returns nothing.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Iterable

import requests

from .base import Acl, Connector, SourceRecord, SourceRef, WebhookSpec
from .registry import register

ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Confluence returns 'storage format' HTML; strip to plain text."""
    text = _TAG_RE.sub(" ", html or "")
    return re.sub(r"\s+", " ", text).strip()


@register
class ConfluenceConnector:
    name = "confluence"

    def __init__(self, base_url: str | None = None, email: str | None = None,
                 token: str | None = None, space: str | None = None, **_):
        self.base = (base_url or os.environ.get("CONFLUENCE_BASE_URL", "")).rstrip("/")
        self.email = email or os.environ.get("CONFLUENCE_EMAIL", "")
        self.token = token or os.environ.get("CONFLUENCE_API_TOKEN", "")
        self.space = space or os.environ.get("CONFLUENCE_SPACE", "")

    # -- http --------------------------------------------------------------- #
    def _get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(f"{self.base}{path}", params=params or {},
                         auth=(self.email, self.token),
                         headers={"Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        return r.json()

    # -- discovery (poll) --------------------------------------------------- #
    def discover(self, since: datetime | None = None) -> Iterable[SourceRef]:
        if not (self.base and self.token):
            return
        params = {"limit": 50, "expand": "version"}
        if self.space:
            params["spaceKey"] = self.space
        try:
            data = self._get("/rest/api/content", params)
        except Exception:
            return
        for pg in data.get("results", []):
            yield SourceRef(source=self.name, external_id=str(pg["id"]),
                            hint={"title": pg.get("title", "")})

    # -- fetch + normalize -------------------------------------------------- #
    def fetch(self, ref: SourceRef) -> SourceRecord:
        pid = ref.external_id
        pg = self._get(f"/rest/api/content/{pid}",
                       {"expand": "body.storage,version,history,space"})
        title = pg.get("title", f"page {pid}")
        body = f"{title}\n\n{_html_to_text(pg.get('body', {}).get('storage', {}).get('value', ''))}"
        created = (pg.get("history", {}).get("createdDate", "") or "")[:10]
        author = pg.get("history", {}).get("createdBy", {}).get("email", "") or \
            pg.get("history", {}).get("createdBy", {}).get("displayName", "")
        return SourceRecord(
            source=self.name,
            external_id=pid,
            kind="doc",
            title=title,
            body=body,
            occurred_on=created,
            people=[author] if author else [],
            components=[],                 # inferred on ingest
            issue_ids=ISSUE_RE.findall(body),
            acl=self._acl_for_page(pid),
            url=f"{self.base}/pages/{pid}",
        )

    # -- permissions -------------------------------------------------------- #
    def resolve_acl(self, ref: SourceRef) -> Acl:
        return self._acl_for_page(ref.external_id)

    def _acl_for_page(self, pid: str) -> Acl:
        """Read restrictions on a page map to a restricted allow-list; a page
        with none is internal (space-wide)."""
        try:
            data = self._get(f"/rest/api/content/{pid}/restriction/byOperation/read")
        except Exception:
            return Acl(visibility="internal")
        users = data.get("restrictions", {}).get("user", {}).get("results", [])
        groups = data.get("restrictions", {}).get("group", {}).get("results", [])
        if not users and not groups:
            return Acl(visibility="internal")
        allow = [u.get("email") or u.get("accountId") for u in users if u]
        allow += [g.get("name") for g in groups if g]
        return Acl(visibility="restricted", allow=[a for a in allow if a])

    def subscribe(self) -> WebhookSpec | None:
        return WebhookSpec(path="/api/webhook/confluence",
                           verify_header="", secret_env="CONFLUENCE_WEBHOOK_SECRET")


assert isinstance(ConfluenceConnector(base_url="", email="", token=""), Connector)
