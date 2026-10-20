"""Create the per-agent Box identities, scope their folder access, and PROVE the
least-privilege boundary (Week 2 "Done when").

Runs against the live Box tenant using the existing ids.json folders. Idempotent:
re-running reuses existing agent users and re-applies collaborations.

    python scripts/setup_agents.py

The proof at the end: the Scribe (whose write scope is ONLY Memory/Meeting Notes)
attempts to write metadata to a Decisions file. Box must reject it. If Box
ALLOWS it, the boundary is broken and the script exits non-zero.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from seed import setup_box as S  # noqa: E402
from shadow_box import tools as T  # noqa: E402

IDS = json.loads((ROOT / "seed" / "ids.json").read_text())


def main() -> int:
    folders = IDS["folders"]
    ids = {"folders": folders}

    print("creating agent identities...")
    agents = S.ensure_agent_users()
    print("applying scoped permissions...")
    S.apply_agent_permissions(ids, agents)

    # persist agent_users back into ids.json so ingest can act as them
    IDS["agent_users"] = agents
    (ROOT / "seed" / "ids.json").write_text(json.dumps(IDS, indent=2))
    print(f"wrote {len(agents)} agent users to ids.json")

    # --- the boundary proof ---
    scribe = agents.get("Shadow Scribe")
    dec_folder = folders.get("Memory/Decisions")
    if not scribe or not dec_folder:
        print("SKIP proof: missing scribe or Decisions folder")
        return 0

    # find a decision file the Scribe should NOT be able to write
    dec_file = None
    for it in T.folder_items(dec_folder):
        if it.get("type") == "file":
            dec_file = it["id"]
            break
    if not dec_file:
        print("SKIP proof: no decision file to test against")
        return 0

    # PROOF 1 (write): Scribe writes a Decisions file AS ITSELF -> Box must refuse.
    print(f"PROOF(write): Scribe ({scribe}) writes Decisions file {dec_file} as itself...")
    write_denied = False
    try:
        T.apply_metadata(dec_file, "shadowDecision", {"confirmed": "pending"},
                         as_user=scribe)
        print("  write SUCCEEDED — boundary BROKEN")
    except Exception as e:
        write_denied = True
        print(f"  write DENIED by Box (expected): {str(e)[:80]}")

    # PROOF 2 (read): Scribe reads a Decisions file -> Box must refuse.
    can = T.can_read(dec_file, scribe)
    print(f"PROOF(read): Scribe can_read a Decisions file: {can}  (expected False)")

    if can or not write_denied:
        print("BOUNDARY BROKEN: Scribe reached Decisions outside its role.")
        return 1
    print("BOUNDARY OK: Box denies the Scribe both read and write on Decisions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
