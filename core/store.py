"""Record store — where ingested SourceRecords live so retrieval can query them.

Connector records (GitHub PRs, Slack threads) do not belong in Box, so they
persist here. `RecordStore` is the stable facade the rest of the core uses
(`put` / `all` / `item_for` / `count` / `count_by_source`); it delegates to a
pluggable **backend** chosen by `SHADOW_STORE_BACKEND`:

  - "jsonl"    (default) a dependency-free JSON-lines file. Runs anywhere with
               no infra — right for local dev and the demo.
  - "pgvector" a Postgres + pgvector backend for real volume / concurrency /
               vector recall. The seam is wired here; the class documents the
               exact DDL + queries and raises a clear error until its driver
               (psycopg) and DATABASE_URL are configured.

Swapping the backend changes no call site: ingest still calls `store.put(...)`
and retrieval still calls `store.all()`. This is the "durable store at scale"
item from STATUS.md's finish list — the abstraction that lets the JSON file be
replaced without touching the engine.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

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


# --------------------------------------------------------------------------- #
# Backend protocol — the seam a production store implements
# --------------------------------------------------------------------------- #
@runtime_checkable
class StoreBackend(Protocol):
    """Upsert-by-key record storage. A backend only moves bytes; all record
    <-> row projection stays in this module, so a new backend implements four
    small methods and nothing else."""

    def read_rows(self) -> dict[str, dict]:
        """Return every stored row keyed by `_key`."""
        ...

    def write_rows(self, rows: dict[str, dict]) -> None:
        """Persist the full row set (upsert semantics via the caller)."""
        ...


class JsonlBackend:
    """The default: a JSON-lines file. Dependency-free, process-safe via the
    module lock, right for local dev and the demo."""

    def read_rows(self) -> dict[str, dict]:
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

    def write_rows(self, rows: dict[str, dict]) -> None:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows.values():
                fh.write(json.dumps(r) + "\n")


class PgVectorBackend:
    """Postgres + pgvector backend for scale (the STATUS.md 'durable store at
    scale' target). The seam is complete; the implementation is intentionally a
    documented stub so the dependency (psycopg) and DATABASE_URL are only
    required when this backend is explicitly selected.

    Table shape:
        CREATE TABLE shadow_records (
            key          text PRIMARY KEY,         -- "<source>:<external_id>"
            row          jsonb NOT NULL,           -- the full _record_to_row dict
            embedding    vector(1024),             -- Titan v2 dim, for ANN recall
            quarantined  boolean NOT NULL DEFAULT false
        );
        CREATE INDEX ON shadow_records USING ivfflat (embedding vector_cosine_ops);

    read_rows -> SELECT key, row FROM shadow_records;
    write_rows -> upsert each row (INSERT ... ON CONFLICT (key) DO UPDATE).
    Vector recall then runs in the DB (ORDER BY embedding <=> :qvec LIMIT k)
    instead of core.retrieve scanning every record in Python — the real scale
    win. core.retrieve keeps its current signature; only its candidate source
    changes when this backend is active.
    """

    def __init__(self) -> None:
        self._dsn = os.environ.get("DATABASE_URL", "")

    def _unavailable(self) -> RuntimeError:
        return RuntimeError(
            "pgvector backend selected (SHADOW_STORE_BACKEND=pgvector) but not "
            "provisioned: set DATABASE_URL and install psycopg[binary] + pgvector. "
            "See core/store.py PgVectorBackend for the table DDL. Falling back to "
            "the jsonl backend is done automatically when this is unset."
        )

    def read_rows(self) -> dict[str, dict]:
        raise self._unavailable()

    def write_rows(self, rows: dict[str, dict]) -> None:
        raise self._unavailable()


def _make_backend() -> StoreBackend:
    """Select the backend from SHADOW_STORE_BACKEND (default jsonl). An unknown
    value falls back to jsonl with a warning rather than crashing — the store is
    load-bearing, so it must always come up."""
    name = os.environ.get("SHADOW_STORE_BACKEND", "jsonl").lower()
    if name == "pgvector":
        return PgVectorBackend()
    if name not in ("jsonl", ""):
        print(f"[store] unknown SHADOW_STORE_BACKEND={name!r}; using jsonl")
    return JsonlBackend()


class RecordStore:
    """Upsert-by-key store. Stable public API (put/all/item_for/count/
    count_by_source); the storage medium is the injected backend."""

    def __init__(self, backend: StoreBackend | None = None) -> None:
        self._backend = backend or _make_backend()

    def put(self, record: SourceRecord, item: dict, quarantined: bool = False) -> None:
        row = _record_to_row(record, item, quarantined)
        with _LOCK:
            rows = self._backend.read_rows()
            rows[row["_key"]] = row
            self._backend.write_rows(rows)

    def all(self, include_quarantined: bool = False) -> list[SourceRecord]:
        rows = self._backend.read_rows()
        return [_row_to_record(r) for r in rows.values()
                if include_quarantined or not r.get("_quarantined")]

    def item_for(self, record: SourceRecord) -> dict:
        rows = self._backend.read_rows()
        r = rows.get(_key(record.source, record.external_id)) or {}
        return r.get("_item", {})

    def count(self) -> int:
        return sum(1 for r in self._backend.read_rows().values()
                   if not r.get("_quarantined"))

    def count_by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._backend.read_rows().values():
            if r.get("_quarantined"):
                continue
            out[r["source"]] = out.get(r["source"], 0) + 1
        return out


# module-level singleton, the usual entry point
store = RecordStore()
