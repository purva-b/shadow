"""Wire source-specific hooks into the core.

Kept separate so the layering stays clean: connectors never import core, and
core.acl stays source-agnostic. The server imports this once at startup to
register each source's live permission probe.

Box permissions are per-asker (collaborations), not a static allow-list, so a
restricted Box record is gated by a LIVE can_read probe rather than a frozen
allow list captured at ingest. Every other connector's Acl is a static
allow-list and needs no probe.
"""
from __future__ import annotations

from . import acl, identity


def _box_can_read(external_id: str, principal: str) -> bool:
    """Resolve the principal back to a Box App User id and probe can_read.
    principal is canonical (e.g. priya@payments.example); Box wants the user id.
    """
    from agents.common import ids
    from shadow_box import tools as T

    users = ids().get("users", {})
    # map canonical principal -> App User id by first-name / email-local match
    local = identity.resolve(principal).split("@")[0].lower()
    uid = None
    for name, user_id in users.items():
        if name.split()[0].lower() == local:
            uid = user_id
            break
    if not uid:
        return False
    try:
        return T.can_read(external_id, str(uid))
    except Exception:
        return False


def install() -> None:
    """Register every source probe. Idempotent."""
    acl.register_probe("box", _box_can_read)
