"""Week 3 validation: prove the answer path is tenant-agnostic (no hardcoded
names) and that component routing is config-driven.

These use entirely NON-demo names and components (nothing Payments-specific), so
a pass means nothing in the runtime path is coded to the seeded tenant.
"""
from __future__ import annotations

from agents.answer import _seniority_rank, _who_to_ask
from core import taxonomy


def _card(name, role, expertise, active="yes", email=None, **extra):
    return {
        "name": name, "role": role, "team": "Widgets",
        "expertise": expertise, "active": active,
        "email": email or f"{name.split()[0].lower()}@widgets.test",
        **extra,
    }


def test_who_to_ask_ranks_by_data_not_name_list():
    """A made-up roster the code has never seen: the senior owner of the
    queried component must win, purely from role + expertise data."""
    cards = [
        _card("Robin Vale", "Engineer", ["search-index"]),
        _card("Kai Mori", "Senior Engineer", ["search-index", "billing"]),
        _card("Jess Park", "Engineer", ["billing"]),
    ]
    who = _who_to_ask(["search-index"], cards, org={}, cited_people="")
    assert who is not None
    # Kai owns search-index AND is senior -> wins over Robin (also owns it,
    # but junior). No name is hardcoded; the verdict is data-driven.
    assert who["name"] == "Kai Mori"


def test_who_to_ask_expertise_match_count_breaks_ties():
    """Among same-seniority owners, the one owning MORE of the question's
    components ranks first."""
    cards = [
        _card("Ana Flores", "Engineer", ["auth"]),
        _card("Ben Cole", "Engineer", ["auth", "sessions"]),
    ]
    who = _who_to_ask(["auth", "sessions"], cards, org={}, cited_people="")
    assert who is not None
    assert who["name"] == "Ben Cole"  # owns both -> higher overlap


def test_moved_note_reads_from_card_metadata_not_hardcoded_date():
    """The moved-note's destination + date come from the card, never a literal."""
    cards = [
        _card("Dylan Reed", "Senior Engineer", ["auth"]),  # active owner -> who
        _card("Omar Vance", "Engineer", ["auth"], active="no",
              email="omar@widgets.test", movedTo="Platform", movedOn="2026-01-15"),
    ]
    who = _who_to_ask(["auth"], cards, org={}, cited_people="omar@widgets.test")
    assert who is not None
    assert who["moved_note"] is not None
    assert "Platform" in who["moved_note"]   # destination from card
    assert "2026-01-15" in who["moved_note"]  # date from card, not code


def test_seniority_rank_from_role_title():
    assert _seniority_rank("Engineering Manager") < _seniority_rank("Senior Engineer")
    assert _seniority_rank("Senior Engineer") < _seniority_rank("Engineer")
    assert _seniority_rank("Staff Engineer") < _seniority_rank("Senior Engineer")


def test_taxonomy_routing_is_config_driven(tmp_path, monkeypatch):
    """Point the taxonomy at a throwaway config with INVENTED components +
    keywords; component inference must follow it with zero code change."""
    cfg = tmp_path / "workspace.yaml"
    cfg.write_text(
        "components: [search-index, billing]\n"
        "taxonomy:\n"
        "  keywords:\n"
        "    lookup: search-index\n"
        "    invoice: billing\n"
        "  priority: [billing, search-index]\n"
    )
    monkeypatch.setenv("SHADOW_WORKSPACE_CONFIG", str(cfg))
    taxonomy._reset_cache()
    try:
        # a question using the INVENTED keywords routes to the invented components
        assert taxonomy.components_from("how does lookup work?") == ["search-index"]
        # priority order from config: billing before search-index
        both = taxonomy.components_from("invoice and lookup")
        assert both == ["billing", "search-index"]
        assert taxonomy.components() == ["search-index", "billing"]
    finally:
        monkeypatch.delenv("SHADOW_WORKSPACE_CONFIG", raising=False)
        taxonomy._reset_cache()


def test_taxonomy_degrades_without_config(tmp_path, monkeypatch):
    """No config file -> built-in default keywords still work (no hard-fail)."""
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("SHADOW_WORKSPACE_CONFIG", str(missing))
    taxonomy._reset_cache()
    try:
        assert taxonomy.components_from("retry logic") == ["retry-service"]
    finally:
        monkeypatch.delenv("SHADOW_WORKSPACE_CONFIG", raising=False)
        taxonomy._reset_cache()
