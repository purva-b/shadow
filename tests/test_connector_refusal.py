"""Connector-path refusal parity (Week 4).

When a question's only matching source is a connector record the asker cannot
read, the connector answer path must REFUSE fully — and, like the Box pipeline,
point the asker at a readable owner — WITHOUT leaking anything about the
restricted record (its title, body, or the fact of its specific contents).

Hermetic: an isolated on-disk store + a mocked people-cards lookup, so no live
Box tenant or network is touched.
"""
from __future__ import annotations

import importlib

import pytest

from connectors.base import Acl, SourceRecord


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """A RecordStore backed by a throwaway file, with one restricted record that
    only 'owner@acme.com' may read."""
    store_path = tmp_path / "records.jsonl"
    monkeypatch.setenv("SHADOW_RECORD_STORE", str(store_path))
    # embeddings off -> deterministic keyword lane only
    monkeypatch.setenv("SHADOW_EMBEDDINGS", "0")

    from core import store as store_mod
    importlib.reload(store_mod)
    st = store_mod.RecordStore()

    rec = SourceRecord(
        source="confluence", external_id="SEC-1", kind="doc",
        title="Vendor pricing contract terms",
        body="confidential gateway vendor pricing and contract terms",
        occurred_on="2026-02-01", people=["owner@acme.com"],
        components=["gateway"], issue_ids=[],
        acl=Acl(visibility="restricted", allow=["owner@acme.com"]),
        url="https://wiki/SEC-1",
    )
    st.put(rec, rec.to_shadow_item())
    return st


def test_connector_refusal_points_to_readable_owner_without_leaking(isolated_store, monkeypatch):
    from core import answer_connectors as ac

    # mock the people cards so the readable-owner pointer resolves without Box
    monkeypatch.setattr(
        "core.structured.people_cards",
        lambda: [{"name": "Dana Owner", "role": "Engineering Manager",
                  "team": "Platform", "expertise": ["gateway"], "active": "yes",
                  "email": "owner@acme.com"}],
    )

    # asker is NOT on the record's allow-list -> must be refused
    out = ac.answer_from_connectors(
        "newhire@acme.com", "gateway vendor pricing contract terms",
        store=isolated_store,
    )
    assert out is not None, "a matching restricted record exists -> must not fall through"
    assert out["citations"] == [], "a full refusal cites nothing"
    md = out["answer_markdown"]

    # it POINTS to a readable owner (parity with the Box pipeline)
    assert "Dana Owner" in md, "refusal should name a readable owner to ask"
    assert out["who_to_ask"] and out["who_to_ask"]["name"] == "Dana Owner"

    # it LEAKS NOTHING about the restricted record itself
    assert "Vendor pricing contract terms" not in md, "leak: restricted title"
    assert "confidential" not in md.lower(), "leak: restricted body"
    assert "SEC-1" not in md, "leak: restricted id"


def test_authorized_asker_gets_the_record(isolated_store, monkeypatch):
    """Control: the owner, who IS on the allow-list, gets an answer (proves the
    refusal is discriminating, not a blanket deny)."""
    from core import answer_connectors as ac

    monkeypatch.setattr("core.structured.people_cards", lambda: [])
    # enrich reaches Box for structured fields; force it to no-op so the test
    # stays hermetic (the readable answer itself does not depend on it).
    monkeypatch.setattr(ac, "_enrich_structured", lambda readable, principal: (None, None))

    out = ac.answer_from_connectors(
        "owner@acme.com", "gateway vendor pricing contract terms",
        store=isolated_store,
    )
    assert out is not None
    assert len(out["citations"]) == 1, "owner can read the one matching record"
    assert out["citations"][0]["name"] == "Vendor pricing contract terms"
