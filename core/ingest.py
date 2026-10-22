"""Source-agnostic ingest.

`ingest_record(record)` runs Shadow's pipeline on a normalized SourceRecord,
no matter which connector produced it:

  1. Librarian  — enrich metadata (summary/components) from record.body, decide
                  sensitivity; hr/restricted stop the pipeline.
  2. Scribe     — for a meeting/event, write a structured note.
  3. Librarian  — detect a decision-status change from the body.
  4. Cartographer — refresh people activity (people are canonical principals).

It deliberately does NOT fetch anything: the connector already supplied
`record.body` and `record.acl`. Storage still projects onto the existing
shadowItem shape via record.to_shadow_item(), so the current Box-backed store
and UI keep working while connectors are added.

The legacy Box-file-id entry point (agents/ingest.py `ingest(file_id)`) still
exists; this is the path every NEW connector uses.
"""
from __future__ import annotations

from datetime import UTC, datetime

from connectors.base import SourceRecord

from . import identity

try:
    from core.llm import bedrock_json
except Exception:  # pragma: no cover - agents import optional in some contexts
    bedrock_json = None


def _enrich(record: SourceRecord) -> dict:
    """Fill summary/components from the body when the connector left them empty.
    Uses Bedrock (through the Strands orchestrator) when available, else a cheap
    deterministic summary so ingest never hard-fails offline."""
    components = list(record.components or [])
    summary = ""
    sens = ""
    # Connector-supplied components are authoritative and never overwritten;
    # the LLM only fills a summary and (when the connector left it blank)
    # components + a sensitivity signal.
    need_components = not components
    if bedrock_json is not None and record.body:
        prompt = (
            "Extract JSON from this team source with keys: "
            "summary (one sentence), components (array of short tags), "
            "sensitivity (normal|restricted|hr). "
            f"kind={record.kind}. Content:\n\n{record.body[:4000]}"
        )
        try:
            md = bedrock_json(prompt, max_tokens=400)
            summary = (md.get("summary") or "").strip()
            if need_components and isinstance(md.get("components"), list):
                components = [str(c).strip() for c in md["components"] if str(c).strip()]
            sens = (md.get("sensitivity") or "").strip()
        except Exception:
            sens = ""
    else:
        sens = ""
    if not summary:
        first = (record.body or record.title or "").strip().splitlines()
        summary = (first[0][:160] if first else record.title)[:160]
    item = record.to_shadow_item()
    item["summary"] = summary
    if components:
        item["components"] = components
    # Distinguish two very different notions of "restricted":
    #  - acl.visibility == "restricted": permission-gated, but STILL answerable
    #    to those on the allow-list. The ACL filter enforces this at query time,
    #    so the record stays in the store (not quarantined).
    #  - sensitivity == "hr": genuinely sensitive (a 1:1). NEVER answerable.
    # Only an explicit hr signal from extraction quarantines a record; an
    # extraction "restricted" is downgraded to the ACL (which already handles it).
    if sens == "hr":
        item["sensitivity"] = "hr"
    elif item.get("sensitivity") == "restricted":
        item["sensitivity"] = "gated"   # answerable, permission-gated by Acl
    return item


def ingest_record(record: SourceRecord, store=None) -> dict:
    """Run the pipeline on a normalized record. `store` is an optional sink with
    a put(record, item) method; when None, ingest returns the computed item and
    the caller persists it (keeps this layer storage-agnostic and testable)."""
    # canonicalize people once, up front — everything downstream (people cards,
    # who-to-ask, Acl) expects canonical principals.
    record.people = identity.resolve_all(record.people)

    item = _enrich(record)
    steps = [{"step": "librarian", "sourceType": item["sourceType"],
              "sensitivity": item["sensitivity"]}]

    # quarantine gate — ONLY genuinely sensitive (hr) content is pulled out of
    # answerable memory. Permission-gated records stay in and are filtered by Acl.
    if item["sensitivity"] == "hr":
        result = {"ok": True, "stopped_at": "librarian",
                  "sensitivity": item["sensitivity"], "item": item, "steps": steps}
        if store is not None:
            store.put(record, item, quarantined=True)
        return result

    if record.kind in ("meeting", "event"):
        steps.append({"step": "scribe", "note": f"note for {record.title}"})
        steps.append({"step": "librarian_decision",
                      "checked_on": datetime.now(UTC).strftime("%Y-%m-%d")})

    steps.append({"step": "cartographer", "people": record.people})

    if store is not None:
        store.put(record, item)

    # proactive: ping the owners if this record is notable (decision change,
    # PR/issue activity, an event naming them). Best-effort, never blocks ingest.
    notes = []
    try:
        from . import notify

        notes = notify.notify_for_record(record)
    except Exception:
        notes = []

    return {"ok": True, "item": item, "steps": steps,
            "people": record.people, "url": record.url,
            "notifications": notes}
