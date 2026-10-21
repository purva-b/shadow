"""Store-backend seam (Week 6 + résumé-grade pgvector).

RecordStore's public API (put/all/item_for/count/count_by_source/vector_recall)
must work over ANY StoreBackend, so the JSON-lines file can be swapped for
pgvector without touching a call site. These tests use an in-memory backend
(no disk), prove the default selection is jsonl, prove pgvector fails loudly
when selected without DATABASE_URL, and — via a FAKE psycopg (no live DB, no
driver install) — prove the real pgvector SQL path: schema DDL, upsert, and
DB-side nearest-neighbour recall (ORDER BY embedding <=> :q).
"""
from __future__ import annotations

import json

import pytest

from connectors.base import Acl, SourceRecord
from core import store as store_mod


class MemoryBackend:
    """A StoreBackend that keeps rows in a dict — hermetic, no filesystem."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def read_rows(self) -> dict[str, dict]:
        return dict(self.rows)

    def write_rows(self, rows: dict[str, dict]) -> None:
        self.rows = dict(rows)


def _rec(source="github", ext="1", comp="gateway"):
    return SourceRecord(source=source, external_id=ext, kind="pr",
                        title=f"PR {ext}", body="body", components=[comp],
                        acl=Acl(visibility="internal"))


def test_recordstore_works_over_an_injected_backend():
    be = MemoryBackend()
    st = store_mod.RecordStore(backend=be)
    r = _rec(ext="7")
    st.put(r, {"summary": "s7"})
    assert st.count() == 1
    assert st.all()[0].external_id == "7"
    assert st.item_for(r)["summary"] == "s7"
    # the bytes really live in the injected backend, not on disk
    assert "github:7" in be.rows


def test_put_is_upsert_by_key():
    be = MemoryBackend()
    st = store_mod.RecordStore(backend=be)
    st.put(_rec(ext="1"), {"summary": "a"})
    st.put(_rec(ext="1"), {"summary": "b"})   # same key -> overwrite, not append
    assert st.count() == 1
    assert st.item_for(_rec(ext="1"))["summary"] == "b"


def test_count_by_source():
    be = MemoryBackend()
    st = store_mod.RecordStore(backend=be)
    st.put(_rec(source="github", ext="1"), {})
    st.put(_rec(source="slack", ext="2"), {})
    st.put(_rec(source="slack", ext="3"), {})
    assert st.count_by_source() == {"github": 1, "slack": 2}


def test_quarantined_rows_are_hidden_from_all_and_count():
    be = MemoryBackend()
    st = store_mod.RecordStore(backend=be)
    st.put(_rec(ext="q"), {}, quarantined=True)
    st.put(_rec(ext="ok"), {})
    assert st.count() == 1
    assert [r.external_id for r in st.all()] == ["ok"]
    assert len(st.all(include_quarantined=True)) == 2


def test_default_backend_is_jsonl(monkeypatch):
    monkeypatch.delenv("SHADOW_STORE_BACKEND", raising=False)
    assert isinstance(store_mod._make_backend(), store_mod.JsonlBackend)


def test_jsonl_backend_does_not_support_vector_recall():
    be = store_mod.JsonlBackend()
    assert be.supports_vector() is False
    # RecordStore surfaces the same answer, so core.retrieve falls back to scan.
    assert store_mod.RecordStore(backend=MemoryBackend()).supports_vector() is False


def test_pgvector_selected_but_unprovisioned_raises_clearly(monkeypatch):
    # no DATABASE_URL -> selecting pgvector must fail loudly on first use.
    monkeypatch.setenv("SHADOW_STORE_BACKEND", "pgvector")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    be = store_mod._make_backend()
    assert isinstance(be, store_mod.PgVectorBackend)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        be.read_rows()


def test_unknown_backend_falls_back_to_jsonl(monkeypatch):
    monkeypatch.setenv("SHADOW_STORE_BACKEND", "banana")
    assert isinstance(store_mod._make_backend(), store_mod.JsonlBackend)


# --------------------------------------------------------------------------- #
# pgvector backend — hermetic SQL-path test via a FAKE psycopg (no live DB,
# no psycopg install). Proves the DDL, the upsert, and the DB-side ANN query
# all fire with the right shape, so the scale path is real, not a stub.
# --------------------------------------------------------------------------- #
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._result: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))
        s = " ".join(sql.split()).lower()
        if "select key, row from shadow_records" in s:
            self._result = [(k, json.dumps(r)) for k, r in self._conn.table.items()]
        elif "order by embedding <=>" in s:
            # emulate ANN: return stored rows (ordering is the DB's job; the test
            # asserts the query SHAPE, the real ordering is pgvector's).
            self._result = [(json.dumps(r),) for r in self._conn.table.values()]
        elif s.startswith("insert into shadow_records"):
            key, _source, row_json = params[0], params[1], params[2]
            self._conn.table[key] = json.loads(row_json)

    def fetchall(self):
        return self._result


class _FakeConn:
    def __init__(self):
        self.table: dict[str, dict] = {}
        self.executed: list = []

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        pass


class _FakePsycopg:
    """Stands in for the psycopg module. The single connection it hands back is
    shared so inserts in one call are visible to selects in the next."""

    def __init__(self):
        self.conn = _FakeConn()

    def connect(self, dsn, autocommit=False):
        return self.conn


@pytest.fixture
def pgvector_store(monkeypatch):
    """A RecordStore over PgVectorBackend wired to a fake psycopg + fake embed."""
    import sys
    fake = _FakePsycopg()
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setenv("SHADOW_STORE_BACKEND", "pgvector")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/db")
    # deterministic embedding so no Bedrock call happens
    from core import embeddings
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed", lambda text: [0.1] * store_mod.EMBED_DIM)
    be = store_mod.PgVectorBackend()
    return store_mod.RecordStore(backend=be), fake


def test_pgvector_put_creates_schema_and_upserts(pgvector_store):
    st, fake = pgvector_store
    st.put(_rec(ext="42"), {"summary": "s42"})
    executed = [sql for sql, _ in fake.conn.executed]
    # schema is created idempotently on first connect
    assert any("create extension if not exists vector" in s.lower() for s in executed)
    assert any("create table if not exists shadow_records" in s.lower() for s in executed)
    assert any("vector(1024)" in s.lower() for s in executed)
    assert any("ivfflat" in s.lower() for s in executed)
    # the row landed via INSERT ... ON CONFLICT (upsert)
    assert any("on conflict (key) do update" in s.lower() for s in executed)
    assert "github:42" in fake.conn.table
    # the facade reads it back through read_rows
    assert st.count() == 1
    assert st.item_for(_rec(ext="42"))["summary"] == "s42"


def test_pgvector_supports_vector_and_recall_runs_in_the_db(pgvector_store):
    st, fake = pgvector_store
    assert st.supports_vector() is True
    st.put(_rec(ext="1", comp="gateway"), {"summary": "a"})
    st.put(_rec(ext="2", comp="gateway"), {"summary": "b"})
    hits = st.vector_recall([0.1] * store_mod.EMBED_DIM, k=5, sources=["github"])
    # recall returns SourceRecords, projected from the DB rows
    assert {h.external_id for h in hits} == {"1", "2"}
    # the ranking query ran in Postgres, not Python
    recall_sql = [sql for sql, _ in fake.conn.executed if "order by embedding <=>" in sql.lower()]
    assert recall_sql, "expected a DB-side ANN query (ORDER BY embedding <=> :q)"
    assert "limit" in recall_sql[-1].lower()
    # the source filter was pushed into the query too
    assert "source = any" in recall_sql[-1].lower()


def test_retrieve_uses_db_recall_when_backend_supports_vector(monkeypatch, pgvector_store):
    """End-to-end: core.retrieve must call the backend's vector_recall (not
    store.all) when the backend supports it — proving the seam is load-bearing."""
    from core import embeddings, retrieve
    st, _fake = pgvector_store
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed", lambda text: [0.1] * store_mod.EMBED_DIM)

    called = {"vector": 0, "all": 0}
    real_vr = st.vector_recall
    real_all = st.all
    monkeypatch.setattr(st, "vector_recall",
                        lambda *a, **k: (called.__setitem__("vector", called["vector"] + 1), real_vr(*a, **k))[1])
    monkeypatch.setattr(st, "all",
                        lambda *a, **k: (called.__setitem__("all", called["all"] + 1), real_all(*a, **k))[1])

    st.put(_rec(ext="1", comp="gateway"), {"summary": "a"})
    retrieve.recall("gateway pricing", principal="u1@x", store=st, limit=3)
    assert called["vector"] >= 1, "retrieve should use DB-side vector_recall"
    assert called["all"] == 0, "retrieve should NOT scan all() when the DB can rank"
