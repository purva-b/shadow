"""Structured answer fields — who-to-ask, decision status, people cards.

This is the source-agnostic home for the structured-field logic that USED to
live in `agents/answer.py` (the Box pipeline). It reads the Box-backed people
cards, org chart and decision records and computes:

  - who_to_ask      ranked by DATA (expertise overlap, seniority from role)
  - decision_status the recorded decision + its confirmation state
  - moved_note       a cited author who left, from their card metadata

Week 4 moved it here so `core/` never imports `agents.answer`. The Box reads go
through `shadow_box.tools` (the only Box-specific dependency, which is correct —
these cards physically live in Box); ids come from `seed/ids.json` directly, so
there is no dependency back on the `agents` package. `agents/answer.py` now
re-exports these names for backward compatibility.

Nothing here is tied to a team or a question: ranking is by role title and
expertise overlap, the move note reads `movedOn`/`movedTo` off the card, and the
component priority comes from `config/workspace.yaml` via `core.taxonomy`.
"""
from __future__ import annotations

import json
import os
import time as _time
from functools import lru_cache
from pathlib import Path

from shadow_box import tools as T

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _ids() -> dict:
    return json.loads((ROOT / "seed" / "ids.json").read_text())


@lru_cache(maxsize=1)
def _enterprise_scope() -> str:
    eid = os.environ.get("BOX_ENTERPRISE_ID", "") or _ids().get("enterprise_id", "")
    return f"enterprise_{eid}"


def md_from_listing(entry: dict, template: str = "shadowItem") -> dict:
    """Read a template's metadata from an inline folder_items entry WITHOUT a
    second HTTP call. folder_items requests fields=...,metadata, so the values
    ride along under metadata[enterprise_<id>][template]."""
    md = entry.get("metadata") or {}
    scoped = md.get(_enterprise_scope()) or {}
    inst = scoped.get(template) or {}
    return {k: v for k, v in inst.items() if not k.startswith("$")}


def unwrap_metadata(entry: dict, template: str) -> dict:
    """metadata_query rows nest values under metadata[enterprise_<id>][template]."""
    md = entry.get("metadata") or {}
    scoped = md.get(_enterprise_scope()) or {}
    inst = scoped.get(template) or {}
    return {k: v for k, v in inst.items() if not k.startswith("$")}


# --------------------------------------------------------------------------- #
# People cards + org chart (hot-path cached; the graph rarely changes)
# --------------------------------------------------------------------------- #
_PC_CACHE: dict = {"t": 0.0, "v": None}


def people_cards() -> list[dict]:
    """All shadowPerson cards (service-account view) from the People folder.
    Cached 60s — on the hot answer path."""
    if _PC_CACHE["v"] is not None and (_time.time() - _PC_CACHE["t"]) < 60:
        return _PC_CACHE["v"]
    folder = _ids()["folders"].get("Memory/People")
    out: list[dict] = []
    if not folder:
        return out
    for it in T.folder_items(folder, md_template="shadowPerson"):
        if it.get("type") != "file":
            continue
        md = md_from_listing(it, "shadowPerson")
        if not md:
            continue
        card = {k: v for k, v in md.items() if not k.startswith("$")}
        card["_file_id"] = it["id"]
        card["_name"] = card.get("name")
        out.append(card)
    _PC_CACHE["t"] = _time.time()
    _PC_CACHE["v"] = out
    return out


_ORG_CACHE: dict = {"t": 0.0, "v": None}


def org_chart() -> dict:
    """The org chart, cached 60s. Reuses the shared Box session."""
    if _ORG_CACHE["v"] is not None and (_time.time() - _ORG_CACHE["t"]) < 60:
        return _ORG_CACHE["v"]
    fid = _ids()["file_ids"].get("Org/org_chart.json")
    if not fid:
        return {}
    try:
        r = T._SESSION.get(f"https://api.box.com/2.0/files/{fid}/content",
                           headers=T._headers(), timeout=30)
        data = json.loads(r.text)
    except Exception:
        data = {}
    _ORG_CACHE["t"] = _time.time()
    _ORG_CACHE["v"] = data
    return data


