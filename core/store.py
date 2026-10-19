"""Record store — where ingested SourceRecords live so retrieval can query them.

The demo kept everything in Box. Connector records (GitHub PRs, Slack threads)
do not belong in Box, so they persist here: a simple JSON-lines store keyed by
(source, external_id), holding the normalized record + the enriched item. This
is the sink `core.ingest.ingest_record(record, store=...)` writes to, and the
source `core.retrieve` reads from.

Deliberately file-backed and dependency-free so it runs locally with no infra;
the production swap is pgvector/OpenSearch behind the same put/all/search API.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

from connectors.base import Acl, SourceRecord

ROOT = Path(__file__).resolve().parent.parent
_DEFAULT = ROOT / "web" / "records_store.jsonl"
_LOCK = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get("SHADOW_RECORD_STORE", str(_DEFAULT)))


def _key(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"


def _record_to_row(record: SourceRecord, item: dict, quarantined: bool) -> dict:
    d = asdict(record)                 # includes nested acl as a dict
    d["_item"] = item
    d["_quarantined"] = bool(quarantined)
    d["_key"] = _key(record.source, record.external_id)
    return d


def _row_to_record(row: dict) -> SourceRecord:
    acl = row.get("acl") or {}
    return SourceRecord(
        source=row["source"], external_id=row["external_id"], kind=row["kind"],
        title=row.get("title", ""), body=row.get("body", ""),
        occurred_on=row.get("occurred_on", ""), people=row.get("people", []),
        components=row.get("components", []), issue_ids=row.get("issue_ids", []),
        acl=Acl(visibility=acl.get("visibility", "internal"),
                allow=acl.get("allow", []), deny=acl.get("deny", [])),
        url=row.get("url", ""),
    )


class RecordStore:
    """Upsert-by-key JSON-lines store. put() is called by ingest; all()/search()
    by retrieval."""

    def put(self, record: SourceRecord, item: dict, quarantined: bool = False) -> None:
        row = _record_to_row(record, item, quarantined)
        with _LOCK:
            rows = self._read_rows()
            rows[row["_key"]] = row
            self._write_rows(rows)

    def all(self, include_quarantined: bool = False) -> list[SourceRecord]:
        rows = self._read_rows()
        return [_row_to_record(r) for r in rows.values()
                if include_quarantined or not r.get("_quarantined")]

    def item_for(self, record: SourceRecord) -> dict:
        rows = self._read_rows()
        r = rows.get(_key(record.source, record.external_id)) or {}
        return r.get("_item", {})

    def count(self) -> int:
        return sum(1 for r in self._read_rows().values() if not r.get("_quarantined"))

    def count_by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._read_rows().values():
            if r.get("_quarantined"):
                continue
            out[r["source"]] = out.get(r["source"], 0) + 1
        return out

    # -- disk --------------------------------------------------------------- #
    def _read_rows(self) -> dict[str, dict]:
        p = _path()
        if not p.exists():
            return {}
        rows: dict[str, dict] = {}
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                rows[r["_key"]] = r
            except Exception:
                continue
        return rows

    def _write_rows(self, rows: dict[str, dict]) -> None:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows.values():
                fh.write(json.dumps(r) + "\n")


# module-level singleton, the usual entry point
store = RecordStore()
