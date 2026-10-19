"""Calendar connector — meetings/events as a source.

Normalizes calendar events (Google Calendar API shape) into SourceRecords. An
event's attendees ARE its permission: only attendees may read it, so the Acl is
`restricted` with the attendee emails as the allow-list. A public/organization
event (no attendee list) maps to `internal`.

Config (env, never committed):
  GCAL_ACCESS_TOKEN   an OAuth access token with calendar.readonly
  GCAL_CALENDAR_ID    calendar id (default "primary")

Degrades gracefully: with no token, discover returns nothing.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Iterable

import requests

from .base import Acl, Connector, SourceRecord, SourceRef, WebhookSpec
from .registry import register

API = "https://www.googleapis.com/calendar/v3"
ISSUE_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


@register
class CalendarConnector:
    name = "calendar"

    def __init__(self, token: str | None = None, calendar_id: str | None = None, **_):
        self.token = token or os.environ.get("GCAL_ACCESS_TOKEN", "")
        self.calendar_id = calendar_id or os.environ.get("GCAL_CALENDAR_ID", "primary")

    # -- http --------------------------------------------------------------- #
    def _get(self, path: str, params: dict | None = None) -> dict:
        r = requests.get(f"{API}{path}",
                         headers={"Authorization": f"Bearer {self.token}"},
                         params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    # -- discovery (poll) --------------------------------------------------- #
    def discover(self, since: datetime | None = None) -> Iterable[SourceRef]:
        if not self.token:
            return
        params = {"maxResults": 50, "singleEvents": "true", "orderBy": "startTime"}
        if since:
            params["timeMin"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            data = self._get(f"/calendars/{self.calendar_id}/events", params)
        except Exception:
            return
        for ev in data.get("items", []):
            yield SourceRef(source=self.name, external_id=ev["id"],
                            hint={"title": ev.get("summary", "")})

    # -- fetch + normalize -------------------------------------------------- #
    def fetch(self, ref: SourceRef) -> SourceRecord:
        eid = ref.external_id
        ev = self._get(f"/calendars/{self.calendar_id}/events/{eid}")
        title = ev.get("summary", f"event {eid}")
        desc = ev.get("description", "") or ""
        attendees = [a.get("email", "") for a in ev.get("attendees", []) or [] if a.get("email")]
        body = f"{title}\n\n{desc}\nAttendees: {', '.join(attendees)}"
        start = ev.get("start", {})
        when = (start.get("dateTime") or start.get("date") or "")[:10]
        return SourceRecord(
            source=self.name,
            external_id=eid,
            kind="event",
            title=title,
            body=body,
            occurred_on=when,
            people=attendees,              # resolved to canonical in ingest
            components=[],                 # inferred on ingest
            issue_ids=ISSUE_RE.findall(body),
            acl=self._acl_for_event(ev, attendees),
            url=ev.get("htmlLink", ""),
        )

    # -- permissions -------------------------------------------------------- #
    def resolve_acl(self, ref: SourceRef) -> Acl:
        try:
            ev = self._get(f"/calendars/{self.calendar_id}/events/{ref.external_id}")
        except Exception:
            return Acl(visibility="restricted")
        attendees = [a.get("email", "") for a in ev.get("attendees", []) or [] if a.get("email")]
        return self._acl_for_event(ev, attendees)

    def _acl_for_event(self, ev: dict, attendees: list[str]) -> Acl:
        """Attendees are the read set. A public event (visibility=public) or one
        with no attendee list is organization-internal."""
        if ev.get("visibility") == "public" or not attendees:
            return Acl(visibility="internal")
        return Acl(visibility="restricted", allow=attendees)

    def subscribe(self) -> WebhookSpec | None:
        return WebhookSpec(path="/api/webhook/calendar",
                           verify_header="", secret_env="GCAL_WEBHOOK_SECRET")


assert isinstance(CalendarConnector(token=""), Connector)