def clear_caches() -> None:
    """Drop the people/org caches (used by tests and after a people sync)."""
    _PC_CACHE["v"] = None
    _ORG_CACHE["v"] = None


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def decisions(components: list[str], user_id: str) -> list[dict]:
    """List the Decisions folder and read shadowDecision metadata inline (one
    folder listing, not N+1 get_metadata calls). multiSelect fields are not
    reliably queryable, so filter by component in code."""
    folder = _ids()["folders"].get("Memory/Decisions")
    if not folder:
        return []
    out: list[dict] = []
    for it in T.folder_items(folder, as_user=user_id, md_template="shadowDecision"):
        if it.get("type") != "file":
            continue
        md = md_from_listing(it, "shadowDecision")
        if not md:
            continue
        comps = md.get("components", []) or []
        if not components or any(c in comps for c in components):
            out.append({"id": it["id"], "name": it["name"],
                        "metadata": {_enterprise_scope(): {"shadowDecision": md}}})
    return out


def decision_status(decs: list[dict]) -> dict | None:
    for r in decs:
        d = unwrap_metadata(r, "shadowDecision")
        if d:
            return {
                "decision": d.get("decision", ""),
                "status": d.get("status", ""),
                "confirmed": d.get("confirmed", ""),
                "decidedOn": d.get("decidedOn", "")[:10],
            }
    return None


# --------------------------------------------------------------------------- #
# Who-to-ask — ranked by DATA, no hardcoded roster
# --------------------------------------------------------------------------- #
def seniority_rank(role: str) -> int:
    """Lower = more senior. Derived from the role TITLE, not a name list, so it
    works for any team. Ties broken later by expertise-match count."""
    r = (role or "").lower()
    if "manager" in r or "director" in r or "vp" in r or "head" in r:
        return 0
    if "staff" in r or "principal" in r or "lead" in r:
        return 1
    if "senior" in r or "sr" in r:
        return 2
    return 3


def _owns_by_email(org: dict) -> dict[str, set]:
    """Map each person's email -> the set of components they OWN, read from the
    org chart. Ownership is a stronger signal than listing something as
    expertise: the owner is who you should actually ask."""
    out: dict[str, set] = {}
    for team in (org or {}).get("teams", {}).values():
        for m in team.get("members", []):
            email = m.get("email", "")
            if email:
                out[email] = set(m.get("owns") or [])
    return out


def who_to_ask(components: list[str], cards: list[dict], org: dict,
               cited_people: str) -> dict | None:
    """Resolve in component PRIORITY order (components arrives pre-sorted). Among
    candidates for a component, rank by: (1) OWNERSHIP of that component from the
    org chart — an owner outranks a mere expert; (2) expertise-match count;
    (3) seniority from the role title. No hardcoded roster of names."""
    comp_set = set(components or [])
    owns = _owns_by_email(org)

    def rank(c: dict, comp: str) -> tuple[int, int, int]:
        is_owner = comp in owns.get(c.get("email", ""), set())
        overlap = len(set(c.get("expertise") or []) & comp_set)
        return (0 if is_owner else 1, -overlap, seniority_rank(c.get("role", "")))

    active = [c for c in cards if c.get("active") == "yes"]
    best = None
    for comp in components:
        # candidates = anyone who owns OR lists expertise in this component
        owners = [c for c in active
                  if comp in (c.get("expertise") or [])
                  or comp in owns.get(c.get("email", ""), set())]
        if owners:
            owners.sort(key=lambda c: rank(c, comp))
            best = owners[0]
            break
    if best is None:
        for c in active or cards:
            if "manager" in c.get("role", "").lower():
                best = c
                break
    if best is None:
        return None
    reason = (f"Owns {', '.join(best.get('expertise') or [])}."
              if best.get("expertise")
              else f"{best.get('role','')} on the {best.get('team','')} team.")
    moved_note = None
    for c in cards:
        if c.get("active") != "no":
            continue
        if c.get("email", "") not in (cited_people or ""):
            continue
        if comp_set and not (set(c.get("expertise") or []) & comp_set):
            continue
        dest = c.get("movedTo") or "another team"
        when = (c.get("movedOn") or "")[:10]
        when_phrase = f" on {when}" if when else ""
        moved_note = (f"{c.get('name')} wrote earlier material but moved to the "
                      f"{dest} team{when_phrase}.")
        break
    return {
        "name": best.get("name"),
        "role": best.get("role"),
        "reason": reason,
        "moved_note": moved_note,
    }
