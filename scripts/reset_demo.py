"""Restore the pre-demo state in under ~30s.

    python scripts/reset_demo.py

- Deletes any Oct 17 standup file(s) from Sources/Meetings.
- Deletes generated meeting-note files for Oct 17 from Memory/Meeting Notes.
- Resets the retry decision to paused / confirmed=yes, decidedOn 2026-03-12,
  and uploads the original decision body as a new version.
- Completes/removes any open confirmation tasks on the decision file.
- Clears the local activity log.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.common import ids  # noqa: E402
from shadow_box import tools as T  # noqa: E402

IDS = ids()
EMAIL = "{}@payments.example".format


def _delete_file(file_id: str):
    tok = T._token()
    requests.delete(f"https://api.box.com/2.0/files/{file_id}",
                    headers={"Authorization": f"Bearer {tok}"}, timeout=30)


def _remove_oct17():
    meetings = IDS["folders"]["Sources/Meetings"]
    for it in T.folder_items(meetings):
        if it["type"] == "file" and "2026-10-17" in it["name"]:
            _delete_file(it["id"])
            print(f"  removed {it['name']}")
    notes = IDS["folders"]["Memory/Meeting Notes"]
    for it in T.folder_items(notes):
        if it["type"] == "file" and "2026-10-17" in it["name"]:
            _delete_file(it["id"])
            print(f"  removed note {it['name']}")


def _reset_decision():
    dec = IDS["file_ids"]["Memory/Decisions/retry-idempotency-keys.md"]
    mar = IDS["file_ids"].get("Meetings/2026-03-12-payments-standup.txt", "")
    doc = IDS["file_ids"].get("Docs/retry-idempotency-design.md", "")
    body = (
        "# Decision — Idempotency keys for retry double-charges\n\n"
        "**Decision:** Pause the retry change until the idempotency key design is "
        "finalized.\n**Status:** paused\n**Decided on:** 2026-03-12\n"
        f"**Owners:** {EMAIL('priya')}\n**Confirmed:** yes\n\n## Context\n"
        "Double-charges occur on retry after gateway timeout (PAY-482). The "
        "idempotency key design is the fix but storage + TTL are unresolved, so "
        "implementation is paused.\n"
    )
    import os
    import tempfile

    scratch = os.environ.get("KIROCREW_SCRATCH", tempfile.gettempdir())
    path = os.path.join(scratch, "reset-decision.md")
    Path(path).write_text(body)
    T.upload_new_version(dec, path)
    T.apply_metadata(dec, "shadowDecision", {
        "decision": "Pause the retry change until the idempotency key design is finalized",
        "status": "paused", "decidedOn": "2026-03-12", "confirmed": "yes",
        "sourceFileIds": f"{mar},{doc}" if mar and doc else "",
    })
    print("  decision reset -> paused / confirmed=yes / 2026-03-12")
    # complete any open tasks (as assignee)
    dana = IDS["users"]["Dana Kim"]
    for t in T.list_task_assignments(dec):
        if t.get("resolution_state") != "completed":
            aid = t.get("assigned_to", {}).get("id", dana)
            try:
                T.complete_task(t["id"], as_user=aid)
                print(f"  closed task {t['id']}")
            except Exception:
                pass


def _clear_activity():
    log = ROOT / "web" / "activity_log.jsonl"
    if log.exists():
        log.unlink()
        print("  cleared local activity log")


def main():
    print("reset_demo:")
    _remove_oct17()
    _reset_decision()
    _clear_activity()
    print("done — pre-demo state restored.")


if __name__ == "__main__":
    main()
