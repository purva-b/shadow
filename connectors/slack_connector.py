"""Slack connector — a second real external plugin.

Normalizes Slack messages/threads into SourceRecords. Channel privacy is the
native permission: a public channel maps to `internal`; a private channel maps
to `restricted` with its members as the allow-list.

Config (env, never committed):
  SLACK_BOT_TOKEN   xoxb-… with channels:history, channels:read,
                    groups:history, groups:read, users:read
  SLACK_CHANNELS    optional comma list of channel ids to sync (else all the
                    bot is a member of)

Degrades gracefully: with no token, discover returns nothing rather than
inventing data.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Iterable

import requests

from .base import Acl, Connector, SourceRecord, SourceRef, WebhookSpec
from .registry import register

API = "https://slack.com/api"
ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


@register
class SlackConnector:
    name = "slack"

    def __init__(self, token: str | None = None, channels: str | None = None, **_):
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        env_ch = channels or os.environ.get("SLACK_CHANNELS", "")
        self.channels = [c.strip() for c in env_ch.split(",") if c.strip()]

    # -- http --------------------------------------------------------------- #
    def _call(self, method: str, params: dict | None = None) -> dict:
        r = requests.get(f"{API}/{method}",
                         headers={"Authorization": f"Bearer {self.token}"},
                         params=params or {}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"slack {method}: {data.get('error')}")
        return data

    def _channel_ids(self) -> list[str]:
        if self.channels:
            return self.channels
        data = self._call("conversations.list",
                          {"types": "public_channel,private_channel", "limit": 200})
        return [c["id"] for c in data.get("channels", []) if c.get("is_member")]

    # -- discovery (poll) --------------------------------------------------- #
    def discover(self, since: datetime | None = None) -> Iterable[SourceRef]:
        if not self.token:
            return
        oldest = str(since.timestamp()) if since else "0"
        for cid in self._channel_ids():
            try:
                data = self._call("conversations.history",
                                  {"channel": cid, "oldest": oldest, "limit": 50})
            except Exception:
                continue
            for msg in data.get("messages", []):
                ts = msg.get("ts")
                if ts and not msg.get("subtype"):     # skip joins/leaves/etc.
                    yield SourceRef(source=self.name, external_id=f"{cid}:{ts}",
                                    hint={"channel": cid, "ts": ts})

    # -- fetch + normalize -------------------------------------------------- #
    def fetch(self, ref: SourceRef) -> SourceRecord:
        cid = ref.hint["channel"]
        ts = ref.hint["ts"]
        # pull the message plus any threaded replies for full context
        data = self._call("conversations.replies", {"channel": cid, "ts": ts})
        msgs = data.get("messages", [])
        body = "\n".join(m.get("text", "") for m in msgs)
        people = [m.get("user", "") for m in msgs if m.get("user")]
        chan = self._channel_meta(cid)
        occurred = datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d")
        return SourceRecord(
            source=self.name,
            external_id=ref.external_id,
            kind="chat",
            title=f"#{chan.get('name', cid)} thread",
            body=body,
            occurred_on=occurred,
            people=people,                 # resolved to canonical in ingest
            components=[],                 # Slack has no labels; inferred on ingest
            issue_ids=ISSUE_RE.findall(body),
            acl=self._acl_for_channel(chan),
            url=f"https://slack.com/app_redirect?channel={cid}",
        )

    # -- permissions -------------------------------------------------------- #
    def resolve_acl(self, ref: SourceRef) -> Acl:
        return self._acl_for_channel(self._channel_meta(ref.hint.get("channel", "")))

    def _channel_meta(self, cid: str) -> dict:
        try:
            return self._call("conversations.info", {"channel": cid}).get("channel", {})
        except Exception:
            return {"id": cid}

    def _acl_for_channel(self, chan: dict) -> Acl:
        """Public channel -> internal. Private channel -> restricted, with its
        members as the allow-list."""
        if not chan.get("is_private"):
            return Acl(visibility="internal")
        allow: list[str] = []
        try:
            members = self._call("conversations.members",
                                 {"channel": chan.get("id", ""), "limit": 200})
            allow = members.get("members", [])
        except Exception:
            pass
        return Acl(visibility="restricted", allow=allow)

    def subscribe(self) -> WebhookSpec | None:
        return WebhookSpec(path="/api/webhook/slack",
                           verify_header="x-slack-signature",
                           secret_env="SLACK_SIGNING_SECRET")

    # -- native search ------------------------------------------------------ #
    def search(self, query: str, limit: int = 8) -> list[SourceRecord]:
        """Use Slack's OWN search.messages instead of scanning channel history.
        Note: search.messages needs a USER token (xoxp) with search:read; with a
        bot token it returns nothing, and the caller falls back to the index."""
        if not self.token:
            return []
        try:
            data = self._call("search.messages", {"query": query, "count": limit})
        except Exception:
            return []
        out = []
        for m in data.get("messages", {}).get("matches", [])[:limit]:
            cid = (m.get("channel") or {}).get("id", "")
            ts = m.get("ts", "")
            body = m.get("text", "")
            chan = self._channel_meta(cid) if cid else {}
            out.append(SourceRecord(
                source=self.name, external_id=f"{cid}:{ts}", kind="chat",
                title=f"#{chan.get('name', cid)} message",
                body=body, occurred_on="",
                people=[m.get("user", "")] if m.get("user") else [],
                components=[], issue_ids=ISSUE_RE.findall(body),
                acl=self._acl_for_channel(chan),
                url=m.get("permalink", "")))
        return out


assert isinstance(SlackConnector(token="", channels=""), Connector)
