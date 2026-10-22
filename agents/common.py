"""Shared helpers for the Shadow agents: ids.json + metadata shapes.

The LLM orchestration layer (Strands Agents over Bedrock) moved to `core.llm`
in Week 4 so the core no longer imports the `agents` package; it is re-exported
here so existing `from agents.common import bedrock_json` / `strands_agent`
imports keep working unchanged.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

# Re-export the reasoning layer from its new source-agnostic home.
from core.llm import (  # noqa: F401
    STRANDS_AVAILABLE,
    agent_complete,
    bedrock_complete,
    bedrock_json,
    strands_agent,
)

ROOT = Path(__file__).resolve().parent.parent

try:  # ensure .env is loaded even when agents run standalone
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


@lru_cache(maxsize=1)
def ids() -> dict:
    return json.loads((ROOT / "seed" / "ids.json").read_text())


@lru_cache(maxsize=1)
def enterprise_scope() -> str:
    eid = os.environ.get("BOX_ENTERPRISE_ID", "") or ids().get("enterprise_id", "")
    return f"enterprise_{eid}"


def unwrap_metadata(entry: dict, template: str) -> dict:
    """metadata_query rows nest values under metadata[enterprise_<id>][template]."""
    md = entry.get("metadata") or {}
    scoped = md.get(enterprise_scope()) or {}
    inst = scoped.get(template) or {}
    # strip Box's $-prefixed system keys
    return {k: v for k, v in inst.items() if not k.startswith("$")}
