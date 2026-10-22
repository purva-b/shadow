"""Record store — where ingested SourceRecords live so retrieval can query them.

Connector records (GitHub PRs, Slack threads) do not belong in Box, so they
persist here. `RecordStore` is the stable facade the rest of the core uses
(`put` / `all` / `item_for` / `count` / `count_by_source` / `vector_recall`);
it delegates to a pluggable **backend** chosen by `SHADOW_STORE_BACKEND`:

  - "jsonl"    (default) a dependency-free JSON-lines file. Runs anywhere with
               no infra — right for local dev and the demo. Vector recall is a
               Python scan in `core.retrieve`.
  - "pgvector" a Postgres + pgvector backend for real volume / concurrency /
               vector recall. Records AND their Titan embeddings live in the DB;
               nearest-neighbour recall runs as `ORDER BY embedding <=> :q` in
               Postgres instead of a Python scan over every record. Needs the
               `pgvector` extra (psycopg) and `DATABASE_URL`.

Swapping the backend changes no call site: ingest still calls `store.put(...)`,
retrieval still calls `store.all()` / `store.vector_recall(...)`. This is the
"durable store at scale" item from STATUS.md's finish list — the abstraction
that lets the JSON file be replaced without touching the engine.
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

# Titan v2 embedding dimension — the pgvector column width.
EMBED_DIM = 1024


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
    """Upsert-by-key record storage. A backend moves bytes; all record <-> row
    projection stays in this module, so a new backend implements two small
    methods and, if it can, two optional vector methods.

    A backend that cannot do vector recall simply omits `supports_vector` /
    `vector_recall` (or returns False); the store then falls back to the
    in-Python scan in `core.retrieve`. A backend that can — pgvector — runs the
    nearest-neighbour query in the engine, which is the real scale win."""

    def read_rows(self) -> dict[str, dict]:
        """Return every stored row keyed by `_key`."""
        ...

    def write_rows(self, rows: dict[str, dict]) -> None:
        """Persist the full row set (upsert semantics via the caller)."""
        ...


class JsonlBackend:
    """The default: a JSON-lines file. Dependency-free, process-safe via the
    module lock, right for local dev and the demo. No DB-side vector recall —
    `core.retrieve` scans in Python, which is fine at demo scale."""

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

    def supports_vector(self) -> bool:
        return False


class PgVectorBackend:
    """Postgres + pgvector backend for scale (the STATUS.md 'durable store at
    scale' target). Records and their Titan embeddings live in one table;
    nearest-neighbour recall runs in the DB.

    Table shape (created on first connect, idempotently):

        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS shadow_records (
            key          text PRIMARY KEY,         -- "<source>:<external_id>"
            source       text NOT NULL,
            row          jsonb NOT NULL,           -- the full _record_to_row dict
            embedding    vector(1024),             -- Titan v2 dim, nullable
            quarantined  boolean NOT NULL DEFAULT false
        );
        CREATE INDEX IF NOT EXISTS shadow_records_embedding_idx
            ON shadow_records USING ivfflat (embedding vector_cosine_ops);

    `read_rows` / `write_rows` keep the facade's bulk API working; `vector_recall`
    is the fast path `core.retrieve` uses when `supports_vector()` is True:
    candidate selection becomes `ORDER BY embedding <=> :qvec LIMIT k` in
    Postgres instead of a scan over every record in Python.

    The driver (psycopg) is an OPTIONAL extra, so importing this module costs
    nothing; psycopg is imported lazily inside `_connect`, and a clear error is
    raised only when this backend is actually selected without being provisioned.
    """

    def __init__(self) -> None:
        self._dsn = os.environ.get("DATABASE_URL", "")
        self._initialized = False

    # --- connection / schema ------------------------------------------------ #
    def _connect(self):
        if not self._dsn:
            raise RuntimeError(
                "pgvector backend selected (SHADOW_STORE_BACKEND=pgvector) but "
                "DATABASE_URL is not set. Set DATABASE_URL and install the extra: "
                "pip install -e '.[pgvector]'. The jsonl backend is used "
                "automatically when SHADOW_STORE_BACKEND is unset."
            )
        try:
            import psycopg  # type: ignore
        except ImportError as e:  # pragma: no cover - exercised via fake in tests
            raise RuntimeError(
                "pgvector backend selected but psycopg is not installed. "
                "Install the extra: pip install -e '.[pgvector]'."
            ) from e
        conn = psycopg.connect(self._dsn, autocommit=True)
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn) -> None:
        if self._initialized:
            return
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS shadow_records (
                    key          text PRIMARY KEY,
                    source       text NOT NULL,
                    row          jsonb NOT NULL,
                    embedding    vector({EMBED_DIM}),
                    quarantined  boolean NOT NULL DEFAULT false
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS shadow_records_embedding_idx "
                "ON shadow_records USING ivfflat (embedding vector_cosine_ops)"
            )
        self._initialized = True

    @staticmethod
    def _vec_literal(vec: list[float] | None) -> str | None:
        """pgvector accepts a bracketed CSV string, e.g. '[0.1,0.2,...]'."""
        if not vec:
            return None
        return "[" + ",".join(repr(float(x)) for x in vec) + "]"

    # --- bulk facade (read_rows / write_rows) ------------------------------- #
    def read_rows(self) -> dict[str, dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT key, row FROM shadow_records")
                return {k: (r if isinstance(r, dict) else json.loads(r))
                        for k, r in cur.fetchall()}
        finally:
            conn.close()

    def write_rows(self, rows: dict[str, dict]) -> None:
        """Upsert every row. Embeddings are computed here (lazily, via
        core.embeddings) from title+body so recall has a vector to query."""
        from . import embeddings
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                for key, row in rows.items():
                    rec = _row_to_record(row)
                    vec = (embeddings.embed(f"{rec.title}\n{rec.body}")
                           if embeddings.available() else None)
                    cur.execute(
                        """
                        INSERT INTO shadow_records (key, source, row, embedding, quarantined)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (key) DO UPDATE SET
                            source = EXCLUDED.source,
                            row = EXCLUDED.row,
                            embedding = COALESCE(EXCLUDED.embedding, shadow_records.embedding),
                            quarantined = EXCLUDED.quarantined
                        """,
                        (key, row["source"], json.dumps(row),
                         self._vec_literal(vec), bool(row.get("_quarantined"))),
                    )
        finally:
            conn.close()

    # --- vector capability -------------------------------------------------- #
    def supports_vector(self) -> bool:
        return True

    def vector_recall(self, qvec: list[float], k: int,
                      sources: list[str] | None = None) -> list[dict]:
        """Return up to k non-quarantined rows nearest to `qvec` by cosine
        distance, filtered to `sources` when given. The ranking happens in
        Postgres (`embedding <=> :q`), not in Python."""
        qlit = self._vec_literal(qvec)
        if qlit is None:
            return []
        sql = (
            "SELECT row FROM shadow_records "
            "WHERE quarantined = false AND embedding IS NOT NULL"
        )
        params: list = []
        if sources:
            sql += " AND source = ANY(%s)"
            params.append(list(sources))
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params.extend([qlit, int(k)])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [(r if isinstance(r, dict) else json.loads(r))
                        for (r,) in cur.fetchall()]
        finally:
            conn.close()


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
    count_by_source/vector_recall); the storage medium is the injected backend."""

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

    # --- vector recall seam ------------------------------------------------- #
    def supports_vector(self) -> bool:
        """True when the backend does nearest-neighbour recall in the engine."""
        fn = getattr(self._backend, "supports_vector", None)
        return bool(fn and fn())

    def vector_recall(self, qvec: list[float], k: int,
                     sources: list[str] | None = None) -> list[SourceRecord]:
        """Backend-side nearest-neighbour recall. Only valid when
        `supports_vector()` — `core.retrieve` checks first and otherwise scans
        in Python, so this never needs a fallback here."""
        rows = self._backend.vector_recall(qvec, k, sources)  # type: ignore[attr-defined]
        return [_row_to_record(r) for r in rows]


# module-level singleton, the usual entry point
store = RecordStore()
