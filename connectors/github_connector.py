"""GitHub connector — the first real external plugin.

Pulls issues and pull requests from a repo and normalizes each to a
SourceRecord, so Shadow answers questions about PRs/issues the same way it
answers about Box files. Permissions map from repo visibility: a public repo is
`internal`; a private repo is `restricted` with the repo's readers as `allow`.

Config (env, never committed):
  GITHUB_TOKEN   a PAT or app token with repo:read
  GITHUB_REPO    "owner/name" (can also be passed to __init__)

Degrades gracefully: with no token, discover/fetch return empty/raise clearly
rather than inventing data.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Iterable

import requests

from .base import Acl, Connector, SourceRecord, SourceRef, WebhookSpec
from .registry import register

API = "https://api.github.com"
ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")   # external tracker ids in text


@register
class GitHubConnector:
    name = "github"

    def __init__(self, repo: str | None = None, token: str | None = None, **_):
        self.repo = repo or os.environ.get("GITHUB_REPO", "")
        self.token = token or os.environ.get("GITHUB_TOKEN", "")

    # -- http --------------------------------------------------------------- #
    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        r = requests.get(f"{API}{path}", headers=self._headers(),
                         params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    # -- discovery (poll) --------------------------------------------------- #
    def discover(self, since: datetime | None = None) -> Iterable[SourceRef]:
        if not self.repo:
            return
        params = {"state": "all", "per_page": 50, "sort": "updated",
                  "direction": "desc"}
        if since:
            params["since"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        # /issues returns PRs too (a PR is an issue with a pull_request field)
        for it in self._get(f"/repos/{self.repo}/issues", params):
            kind = "pr" if it.get("pull_request") else "issue"
            yield SourceRef(source=self.name, external_id=str(it["number"]),
                            hint={"kind": kind, "title": it.get("title", "")})

    # -- fetch + normalize -------------------------------------------------- #
    def fetch(self, ref: SourceRef) -> SourceRecord:
        num = ref.external_id
        it = self._get(f"/repos/{self.repo}/issues/{num}")
        kind = "pr" if it.get("pull_request") else "issue"
        body = f"{it.get('title','')}\n\n{it.get('body') or ''}"
        people = [it.get("user", {}).get("login", "")]
        people += [a.get("login", "") for a in it.get("assignees", []) or []]
        people = [p for p in people if p]
        issue_ids = ISSUE_RE.findall(body)
        labels = [lbl.get("name", "") for lbl in it.get("labels", []) or []]
        return SourceRecord(
            source=self.name,
            external_id=num,
            kind=kind,
            title=it.get("title", f"#{num}"),
            body=body,
            occurred_on=(it.get("created_at", "") or "")[:10],
            people=people,                 # resolved to canonical in ingest
            components=labels,             # GitHub labels -> components
            issue_ids=issue_ids,
            acl=self.resolve_acl(ref),
            url=it.get("html_url", f"https://github.com/{self.repo}/issues/{num}"),
        )

    # -- permissions -------------------------------------------------------- #
    def resolve_acl(self, ref: SourceRef) -> Acl:
        """Repo visibility is the native permission. Public repo -> internal
        (anyone in the workspace). Private repo -> restricted, with the repo's
        readers as the allow-list (collaborators + org teams with read)."""
        if not self.repo:
            return Acl(visibility="internal")
        try:
            repo = self._get(f"/repos/{self.repo}")
        except Exception:
            return Acl(visibility="restricted")   # fail closed
        if not repo.get("private"):
            return Acl(visibility="internal")
        allow: list[str] = []
        try:
            for c in self._get(f"/repos/{self.repo}/collaborators",
                               {"permission": "pull", "per_page": 100}):
                login = c.get("login")
                if login:
                    allow.append(login)        # resolved to canonical in acl check
        except Exception:
            pass
        return Acl(visibility="restricted", allow=allow)

    def subscribe(self) -> WebhookSpec | None:
        return WebhookSpec(path="/api/webhook/github",
                           verify_header="x-hub-signature-256",
                           secret_env="GITHUB_WEBHOOK_SECRET")


assert isinstance(GitHubConnector(repo="", token=""), Connector)
