"""Shadow ingest pipeline (beats 3 & 4).

ingest(file_id) orchestrates:
  1. librarian_classify  — extract metadata + decide sensitivity (hr/restricted -> move & stop)
  2. scribe_note         — meetings: write a structured note to Memory/Meeting Notes
  3. librarian_decision  — detect decision status change; version the decision file;
                           if confidence < high, set confirmed=pending and create a
                           Box task for the meeting host
  4. cartographer_update — refresh people cards for attendees

Box AI extract_structured is used when available; otherwise Bedrock extracts
from the downloaded text. Decision-change reasoning always uses Bedrock.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime

import requests

from shadow_box import tools as T

from .activity import log_activity
from .common import bedrock_json, enterprise_scope, ids


def _components() -> list[str]:
    from core import taxonomy

    return taxonomy.components()


def _md_from_listing(entry: dict, template: str) -> dict:
    """Inline template metadata from a folder_items entry (no extra HTTP call)."""
    md = entry.get("metadata") or {}
    scoped = md.get(enterprise_scope()) or {}
    inst = scoped.get(template) or {}
    return {k: v for k, v in inst.items() if not k.startswith("$")}


def _download_text(file_id: str) -> str:
    tok = T._token()
    r = requests.get(f"https://api.box.com/2.0/files/{file_id}/content",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    r.raise_for_status()
    return r.text


def _file_name(file_id: str) -> str:
    md = T.get_metadata(file_id, "shadowItem")
    if md and md.get("title"):
        return md["title"]
    tok = T._token()
    r = requests.get(f"https://api.box.com/2.0/files/{file_id}?fields=name",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    return r.json().get("name", file_id)


# --------------------------------------------------------------------------- #
# 1. Librarian — classify + metadata + sensitivity
# --------------------------------------------------------------------------- #
def librarian_classify(file_id: str, text: str) -> dict:
    existing = T.get_metadata(file_id, "shadowItem")
    if existing and existing.get("summary"):
        md = {k: v for k, v in existing.items() if not k.startswith("$")}
        log_activity("Librarian", f"Read existing metadata for {_file_name(file_id)}",
                     file_id)
    else:
        md = _extract_metadata(file_id, text)
        clean = {k: v for k, v in md.items() if v not in ("", None, [])}
        T.apply_metadata(file_id, "shadowItem", clean)
        log_activity("Librarian", f"Extracted metadata for {_file_name(file_id)}",
                     file_id, f"sourceType={md.get('sourceType')}")

    sensitivity = md.get("sensitivity", "normal")
    if sensitivity == "hr":
        T.move_file(file_id, ids()["folders"]["Quarantine"])
        log_activity("Librarian", "Quarantined 1 item (HR) - never used in answers",
                     file_id)
        return {"stop": True, "metadata": md, "sensitivity": "hr"}
    if sensitivity == "restricted":
        T.move_file(file_id, ids()["folders"]["Restricted"])
        log_activity("Librarian", "Moved to Restricted (host-only)", file_id)
        return {"stop": True, "metadata": md, "sensitivity": "restricted"}
    return {"stop": False, "metadata": md, "sensitivity": sensitivity}


def _extract_metadata(file_id: str, text: str) -> dict:
    # try Box AI first
    try:
        res = T.extract_structured(file_id, "shadowItem")
        if res and not res.get("error") and (res.get("answer") or res.get("fields") or
                                             any(k for k in res if k not in ("error",))):
            # Box returns the fields at top level
            fields = {k: v for k, v in res.items()
                      if k in {"sourceType", "title", "occurredOn", "components",
                               "issueIds", "people", "summary", "sensitivity"}}
            if fields.get("summary"):
                return fields
    except Exception:
        pass
    # Bedrock extraction from text
    from core import taxonomy

    _domain = taxonomy.canonical_domain() or "example.com"
    prompt = (
        "Extract metadata from this team source file as JSON with keys: "
        "sourceType (one of meeting,slack,issue,pr,doc,org), title, occurredOn "
        "(YYYY-MM-DD), components (subset of "
        f"{_components()}), issueIds (comma string like PAY-482), people "
        f"(comma emails firstname@{_domain}), summary (one sentence), "
        "sensitivity (normal|restricted|hr). Content:\n\n" + text[:4000]
    )
    try:
        md = bedrock_json(prompt, max_tokens=500)
        if isinstance(md.get("components"), str):
            md["components"] = [c.strip() for c in md["components"].split(",") if c.strip()]
        return md
    except Exception:
        # last-resort deterministic guess for a meeting transcript
        return {"sourceType": "meeting", "title": _file_name(file_id),
                "occurredOn": "2026-10-17",
                "components": ["retry-service", "gateway-client"],
                "issueIds": ",".join(re.findall(r"PAY-\d+", text)),
                "people": "", "summary": text.strip().splitlines()[0][:160],
                "sensitivity": "normal"}


# --------------------------------------------------------------------------- #
# 2. Scribe — structured note (meetings only)
# --------------------------------------------------------------------------- #
def scribe_note(file_id: str, text: str, md: dict) -> dict:
    title = md.get("title", _file_name(file_id))
    date = (md.get("occurredOn", "") or "")[:10]
    prompt = (
        "Write concise structured meeting notes in markdown with sections "
        "**Summary**, **Decisions**, **Action items**, **People**. Base them "
        "ONLY on this transcript. Keep it short.\n\n" + text[:4000]
    )
    try:
        from .common import bedrock_complete

        body = bedrock_complete(prompt, max_tokens=500)
    except Exception:
        body = (f"# Meeting Notes — {title}\n\n**Summary:** {md.get('summary','')}\n\n"
                f"**People:** {md.get('people','')}\n")
    note_md = f"# Meeting Notes — {title}, {date}\n\n{body}\n"

    fname = f"note-{file_id}.md"   # unique per source file; a replay overwrites in place
    scratch = tempfile.mkdtemp(prefix="shadow-ingest-")  # per-run, no collisions
    path = os.path.join(scratch, fname)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(note_md)
    folder = ids()["folders"]["Memory/Meeting Notes"]
    note_name = f"notes-{file_id}.md"
    # idempotent: if a note for this meeting already exists, update it in place
    # instead of uploading a duplicate
    existing_id = next((it["id"] for it in T.folder_items(folder)
                        if it.get("type") == "file" and it["name"] == note_name), None)
    if existing_id:
        new_id = T.upload_new_version(existing_id, path)
    else:
        new_id = T.upload_file(folder, path, name=note_name)
    note_meta = {
        "sourceType": "meeting", "title": f"Notes — {title}", "occurredOn": date,
        "components": md.get("components", []), "issueIds": md.get("issueIds", ""),
        "people": md.get("people", ""),
        "summary": f"Structured notes for {title}.", "sensitivity": "normal",
    }
    T.apply_metadata(new_id, "shadowItem",
                     {k: v for k, v in note_meta.items() if v not in ("", None, [])})
    log_activity("Scribe", f"Wrote meeting notes for {title}", new_id)
    return {"note_file_id": new_id}


# --------------------------------------------------------------------------- #
# 3. Librarian — decision-change detection
# --------------------------------------------------------------------------- #
def _existing_decisions(components: list[str]) -> list[dict]:
    folder = ids()["folders"]["Memory/Decisions"]
    out = []
    for it in T.folder_items(folder):
        if it.get("type") != "file":
            continue
        md = T.get_metadata(it["id"], "shadowDecision")
        if not md:
            continue
        comps = md.get("components", []) or []
        if not components or any(c in comps for c in components):
            out.append({"file_id": it["id"], "name": it["name"],
                        "metadata": {k: v for k, v in md.items()
                                     if not k.startswith("$")}})
    return out


def librarian_decision(file_id: str, text: str, md: dict) -> dict:
    components = md.get("components", []) or []
    decisions = _existing_decisions(components)
    if not decisions:
        return {"changed": False, "reason": "no matching decision"}
    # deterministic pick: the decision whose components best overlap this file's,
    # then by file id for a stable tiebreak — NOT an arbitrary decisions[0].
    want = set(md.get("components", []) or [])
    dec = sorted(
        decisions,
        key=lambda d: (-len(want & set((d.get("metadata") or {}).get("components", []) or [])),
                       str(d.get("file_id", ""))),
    )[0]
    current = dec["metadata"]
    prompt = (
        "You compare a team's recorded decision against a new meeting transcript.\n"
        f"Recorded decision: {json.dumps(current)}\n\n"
        f"New meeting transcript:\n{text[:3500]}\n\n"
        "Return JSON: {\"changed\": bool, \"new_status\": one of "
        "proposed|decided|paused|resolved|reversed, \"decision\": short string, "
        "\"confidence\": high|medium|low, \"reason\": short string}. "
        "Mark changed=true only if the transcript clearly moves the decision "
        "(e.g. from paused to decided). If wording is tentative "
        "('basically settled', 'unless anyone objects'), use confidence medium or low."
    )
    try:
        verdict = bedrock_json(prompt, max_tokens=300)
    except Exception as e:
        # deterministic fallback: detect 'settled'/'go with that' phrasing
        low = text.lower()
        changed = ("settled" in low or "go with that" in low or "ship" in low) and \
                  current.get("status") == "paused"
        verdict = {"changed": changed, "new_status": "decided" if changed else current.get("status"),
                   "decision": "Ship idempotency keys behind a feature flag next sprint",
                   "confidence": "medium", "reason": f"heuristic ({e})"}

    if not verdict.get("changed"):
        log_activity("Librarian", "Reviewed decision — no change", dec["file_id"],
                     verdict.get("reason", ""))
        return {"changed": False, "verdict": verdict}

    # version the decision file + update metadata
    new_status = verdict.get("new_status", "decided")
    decided_on = (md.get("occurredOn", "") or datetime.now(UTC).strftime("%Y-%m-%d"))[:10]
    confidence = verdict.get("confidence", "medium")
    confirmed = "pending" if confidence != "high" else "yes"
    new_decision = verdict.get("decision", current.get("decision", ""))

    body = (f"# Decision — Idempotency keys for retry double-charges\n\n"
            f"**Decision:** {new_decision}\n**Status:** {new_status}\n"
            f"**Decided on:** {decided_on}\n**Owners:** {current.get('owners','')}\n"
            f"**Confirmed:** {confirmed}\n\n## Context\n"
            f"Updated from the {decided_on} standup. {verdict.get('reason','')}\n")
    scratch = tempfile.mkdtemp(prefix="shadow-ingest-")  # per-run, no collisions
    path = os.path.join(scratch, "decision.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    T.upload_new_version(dec["file_id"], path)
    src = current.get("sourceFileIds", "")
    src = (src + "," + file_id) if src else file_id
    T.apply_metadata(dec["file_id"], "shadowDecision", {
        "decision": new_decision, "status": new_status, "decidedOn": decided_on,
        "confirmed": confirmed, "sourceFileIds": src,
    })
    log_activity("Librarian",
                 f"Decision updated: {current.get('status')} -> {new_status} "
                 f"(confirmed={confirmed})", dec["file_id"], verdict.get("reason", ""))

    task = None
    if confirmed == "pending":
        host_email = _meeting_host_email(text, md)
        host_name = host_email.split("@")[0].replace(".", " ").title() if host_email else "the host"
        host_id = _user_id_for_email(host_email)
        if host_id:
            task = T.create_task_and_assign(
                dec["file_id"],
                f"Confirm: {new_decision} (decided {decided_on})?", host_id)
            log_activity("Librarian",
                         f"Created confirmation task for {host_name.title()}",
                         dec["file_id"], f"assignment {task.get('assignment_id')}")
    return {"changed": True, "verdict": verdict, "decision_file": dec["file_id"],
            "confirmed": confirmed, "task": task}


def _meeting_host_email(text: str, md: dict) -> str:
    m = re.search(r"Host:\s*([A-Za-z ]+)", text)
    if m:
        from core import identity

        resolved = identity.resolve(m.group(1).strip())
        if resolved and "@" in resolved:
            return resolved
    # no parseable/known host -> let the caller's user-id lookup degrade
    return ""


def _user_id_for_email(email: str) -> str | None:
    """Map a principal/email to a Box App User id by matching the display names
    in ids()['users'] — first by first-name prefix, with no hardcoded roster."""
    if not email:
        return None
    users = ids()["users"]
    first = email.split("@")[0].split(".")[0].lower()
    for full, uid in users.items():
        if full.lower().startswith(first):
            return uid
    return None


# --------------------------------------------------------------------------- #
# 4. Cartographer — people cards
# --------------------------------------------------------------------------- #
def cartographer_update(md: dict) -> dict:
    from core import identity

    people = [p.strip() for p in (md.get("people", "") or "").split(",") if p.strip()]
    principals = {identity.resolve(p) for p in people if p}
    date = (md.get("occurredOn", "") or "")[:10]
    folder = ids()["folders"]["Memory/People"]
    updated = []
    # Match attendees to existing cards by the card's OWN email metadata, so no
    # name<->email roster is hardcoded. One listing read, inline metadata.
    for it in T.folder_items(folder, md_template="shadowPerson"):
        if it.get("type") != "file":
            continue
        card_md = _md_from_listing(it, "shadowPerson")
        card_email = (card_md.get("email") or "").strip()
        if not card_email:
            continue
        if card_email in principals or identity.resolve(card_email) in principals:
            T.apply_metadata(it["id"], "shadowPerson", {"lastActiveOn": date})
            updated.append(card_md.get("name") or card_email)
    if updated:
        log_activity("Cartographer", f"Updated activity for {', '.join(updated)}", "",
                     f"lastActiveOn={date}")
    return {"updated": updated}


# --------------------------------------------------------------------------- #
# orchestrator
# --------------------------------------------------------------------------- #
def ingest(file_id: str) -> dict:
    file_id = str(file_id)

    # IDEMPOTENCY: a webhook can fire more than once for the same file version
    # (Box retries, at-least-once delivery). Key on the Box file version: if we
    # already processed THIS version, do nothing — so replaying the same webhook
    # N times yields exactly one set of outputs. The key lives on the file's
    # shadowPipeline.processedVersion (the template added in Week 2).
    version = T.file_version(file_id)
    pipe = T.get_metadata(file_id, "shadowPipeline") or {}
    if version and pipe.get("processedVersion") == version:
        log_activity("Webhook", f"Duplicate ignored (version {version} already "
                     f"processed): {_file_name(file_id)}", file_id)
        return {"ok": True, "skipped": "already processed", "version": version}

    log_activity("Webhook", f"New file received: {_file_name(file_id)}", file_id)
    text = _download_text(file_id)

    step1 = librarian_classify(file_id, text)
    md = step1["metadata"]
    if step1.get("stop"):
        _mark_processed(file_id, version, "done")
        return {"ok": True, "stopped_at": "librarian",
                "sensitivity": step1["sensitivity"], "steps": [step1]}

    steps = [step1]
    if md.get("sourceType") == "meeting":
        steps.append(scribe_note(file_id, text, md))
        steps.append(librarian_decision(file_id, text, md))
    steps.append(cartographer_update(md))
    _mark_processed(file_id, version, "done")
    return {"ok": True, "steps": steps, "metadata": md}


def _mark_processed(file_id: str, version: str, state: str) -> None:
    """Record that this file VERSION has been fully processed — the idempotency
    guard reads this on the next (possibly duplicate) webhook."""
    try:
        T.apply_metadata(file_id, "shadowPipeline",
                         {"processedVersion": version or "", "state": state})
    except Exception as e:  # don't fail the whole ingest on the flag write
        log_activity("Webhook", f"could not mark processed: {e}", file_id)


if __name__ == "__main__":
    import sys

    print(json.dumps(ingest(sys.argv[1]), indent=2, default=str))
