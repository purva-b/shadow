"""Proactive notifications — Shadow reaching out, not just answering.

When a notable record is ingested (a decision change, a PR/issue on a tracked
component, an event naming an owner), Shadow drafts a notification to the people
who should know and records it. This is what turns ask-and-answer into an
autonomous teammate.

Delivery is pluggable: by default notifications are appended to a local
notifications store (and the activity feed) so the UI can surface them. A real
deployment registers a sender (Slack DM, Box comment, email) via
register_sender; the detection/targeting logic is identical either way.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from connectors.base import SourceRecord

from . import identity

ROOT = Path(__file__).resolve().parent.parent
_LOCK = threading.Lock()

# optional real delivery hook: sender(principal, title, body) -> None
_SENDER: Callable[[str, str, str], None] | None = None


def register_sender(fn: Callable[[str, str, str], None]) -> None:
    global _SENDER
    _SENDER = fn


def _path() -> Path:
    return Path(os.environ.get("SHADOW_NOTIFY_STORE",
                               str(ROOT / "web" / "notifications.jsonl")))


# --------------------------------------------------------------------------- #
# detection — is this record worth a proactive ping, and to whom?
# --------------------------------------------------------------------------- #
# components a person "watches" could come from their people-card expertise; for
# now we notify the record's own named people plus anyone flagged as an owner.
def _notable(record: SourceRecord) -> str | None:
    """Return a human reason this record is notable, or None to stay silent."""
    text = f"{record.title} {record.body}".lower()
    if record.kind == "pr" and record.issue_ids:
        return f"a PR landed referencing {', '.join(record.issue_ids)}"
    if any(w in text for w in ("decided", "decision", "settled", "ship ", "approved")):
        return "a decision looks like it changed"
    if record.kind == "event" and record.people:
        return "a meeting was scheduled that names you"
    if record.issue_ids:
        return f"new activity on {', '.join(record.issue_ids)}"
    return None


def _targets(record: SourceRecord) -> list[str]:
    """Canonical principals who should hear about this record."""
    return identity.resolve_all(record.people)


def notify_for_record(record: SourceRecord) -> list[dict]:
    """Detect + emit proactive notifications for one freshly-ingested record.
    Returns the notifications created (possibly empty)."""
    reason = _notable(record)
    if not reason:
        return []
    targets = _targets(record)
    if not targets:
        return []
    title = f"Shadow: {reason}"
    body = (f"{record.title} ({record.source}) — {reason}. "
            f"{(record.body or '')[:160]}")
    notes = []
    for principal in targets:
        note = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "to": principal, "title": title, "body": body,
            "source": record.source, "external_id": record.external_id,
            "url": record.url,
        }
        notes.append(note)
        if _SENDER is not None:
            try:
                _SENDER(principal, title, body)
            except Exception:
                pass
    _append(notes)
    return notes


def _append(notes: list[dict]) -> None:
    if not notes:
        return
    p = _path()
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            for n in notes:
                fh.write(json.dumps(n) + "\n")


def recent(limit: int = 20, principal: str | None = None) -> list[dict]:
    """Read recent notifications, newest first; optionally filter to a person."""
    p = _path()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            n = json.loads(line)
        except Exception:
            continue
        if principal and identity.resolve(n.get("to", "")) != identity.resolve(principal):
            continue
        rows.append(n)
    return list(reversed(rows))[:limit]
