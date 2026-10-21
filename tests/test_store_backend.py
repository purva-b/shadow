"""Store-backend seam (Week 6).

RecordStore's public API (put/all/item_for/count/count_by_source) must work
over ANY StoreBackend, so the JSON-lines file can be swapped for pgvector
without touching a call site. These tests use an in-memory backend (no disk),
prove the default selection is jsonl, and prove the pgvector stub fails loudly
only when selected + unprovisioned.
"""
from __future__ import annotations

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


def test_pgvector_backend_selected_but_unprovisioned_raises_clearly(monkeypatch):
    monkeypatch.setenv("SHADOW_STORE_BACKEND", "pgvector")
    be = store_mod._make_backend()
    assert isinstance(be, store_mod.PgVectorBackend)
    with pytest.raises(RuntimeError, match="pgvector backend selected"):
        be.read_rows()


def test_unknown_backend_falls_back_to_jsonl(monkeypatch):
    monkeypatch.setenv("SHADOW_STORE_BACKEND", "banana")
    assert isinstance(store_mod._make_backend(), store_mod.JsonlBackend)
