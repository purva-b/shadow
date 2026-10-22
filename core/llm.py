"""LLM orchestration layer — Strands Agents over Bedrock.

Week 4 moved this out of `agents/common.py` so `core/` depends on the reasoning
layer directly instead of importing the `agents` package. Every agent's LLM step
runs through a Strands `Agent` backed by a Strands `BedrockModel`; raw boto3
remains only as a last-resort fallback so the system never hard-fails if the
Strands runtime is unavailable offline.

`agents/common.py` now re-exports these names, so existing imports keep working.
Nothing here is Box- or team-specific.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _strands_model():
    from strands.models import BedrockModel

    return BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        temperature=0.2,
    )


def strands_agent(system: str = "", tools: list | None = None):
    """Build a Strands Agent bound to the Bedrock model. `tools` is a list of
    @tool-decorated callables the agent may invoke during reasoning."""
    from strands import Agent

    return Agent(model=_strands_model(), system_prompt=system or
                 "You are Shadow, a team knowledge agent. Be precise and cite "
                 "only what the sources support.", tools=tools or [])


def agent_complete(prompt: str, system: str = "", tools: list | None = None) -> str:
    """Run one turn through a Strands Agent (the orchestrator). Falls back to a
    direct Bedrock call only if Strands is unavailable at runtime."""
    try:
        agent = strands_agent(system=system, tools=tools)
        return str(agent(prompt)).strip()
    except Exception:
        return _bedrock_complete_raw(prompt, system=system)


STRANDS_AVAILABLE = True
try:  # record whether the orchestration layer is importable (for /health, UI)
    import strands as _strands  # noqa: F401
except Exception:  # pragma: no cover
    STRANDS_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Bedrock (raw) — fallback path only
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _bedrock():
    import boto3

    return boto3.client("bedrock-runtime",
                        region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _bedrock_complete_raw(prompt: str, system: str = "", max_tokens: int = 800,
                          temperature: float = 0.2) -> str:
    """Single-shot Claude completion via raw Bedrock invoke (fallback)."""
    model_id = os.environ["BEDROCK_MODEL_ID"]
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    resp = _bedrock().invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    payload = json.loads(resp["body"].read())
    parts = payload.get("content", [])
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()


def bedrock_complete(prompt: str, system: str = "", max_tokens: int = 800,
                     temperature: float = 0.2) -> str:
    """Primary reasoning entry point — routes through the Strands orchestrator."""
    return agent_complete(prompt, system=system)


def bedrock_json(prompt: str, system: str = "", max_tokens: int = 800) -> dict:
    """Ask Bedrock for JSON; tolerate code fences."""
    txt = bedrock_complete(prompt, system=system, max_tokens=max_tokens)
    txt = txt.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        if txt.startswith("json"):
            txt = txt[4:]
        txt = txt.strip("` \n")
    try:
        return json.loads(txt)
    except Exception:
        start, end = txt.find("{"), txt.rfind("}")
        if start >= 0 and end > start:
            return json.loads(txt[start:end + 1])
        raise
