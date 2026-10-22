"""Ingest idempotency (brief #8, Week 2 'Done when' part 2).

A webhook can fire more than once for the same file version. ingest() must
process a given version exactly once: the 2nd..Nth replay is a no-op. We prove
this hermetically by faking the Box layer — no live tenant needed — and counting
how many times the real work (classify/scribe/decision/cartographer) runs across
5 replays of the same file version.
"""
from __future__ import annotations

import importlib


def test_replaying_the_same_version_runs_the_work_once(monkeypatch):
    ing = importlib.import_module("agents.ingest")

    # a tiny in-memory stand-in for the file's shadowPipeline metadata
    pipeline_md: dict = {}
    work_runs = {"classify": 0, "scribe": 0, "decision": 0, "cartographer": 0,
                 "marked": 0}

    # --- fake the Box + agent calls ingest() touches ---
    monkeypatch.setattr(ing.T, "file_version", lambda fid, *a, **k: "v1")

    def fake_get_md(fid, template, *a, **k):
        return dict(pipeline_md) if template == "shadowPipeline" else {}
    monkeypatch.setattr(ing.T, "get_metadata", fake_get_md)

    def fake_apply(fid, template, values, *a, **k):
        if template == "shadowPipeline":
            pipeline_md.update(values)
            work_runs["marked"] += 1
    monkeypatch.setattr(ing.T, "apply_metadata", fake_apply)
    monkeypatch.setattr(ing, "_download_text", lambda fid: "Host: Dana\nwe shipped it")
    monkeypatch.setattr(ing, "_file_name", lambda fid: "standup.txt")
    monkeypatch.setattr(ing, "log_activity", lambda *a, **k: None)

    def fake_classify(fid, text):
        work_runs["classify"] += 1
        return {"stop": False, "metadata": {"sourceType": "meeting",
                "components": ["retry-service"], "people": ""}, "sensitivity": "normal"}
    monkeypatch.setattr(ing, "librarian_classify", fake_classify)

    def fake_scribe(fid, text, md):
        work_runs["scribe"] += 1
        return {"note_file_id": "n1"}
    monkeypatch.setattr(ing, "scribe_note", fake_scribe)

    def fake_decision(fid, text, md):
        work_runs["decision"] += 1
        return {"changed": False}
    monkeypatch.setattr(ing, "librarian_decision", fake_decision)

    def fake_carto(md):
        work_runs["cartographer"] += 1
        return {"updated": []}
    monkeypatch.setattr(ing, "cartographer_update", fake_carto)

    # --- replay the SAME file (version v1) five times ---
    results = [ing.ingest("file-123") for _ in range(5)]

    # exactly ONE real run; the other four short-circuited
    assert work_runs["classify"] == 1, work_runs
    assert work_runs["scribe"] == 1, work_runs
    assert work_runs["decision"] == 1, work_runs
    assert work_runs["cartographer"] == 1, work_runs
    # 4 of the 5 calls reported a skip
    assert sum(1 for r in results if r.get("skipped")) == 4, results
