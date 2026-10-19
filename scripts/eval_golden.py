"""Golden eval harness — pin connector behavior so changes can't silently regress.

Each case is a (persona, question, expectation) over the connector retrieval +
ACL path. Expectations cover the behaviors that broke before: correct semantic
ranking, the permission boundary (same question, different person), and clean
handling when nothing is readable. Run on every push.

Hermetic: seeds its own record store + identity map, forces the offline
keyword lane by default so CI needs no AWS; pass --semantic to also exercise
Titan ranking locally.

    .venv/bin/python scripts/eval_golden.py [--semantic]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# hermetic config BEFORE importing core
_tmp = Path(tempfile.mkdtemp())
(_tmp / "workspace.yaml").write_text(
    "identities:\n"
    "  - canonical: priya@acme.com\n    github: pnair\n    aliases: [\"Priya\"]\n"
    "  - canonical: alex@acme.com\n    github: arivera\n    aliases: [\"Alex\"]\n"
)
os.environ["SHADOW_WORKSPACE_CONFIG"] = str(_tmp / "workspace.yaml")
os.environ["SHADOW_RECORD_STORE"] = str(_tmp / "records.jsonl")
os.environ["SHADOW_EMBED_CACHE"] = str(_tmp / "embed.json")
if "--semantic" not in sys.argv:
    # force the offline keyword lane so CI is deterministic + needs no AWS
    os.environ.pop("BEDROCK_MODEL_ID", None)
    os.environ.pop("AWS_REGION", None)

from connectors.base import Acl, SourceRecord          # noqa: E402
from core import retrieve                               # noqa: E402
from core.store import RecordStore                      # noqa: E402

store = RecordStore()

# ---- seed a tiny cross-source corpus -------------------------------------- #
SEED = [
    SourceRecord(source="github", external_id="1201", kind="pr",
        title="PR #1201: checkout flash-sale timeout fix",
        body="Raise the checkout gateway timeout and add a retry budget for PAY-512 flash-sale timeouts.",
        occurred_on="2026-10-06", people=["sortiz"], components=["checkout"],
        issue_ids=["PAY-512"], acl=Acl(visibility="internal"),
        url="https://github.com/acme/pay/pull/1201"),
    SourceRecord(source="github", external_id="1300", kind="pr",
        title="PR #1300: gateway vendor pricing contract terms",
        body="Internal pricing contract terms for the gateway vendor negotiation.",
        occurred_on="2026-10-10", people=["pnair"], components=["gateway-client"],
        issue_ids=[], acl=Acl(visibility="restricted", allow=["pnair"]),
        url="https://github.com/acme/pay/pull/1300"),
    SourceRecord(source="slack", external_id="C1:1", kind="chat",
        title="#payments-oncall thread",
        body="Double charges on retry for PAY-482; idempotency key is the fix.",
        occurred_on="2026-10-09", people=["pnair"], components=["retry-service"],
        issue_ids=["PAY-482"], acl=Acl(visibility="internal"),
        url="https://slack.com/x"),
]
for r in SEED:
    store.put(r, r.to_shadow_item())

# ---- cases ---------------------------------------------------------------- #
# (persona, question, expect) where expect is a dict of assertions
CASES = [
    ("alex", "What was the flash-sale timeout fix?",
     {"lead_contains": "1201", "min_readable": 1}),
    ("alex", "Tell me about the retry double-charge PAY-482",
     {"any_issue": "PAY-482", "min_readable": 1}),
    ("alex", "gateway vendor pricing contract terms",
     {"withheld_contains": "1300", "lead_not": "1300"}),   # Alex can't read #1300
    ("priya", "gateway vendor pricing contract terms",
     {"lead_contains": "1300", "min_readable": 1}),          # Priya can
    ("alex", "capital of France",
     {"max_readable": 0}),                                   # nothing on-topic
]

ok = True


def _ids(recs):
    return [r.external_id for r in recs]


for persona, q, expect in CASES:
    got = retrieve.recall(q, persona, store)
    lead = got.readable[0].external_id if got.readable else ""
    rids = _ids(got.readable)
    wids = _ids(got.withheld)
    passed = True
    if "lead_contains" in expect:
        passed &= expect["lead_contains"] in lead
    if "lead_not" in expect:
        passed &= expect["lead_not"] not in lead
    if "min_readable" in expect:
        passed &= len(got.readable) >= expect["min_readable"]
    if "max_readable" in expect:
        passed &= len(got.readable) <= expect["max_readable"]
    if "withheld_contains" in expect:
        passed &= any(expect["withheld_contains"] in w for w in wids)
    if "any_issue" in expect:
        iss = expect["any_issue"].upper()
        passed &= any(iss in [i.upper() for i in r.issue_ids] for r in got.readable)
    ok &= passed
    print(("PASS" if passed else "FAIL"),
          f"[{persona}] {q!r} -> readable={rids} withheld={wids}")

print("\n" + ("ALL GOLDEN PASS" if ok else "GOLDEN REGRESSION"))
sys.exit(0 if ok else 1)
