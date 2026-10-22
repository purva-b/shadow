"""Identity resolution — one human, many handles.

`priya@payments.example` (Box), `@priya`/`U024` (Slack), `pnair` (GitHub) and
`Priya Nair` (a calendar invite) are the same person. This resolver collapses
any source handle to one canonical principal, so permissions (Acl.admits) and
people-cards stay correct across every connector.

The map is per-workspace config (config/workspace.yaml `identities:`); with no
config it degrades to identity (a handle resolves to itself), so the Box-only
path keeps working unchanged.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CONFIG = ROOT / "config" / "workspace.yaml"


@lru_cache(maxsize=1)
def _identity_table() -> dict[str, str]:
    """Flat map: every known handle/alias (lowercased) -> canonical principal."""
    table: dict[str, str] = {}
    path = Path(os.environ.get("SHADOW_WORKSPACE_CONFIG", str(_CONFIG)))
    if not path.exists():
        return table
    try:
        import yaml  # optional dep; absent -> identity mapping
    except Exception:
        return table
    try:
        cfg = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return table
    for ent in cfg.get("identities", []) or []:
        canonical = ent.get("canonical")
        if not canonical:
            continue
        table[canonical.lower()] = canonical
        # every per-source handle maps to the canonical principal
        for key, val in ent.items():
            if key in ("canonical", "aliases"):
                continue
            if isinstance(val, str) and val:
                table[val.lower()] = canonical
        for alias in ent.get("aliases", []) or []:
            if isinstance(alias, str) and alias:
                table[alias.lower()] = canonical
    return table


def resolve(handle: str) -> str:
    """Map one source handle/alias/email/name to its canonical principal.
    Unknown handles resolve to themselves (trimmed), so nothing is silently
    dropped and the no-config path is a no-op."""
    if not handle:
        return ""
    h = handle.strip()
    return _identity_table().get(h.lower(), h)


def resolve_all(handles) -> list[str]:
    """Resolve a list/comma-string of handles to a de-duplicated principal list,
    preserving first-seen order."""
    if isinstance(handles, str):
        handles = list(handles.split(","))
    out: list[str] = []
    for h in handles:
        r = resolve(h)
        if r and r not in out:
            out.append(r)
    return out


def _reset_cache() -> None:  # test helper
    _identity_table.cache_clear()
