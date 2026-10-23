"""Tests for the two fixes found during end-to-end testing:

  1. sync() must report an UNCONFIGURED connector honestly (ok:false,
     reason:not_configured) rather than a hollow ok:true / ingested:0.
  2. who_to_ask must rank the OWNER of a component above a mere expert — an
     owner from the org chart outranks someone who only lists it as expertise,
     even if that someone is more senior.
"""
from __future__ import annotations

from core import structured
from core.sync import _connector_configured


class _Unconfigured:
    name = "github"
    token = ""
    repo = ""


class _Configured:
    name = "github"
    token = "x"
    repo = "owner/name"


def test_unconfigured_connector_detected():
    assert _connector_configured(_Unconfigured()) is False
    assert _connector_configured(_Configured()) is True


def test_box_is_always_ready():
    class _Box:
        name = "box"
    assert _connector_configured(_Box()) is True


def test_who_to_ask_owner_beats_senior_expert():
    """Priya (Senior, OWNS retry-service) must win over Elena (Staff, only lists
    retry-service as expertise). Ownership is the stronger signal."""
    cards = [
        {"name": "Priya Nair", "email": "priya@x", "role": "Senior Engineer",
         "expertise": ["retry-service", "gateway-client"], "active": "yes"},
        {"name": "Elena Petrova", "email": "elena@x", "role": "Staff Engineer",
         "expertise": ["gateway-client", "retry-service"], "active": "yes"},
    ]
    org = {"teams": {"Payments": {"members": [
        {"name": "Priya Nair", "email": "priya@x", "owns": ["retry-service"]},
        # Elena has no org-chart ownership
    ]}}}
    best = structured.who_to_ask(["retry-service"], cards, org, cited_people="")
    assert best is not None
    assert best["name"] == "Priya Nair"


def test_who_to_ask_falls_back_to_expertise_when_no_owner():
    """With no owner in the org chart, fall back to expertise + seniority — the
    more senior expert wins."""
    cards = [
        {"name": "Junior Dev", "email": "jr@x", "role": "Engineer",
         "expertise": ["checkout"], "active": "yes"},
        {"name": "Senior Dev", "email": "sr@x", "role": "Senior Engineer",
         "expertise": ["checkout"], "active": "yes"},
    ]
    org = {"teams": {}}  # nobody owns anything
    best = structured.who_to_ask(["checkout"], cards, org, cited_people="")
    assert best is not None
    assert best["name"] == "Senior Dev"
