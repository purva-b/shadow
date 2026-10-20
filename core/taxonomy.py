"""Component taxonomy — question words -> components, and component ranking.

Workspace-defined in config/workspace.yaml `taxonomy:`. This replaces the three
copies of the component keyword map / priority order that used to live inline in
agents/answer.py, agents/ingest.py and core/answer_connectors.py. A different
team re-routes its questions by editing the config — no code change.

With no config (or no yaml), it degrades to a safe built-in default so the
Box-only path keeps working unchanged.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CONFIG = ROOT / "config" / "workspace.yaml"

# Built-in fallback — used only when config is absent/unreadable, so nothing
# hard-fails. These are the historical defaults the inline maps carried.
_DEFAULT_KEYWORDS: dict[str, str] = {
    "retry": "retry-service",
    "retries": "retry-service",
    "idempoten": "retry-service",
    "gateway": "gateway-client",
    "ledger": "ledger",
    "checkout": "checkout",
    "payment": "payments-api",
}
_DEFAULT_PRIORITY: list[str] = [
    "retry-service",
    "gateway-client",
    "ledger",
    "payments-api",
    "checkout",
]


def _config_path() -> Path:
    return Path(os.environ.get("SHADOW_WORKSPACE_CONFIG", str(_CONFIG)))


@lru_cache(maxsize=1)
def _taxonomy() -> tuple[dict[str, str], list[str]]:
    """(keywords, priority) from config, else the built-in default."""
    path = _config_path()
    if not path.exists():
        return dict(_DEFAULT_KEYWORDS), list(_DEFAULT_PRIORITY)
    try:
        import yaml  # optional dep; absent -> default
    except Exception:
        return dict(_DEFAULT_KEYWORDS), list(_DEFAULT_PRIORITY)
    try:
        cfg = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return dict(_DEFAULT_KEYWORDS), list(_DEFAULT_PRIORITY)
    tax = cfg.get("taxonomy") or {}
    keywords = {str(k).lower(): str(v) for k, v in (tax.get("keywords") or {}).items()}
    priority = [str(c) for c in (tax.get("priority") or [])]
    if not keywords:
        keywords = dict(_DEFAULT_KEYWORDS)
    if not priority:
        # fall back to the components list order, else the built-in default
        priority = [str(c) for c in (cfg.get("components") or [])] or list(_DEFAULT_PRIORITY)
    return keywords, priority


def components_from(question: str) -> list[str]:
    """Infer components mentioned in a question, in config priority order."""
    keywords, priority = _taxonomy()
    q = (question or "").lower()
    found: list[str] = []
    for frag, comp in keywords.items():
        if frag in q and comp not in found:
            found.append(comp)
    return sort_by_priority(found)


def sort_by_priority(components) -> list[str]:
    """Sort components by the workspace priority (unknown ones sort last,
    stable within the unknown group)."""
    _, priority = _taxonomy()
    rank = {c: i for i, c in enumerate(priority)}
    seen: list[str] = []
    for c in components:
        if c not in seen:
            seen.append(c)
    return sorted(seen, key=lambda c: rank.get(c, len(priority)))


def priority() -> list[str]:
    """The configured component priority order."""
    return list(_taxonomy()[1])


@lru_cache(maxsize=1)
def components() -> list[str]:
    """The workspace's declared component list (config `components:`), else the
    taxonomy priority order as a reasonable stand-in."""
    path = _config_path()
    if path.exists():
        try:
            import yaml

            cfg = yaml.safe_load(path.read_text()) or {}
            comps = [str(c) for c in (cfg.get("components") or [])]
            if comps:
                return comps
        except Exception:
            pass
    return list(_taxonomy()[1])


@lru_cache(maxsize=1)
def canonical_domain() -> str:
    """The workspace email domain (config `workspace.canonical_domain`), used to
    build display emails without hardcoding 'payments.example'. Empty if unset."""
    path = _config_path()
    if path.exists():
        try:
            import yaml

            cfg = yaml.safe_load(path.read_text()) or {}
            return str((cfg.get("workspace") or {}).get("canonical_domain") or "")
        except Exception:
            pass
    return ""


def _reset_cache() -> None:  # test helper
    _taxonomy.cache_clear()
    components.cache_clear()
    canonical_domain.cache_clear()
