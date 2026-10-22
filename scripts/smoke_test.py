"""Checkpoint-1 smoke test — validates the live Box environment.

    python scripts/smoke_test.py

Checks (section 12):
 1. Service account token works; root folder listed.
 2. Metadata applied and read back on one file.
 3. Metadata query returns the PAY-482 items.
 4. Box AI Ask works AS Alex (App User) over 2 files with citations.
 5. Alex is denied a file in Restricted/; Priya can read it.
 6. Task created and assigned to Dana on the decision file.
 7. Embed link returned for a cited file as Alex.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shadow_box import tools as T  # noqa: E402
from shadow_box.client import get_service_client  # noqa: E402

IDS = json.loads((ROOT / "seed" / "ids.json").read_text())
FID = IDS["file_ids"]
USERS = IDS["users"]
ROOT_FOLDER = IDS["root_folder_id"]

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main():
    print("Shadow smoke test")

    # 1 service account + root listing
    try:
        me = get_service_client().users.get_user_me()
        items = T.folder_items(ROOT_FOLDER)
        check("1 service account + root listing",
              me.id and len(items) >= 3, f"{me.name}, {len(items)} root items")
    except Exception as e:
        check("1 service account + root listing", False, str(e))

    # 2 metadata read-back on PAY-482
    pay482 = FID["Issues/PAY-482.md"]
    md = T.get_metadata(pay482, "shadowItem")
    check("2 metadata read-back", bool(md) and md.get("issueIds") == "PAY-482",
          f"issueIds={md.get('issueIds') if md else None}")

    # 3 metadata query for PAY-482 items
    q = T.metadata_query(
        "shadowItem", "issueIds LIKE :iss", {"iss": "%PAY-482%"},
        ROOT_FOLDER, fields=["title", "sourceType", "issueIds"], limit=50)
    check("3 metadata query PAY-482", len(q) >= 3, f"{len(q)} items matched")

    # 4 Box AI Ask AS Alex over 2 files with citations
    alex = USERS["Alex Rivera"]
    two = [FID["Issues/PAY-482.md"], FID["Docs/retry-idempotency-design.md"]]
    ans = T.ai_ask(two, "What is PAY-482 about and what is the proposed fix?",
                   as_user=alex)
    has_ans = bool(ans.get("answer"))
    has_cit = len(ans.get("citations", []) or []) >= 1
    check("4 Box AI Ask as Alex w/ citations", has_ans and has_cit,
          f"answer={len(ans.get('answer',''))} chars, "
          f"{len(ans.get('citations',[]) or [])} citations"
          + (f", err={ans.get('error','')[:80]}" if ans.get("error") else ""))

    # 5 restricted permission: Alex denied, Priya allowed
    restr = FID["Restricted/2026-10-14-design-review.txt"]
    alex_can = T.can_read(restr, alex)
    priya_can = T.can_read(restr, USERS["Priya Nair"])
    check("5 restricted perms (Alex denied, Priya ok)",
          (not alex_can) and priya_can,
          f"alex_can_read={alex_can}, priya_can_read={priya_can}")

    # 6 task on decision file assigned to Dana
    dec = FID["Memory/Decisions/retry-idempotency-keys.md"]
    try:
        existing = T.list_task_assignments(dec)
        if existing:
            check("6 task assigned to Dana", True,
                  f"{len(existing)} existing assignment(s)")
        else:
            res = T.create_task_and_assign(
                dec, "SMOKE TEST: confirm decision?", USERS["Dana Kim"])
            check("6 task assigned to Dana", bool(res.get("assignment_id")),
                  f"assignment {res.get('assignment_id')}")
    except Exception as e:
        check("6 task assigned to Dana", False, str(e))

    # 7 embed link as Alex for a readable cited file
    link = T.embed_link(pay482, as_user=alex)
    check("7 embed link as Alex", bool(link),
          "got expiring_embed_link" if link else "no link")

    npass = sum(1 for _, ok, _ in results if ok)
    print(f"\n{npass}/{len(results)} checks passed")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
