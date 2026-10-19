"""Phase 1 end-to-end: a SourceRecord flows connector -> identity -> ingest ->
ACL filter, with no live Box/AWS needed. Run: .venv/bin/python scripts/test_phase1.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# point identity at a throwaway config so the test is hermetic
import tempfile

cfg = Path(tempfile.mkdtemp()) / "workspace.yaml"
cfg.write_text(
    "identities:\n"
    "  - canonical: priya@acme.com\n"
    "    github: pnair\n"
    "    box: priya@payments.example\n"
    "    aliases: [\"Priya Nair\"]\n"
)
os.environ["SHADOW_WORKSPACE_CONFIG"] = str(cfg)
os.environ.pop("BEDROCK_MODEL_ID", None)  # force the offline deterministic path

import connectors
from connectors.base import Acl, SourceRecord
from core import acl, identity, ingest

ok = True


def check(label, cond):
    global ok
    ok = ok and bool(cond)
    print(("PASS" if cond else "FAIL"), label)


# 1. both connectors registered via autoload
check("connectors registered include box+github",
      {"box", "github"} <= set(connectors.available()))

# 2. identity resolves source handles to one canonical principal
check("github handle pnair -> canonical", identity.resolve("pnair") == "priya@acme.com")
check("box handle -> same canonical",
      identity.resolve("priya@payments.example") == "priya@acme.com")
check("unknown handle -> itself", identity.resolve("stranger") == "stranger")

# 3. ingest consumes a SourceRecord (no Box fetch), canonicalizes people
rec = SourceRecord(
    source="github", external_id="482", kind="issue",
    title="Retry double-charge", body="Idempotency key fix for PAY-482.",
    people=["pnair"], components=["retry-service"], issue_ids=["PAY-482"],
    acl=Acl(visibility="internal"),
    url="https://github.com/acme/pay/issues/482",
)
res = ingest.ingest_record(rec)
check("ingest ok", res.get("ok"))
check("ingest canonicalized people", res.get("people") == ["priya@acme.com"])
check("ingest projected summary", bool(res["item"]["summary"]))
check("ingest kept connector components", res["item"]["components"] == ["retry-service"])

# 4. ACL filter: restricted record admits only allow-listed principal
pub = SourceRecord(source="github", external_id="1", kind="pr", title="p", body="b",
                   acl=Acl(visibility="internal"))
priv = SourceRecord(source="github", external_id="2", kind="pr", title="s", body="b",
                    acl=Acl(visibility="restricted", allow=["pnair"]))
pool = [pub, priv]
# Priya (via github handle) sees both; a stranger sees only the public one
check("priya reads both", len(acl.readable(pool, "pnair")) == 2)
check("stranger reads only public", [r.external_id for r in acl.readable(pool, "sam")] == ["1"])
check("withheld reports the restricted one",
      [r.external_id for r in acl.withheld(pool, "sam")] == ["2"])

print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
