"""Pytest entry for the project's two harnesses.

The harnesses (scripts/eval_golden.py, scripts/test_phase1.py) are standalone,
hermetic scripts that set up their own throwaway config and `sys.exit(0/1)`.
Rather than rewrite them, we run each as a subprocess and assert a clean exit —
so `pytest` (and CI) cover them, and they stay runnable on their own.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_golden_eval():
    """Retrieval + the per-asker permission boundary (keyword lane, offline)."""
    r = _run("eval_golden.py")
    assert r.returncode == 0, f"golden eval failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL GOLDEN PASS" in r.stdout


def test_phase1_interface():
    """Connector interface + identity resolution + ACL filter."""
    r = _run("test_phase1.py")
    assert r.returncode == 0, f"phase1 failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL PASS" in r.stdout
