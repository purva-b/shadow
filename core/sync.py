"""Sync a connector's records into the store.

sync(connector_name) runs the connector's discover -> fetch -> ingest_record
loop, persisting every normalized record so retrieval can answer across it.
This is what the /api/sync/<connector> endpoint and the poll scheduler call.
"""
from __future__ import annotations

import connectors

from . import embeddings
from . import ingest as core_ingest
from .store import store


def sync(connector_name: str, limit: int = 50, **conn_kwargs) -> dict:
    """Pull up to `limit` items from a connector into the store."""
    if connector_name not in connectors.available():
        return {"ok": False, "error": f"unknown connector {connector_name!r}",
                "available": connectors.available()}
    conn = connectors.load(connector_name, **conn_kwargs)

    ingested, embedded, errors = 0, 0, []
    try:
        refs = list(conn.discover())[:limit]
    except Exception as e:
        return {"ok": False, "error": f"discover failed: {e}"}

    for ref in refs:
        try:
            record = conn.fetch(ref)
            core_ingest.ingest_record(record, store=store)
            ingested += 1
            # PRE-EMBED at sync time: warm the embedding cache now so the first
            # question over these records is fast (no per-query embed latency).
            if embeddings.available():
                if embeddings.embed(f"{record.title}\n{record.body}") is not None:
                    embedded += 1
        except Exception as e:
            errors.append({"ref": getattr(ref, "external_id", "?"), "error": str(e)})

    return {"ok": True, "connector": connector_name, "ingested": ingested,
            "embedded": embedded, "errors": errors[:5], "store_total": store.count(),
            "by_source": store.count_by_source()}
