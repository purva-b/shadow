"""Agent-path smoke (Week 6).

The fast CI lane deliberately omits the [agents] extra (strands/bedrock), so the
agentic modules are never imported there. This test fills that gap: it is
SKIPPED unless `strands` is installed, and in the dedicated agent-path CI job it
proves the agentic entry points import and wire up — catching an import/API
break in the agent layer that the offline lane cannot see.

It does NOT call Bedrock (no network/creds in CI): it asserts the modules load,
the Strands layer reports available, and the tool lists are non-empty.
"""
from __future__ import annotations

import pytest

strands = pytest.importorskip("strands", reason="agent path: [agents] extra not installed")


def test_core_llm_reports_strands_available():
    from core import llm

    assert llm.STRANDS_AVAILABLE is True
    # the agent builder is callable; we do NOT construct a model here because
    # that reads BEDROCK_MODEL_ID + AWS creds, absent in CI. Import + the
    # availability flag are the smoke signal.
    assert callable(llm.strands_agent)
    assert callable(llm.bedrock_json)


def test_agentic_answer_module_imports_and_exposes_entrypoint():
    from core import agent_answer

    assert hasattr(agent_answer, "answer_agentic")
    assert callable(agent_answer.answer_agentic)
    assert isinstance(agent_answer.is_enabled(), bool)


def test_connector_tools_are_registered():
    from core import tools_connectors

    # the Strands @tool callables the agent orchestrates over
    assert len(tools_connectors.CONNECTOR_TOOLS) >= 1


def test_box_answer_tools_import():
    from agents import tools_strands

    assert len(tools_strands.ANSWER_TOOLS) >= 1
