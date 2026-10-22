"""Box connector — the existing Box behavior, expressed through the Connector
interface. This is the worked example every other connector follows: it reuses
the real `shadow_box.tools`, so it is not a mock. Adding Slack/GitHub/Confluence
means writing a sibling file that returns the same `SourceRecord` shape.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import requests

from shadow_box import tools as T

from .base import Acl, Connector, SourceRecord, SourceRef, WebhookSpec
from .registry import register

API = "https://api.box.com/2.0"


@register
class BoxConnector:
    name = "box"

    def __init__(self, folders: dict | None = None, **_):
        # folder-id map from ids.json (Sources/*, Memory/*, Restricted, ...)
        self.folders = folders or {}

    # -- discovery (poll) --------------------------------------------------- #
    def discover(self, since: datetime | None = None) -> Iterable[SourceRef]:
        for key, fid in self.folders.items():
            if not key.startswith("Sources/"):
                continue
            for it in T.folder_items(fid):
                if it.get("type") == "file":
                    yield SourceRef(source=self.name, external_id=it["id"],
                                    hint={"folder": key, "name": it["name"]})

    # -- fetch + normalize -------------------------------------------------- #
    def fetch(self, ref: SourceRef) -> SourceRecord:
        fid = ref.external_id
        body = self._download_text(fid)
        md = T.get_metadata(fid, "shadowItem") or {}
        comps = md.get("components", []) or []
        issue_ids = [s.strip() for s in (md.get("issueIds", "") or "").split(",") if s.strip()]
        people = [p.strip() for p in (md.get("people", "") or "").split(",") if p.strip()]
        return SourceRecord(
            source=self.name,
            external_id=fid,
            kind=md.get("sourceType", "doc") or "doc",
            title=md.get("title") or ref.hint.get("name", fid),
            body=body,
            occurred_on=(md.get("occurredOn", "") or "")[:10],
            people=people,
            components=comps,
            issue_ids=issue_ids,
            acl=self.resolve_acl(ref),
            url=T.box_web_url(fid),
        )

    # -- permissions -------------------------------------------------------- #
    def resolve_acl(self, ref: SourceRef) -> Acl:
        """Box collaborations are the native permission. A file the asker's App
        User cannot GET is restricted; `can_read` is the probe the answer
        pipeline already uses. Here we classify by folder placement, which is
        how setup_box.py models it (Restricted/Quarantine are selective)."""
        folder = ref.hint.get("folder", "")
        if folder.startswith("Restricted") or folder.startswith("Quarantine"):
            return Acl(visibility="restricted")   # allow resolved per-asker via can_read
        return Acl(visibility="internal")

    def subscribe(self) -> WebhookSpec | None:
        return WebhookSpec(path="/api/webhook/box",
                           verify_header="box-signature-primary",
                           secret_env="BOX_WEBHOOK_KEY_1")

    # -- helpers ------------------------------------------------------------ #
    def _download_text(self, file_id: str) -> str:
        tok = T._token()
        r = requests.get(f"{API}/files/{file_id}/content",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        r.raise_for_status()
        return r.text


# interface conformance check (cheap, import-time)
assert isinstance(BoxConnector(folders={}), Connector)
