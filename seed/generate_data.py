"""Generate all synthetic Shadow source + memory files, deterministically.

No LLM. Writes seed/data/<area>/<file> and a sibling <file>.meta.json carrying
shadowItem (or shadowDecision / shadowPerson) values that setup_box.py applies
without spending Box AI units. The live Oct 17 standup goes to seed/live/.

Run:  python seed/generate_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LIVE = ROOT / "live"

EMAIL = "{}@payments.example".format

DANA = EMAIL("dana")
PRIYA = EMAIL("priya")
SAM = EMAIL("sam")
MARCUS = EMAIL("marcus")
ALEX = EMAIL("alex")
JORDAN = EMAIL("jordan")
NINA = EMAIL("nina")


def w(relpath: str, text: str, meta: dict | None = None, base: Path = DATA) -> None:
    p = base / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.strip() + "\n", encoding="utf-8")
    if meta is not None:
        (p.parent / (p.name + ".meta.json")).write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )


def item(source_type, title, occurred_on, components, issue_ids, people, summary,
         sensitivity="normal", template="shadowItem"):
    return {
        "_template": template,
        "sourceType": source_type,
        "title": title,
        "occurredOn": occurred_on,
        "components": components,
        "issueIds": issue_ids,
        "people": ",".join(people),
        "summary": summary,
        "sensitivity": sensitivity,
    }


# --------------------------------------------------------------------------- #
# Org
# --------------------------------------------------------------------------- #
def gen_org():
    org = {
        "generatedOn": "2026-10-17",
        "teams": {
            "Payments": {
                "manager": {"name": "Dana Kim", "email": DANA, "role": "Engineering Manager"},
                "members": [
                    {"name": "Priya Nair", "email": PRIYA, "role": "Senior Engineer",
                     "owns": ["retry-service", "gateway-client"]},
                    {"name": "Sam Ortiz", "email": SAM, "role": "Engineer",
                     "owns": ["payments-api", "checkout"]},
                    {"name": "Alex Rivera", "email": ALEX, "role": "Engineer",
                     "startDate": "2026-10-11", "owns": []},
                ],
            },
            "Platform": {
                "manager": {"name": "Jordan Blake", "email": EMAIL("jordan"),
                            "role": "Engineering Manager"},
                "members": [
                    {"name": "Marcus Lee", "email": MARCUS, "role": "Senior Engineer",
                     "owns": ["platform-infra"]},
                ],
            },
        },
        "history": [
            {"date": "2026-09-22",
             "change": "Marcus Lee moved from Payments to Platform in the September reorg. "
                       "Priya Nair took over ownership of retry-service."},
        ],
    }
    w("Org/org_chart.json", json.dumps(org, indent=2),
      item("org", "Payments org chart (post Sept 22 reorg)", "2026-09-22",
           ["retry-service", "payments-api", "ledger", "gateway-client", "checkout"],
           "", [DANA, PRIYA, SAM, ALEX, MARCUS],
           "Current Payments org after the Sept 22 reorg; Marcus moved to Platform, "
           "Priya owns retry-service."))


# --------------------------------------------------------------------------- #
# Docs
# --------------------------------------------------------------------------- #
def gen_docs():
    w("Docs/retry-idempotency-design.md", f"""
# Retry Idempotency Key Design

**Author:** Marcus Lee ({MARCUS})
**Date:** 2026-03-03
**Owner after Sept reorg:** Priya Nair ({PRIYA})
**Components:** retry-service, gateway-client
**Related issue:** PAY-482

## Problem
When a payment call to the gateway times out, retry-service retries the charge.
If the original request actually succeeded at the gateway but the response was
lost, the retry creates a **second charge** — the customer is double-charged.

## Proposal
Generate an **idempotency key** per payment attempt (not per retry). Send it as
the `Idempotency-Key` header on every gateway call. The gateway client stores
the key -> result mapping for **24 hours** so a retry with the same key returns
the original result instead of charging again.

## Open questions
1. Where is the key stored — in-memory in the gateway client, or in the ledger?
2. What TTL — 24h proposed, but incident windows can exceed that.

## Status
Proposed. Needs a decision in a standup before implementation.
""",
      item("doc", "Retry Idempotency Key Design", "2026-03-03",
           ["retry-service", "gateway-client"], "PAY-482", [MARCUS, PRIYA],
           "Design for idempotency keys to stop double-charges on retry after "
           "gateway timeout. Author Marcus Lee; owner now Priya Nair."))

    w("Docs/payments-runbook.md", """
# Payments On-Call Runbook

## Paging
`#payments-oncall` in Slack. Primary on-call rotates weekly.

## Duplicate charge
Symptom: a customer is charged twice for one order, usually after a gateway
timeout during a retry.
1. Confirm in the ledger that two charges share one order id.
2. Issue a refund for the duplicate via the **refunds tooling** (admin console
   -> Refunds -> "duplicate charge" reason code).
3. File or append to the tracking issue (currently PAY-482).
4. Do NOT disable retries globally — that drops legitimate retries.

## Gateway timeout storms
Check the gateway vendor status page, then throttle retry-service concurrency.
""",
      item("doc", "Payments On-Call Runbook", "2026-02-01",
           ["retry-service", "payments-api", "gateway-client"], "PAY-482",
           [SAM, PRIYA],
           "On-call runbook; duplicate-charge section points to refunds tooling "
           "and PAY-482."))

    w("Docs/payments-team-handbook.md", """
# Payments Team Handbook

## How we work
- **Standups:** Monday / Wednesday / Friday at 10:00, hosted by Dana Kim.
- **Pull requests:** any change to `gateway-client` needs one reviewer from the
  retry-service owners (currently Priya Nair).
- **Decisions** are recorded in the standup notes and, when durable, in a
  decision record under Memory/Decisions.
- **Incidents** go to `#payments-oncall`.

## Components
retry-service, payments-api, ledger, gateway-client, checkout.
""",
      item("doc", "Payments Team Handbook", "2026-01-15",
           ["retry-service", "payments-api", "ledger", "gateway-client", "checkout"],
           "", [DANA, PRIYA, SAM],
           "How the Payments team works: standup cadence, PR review rule for "
           "gateway-client, where decisions live."))


# --------------------------------------------------------------------------- #
# Meetings (seeded)
# --------------------------------------------------------------------------- #
def gen_meetings():
    w("Meetings/2026-03-12-payments-standup.txt", """
Payments Standup — 2026-03-12 10:00
Host: Dana Kim
Attendees: Dana Kim, Priya Nair, Sam Ortiz, Marcus Lee

[10:01] Dana: Quick one today. Sam, the duplicate-charge reports.
[10:02] Sam: Filed PAY-482 this morning. Customers double-charged when the
        gateway times out and retry-service retries.
[10:03] Marcus: My idempotency-key design doc covers this. Proposal is a key per
        payment attempt, stored 24h in the gateway client.
[10:04] Priya: The open question is key storage and TTL. I don't want to ship
        before that's settled.
[10:05] Dana: Agreed. Let's PAUSE the retry change until the idempotency key
        design is finalized. Priya owns the follow-up.
[10:06] Priya: Works. I'll pick up the design questions.
[10:06] Dana: Decision recorded: paused pending idempotency key design. Done.
""",
      item("meeting", "Payments Standup Mar 12", "2026-03-12",
           ["retry-service", "gateway-client"], "PAY-482",
           [DANA, PRIYA, SAM, MARCUS],
           "Standup where the retry change was PAUSED pending the idempotency "
           "key design; Priya owns follow-up. PAY-482 filed."))

    w("Meetings/2026-10-10-payments-standup.txt", """
Payments Standup — 2026-10-10 10:00
Host: Dana Kim
Attendees: Dana Kim, Priya Nair, Sam Ortiz

[10:01] Dana: Sam, recap yesterday's incident.
[10:02] Sam: Oct 9, gateway timeouts caused duplicate charges for 37 customers.
        Same root cause as PAY-482. We did manual refunds overnight.
[10:03] Priya: I started a draft PR, #1184, adding the idempotency key header to
        the retry client. Not ready to merge — still want the design signed off.
[10:04] Dana: Good. Alex starts next week and can pick up PAY-482 with Priya's
        context.
[10:05] Priya: I'll leave notes on the PR and the design doc for whoever takes it.
""",
      item("meeting", "Payments Standup Oct 10", "2026-10-10",
           ["retry-service", "gateway-client"], "PAY-482",
           [DANA, PRIYA, SAM],
           "Standup after the Oct 9 incident (37 customers double-charged). Priya "
           "has draft PR #1184; design not yet signed off. Alex to take PAY-482."))


# --------------------------------------------------------------------------- #
# Restricted + Quarantine
# --------------------------------------------------------------------------- #
def gen_restricted():
    w("Restricted/2026-10-14-design-review.txt", """
Design Review — Gateway Vendor — 2026-10-14 14:00
Host: Dana Kim
Attendees: Dana Kim, Priya Nair  (RESTRICTED)

[14:01] Dana: This stays between us until procurement is ready.
[14:02] Priya: Current gateway vendor contract renews in Q1. Pricing jumped 18%.
[14:03] Dana: Let's evaluate a second vendor in parallel. Budget impact is the
        sensitive part — do not share the numbers outside this review.
[14:04] Priya: The idempotency work touches gateway-client either way, so vendor
        choice doesn't block PAY-482.
""",
      item("meeting", "Design Review — Gateway Vendor Pricing", "2026-10-14",
           ["retry-service", "gateway-client"], "",
           [DANA, PRIYA],
           "Restricted design review on gateway vendor contract pricing and a "
           "second-vendor evaluation.", sensitivity="restricted"))


def gen_quarantine():
    w("Quarantine/2026-10-15-dana-priya-1on1.txt", """
1:1 — Dana / Priya — 2026-10-15  (HR / CONFIDENTIAL)
[notes contain performance-review feedback — not for team consumption]
""",
      item("meeting", "Dana/Priya 1:1 (HR)", "2026-10-15",
           [], "", [DANA, PRIYA],
           "Confidential 1:1 with performance-review content. Quarantined; never "
           "used in answers.", sensitivity="hr"))


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #
def gen_slack():
    w("Slack/payments-oncall-2026-10-09.md", """
# #payments-oncall — 2026-10-09

**Sam** 23:14 — Paging. Spike of duplicate charges, gateway timeouts in the last hour.
**Sam** 23:15 — Confirmed 37 customers double-charged. Mitigation: manual refunds now.
**Priya** 23:20 — This is PAY-482 again. Root cause is retry-after-timeout. I have context.
**Sam** 23:41 — Refunds done for the batch. Logged against PAY-482.
""",
      item("slack", "#payments-oncall Oct 9 incident", "2026-10-09",
           ["retry-service", "gateway-client"], "PAY-482", [SAM, PRIYA],
           "On-call thread for the Oct 9 duplicate-charge incident; Priya flags it "
           "as PAY-482 again and says she has context."))

    w("Slack/payments-general-2026-10-13.md", """
# #payments-general — 2026-10-13

**Sam** 09:12 — standup moved?
**Dana** 09:13 — no, normal time.
**Priya** 15:40 — draft PR #1184 for idempotency keys is up, not ready to merge.
Review welcome on the header approach.
""",
      item("slack", "#payments-general Oct 13", "2026-10-13",
           ["retry-service", "gateway-client"], "PAY-482", [SAM, DANA, PRIYA],
           "General chatter; Priya announces draft PR #1184 for idempotency keys, "
           "not ready to merge."))


# --------------------------------------------------------------------------- #
# Issues
# --------------------------------------------------------------------------- #
def gen_issues():
    w("Issues/PAY-482.md", f"""
# PAY-482 — Duplicate charges when payment retries after gateway timeout

- **Created:** 2026-03-12
- **Reporter:** Sam Ortiz ({SAM})
- **Assignee:** Alex Rivera ({ALEX})  _(as of 2026-10-17)_
- **Labels:** retry-service, gateway-client
- **Links:** PR #1184, Docs/retry-idempotency-design.md

## Description
Customers are charged twice when a gateway timeout triggers a retry in
retry-service and the original charge actually succeeded. Recurred in the
Oct 9 incident (37 customers). Fix tracked via idempotency keys.
""",
      item("issue", "PAY-482 Duplicate charges on retry", "2026-03-12",
           ["retry-service", "gateway-client"], "PAY-482",
           [SAM, ALEX, PRIYA],
           "Primary issue: duplicate charges on retry after gateway timeout. "
           "Assigned to Alex on Oct 17; fix via idempotency keys."))

    w("Issues/PAY-455.md", """
# PAY-455 — Ledger reconciliation off by one on refunds

- Created: 2026-08-01
- Labels: ledger
Unrelated to retries; ledger totals drift after partial refunds.
""",
      item("issue", "PAY-455 Ledger reconciliation", "2026-08-01",
           ["ledger"], "PAY-455", [SAM],
           "Unrelated ledger reconciliation bug (noise)."))

    w("Issues/PAY-470.md", """
# PAY-470 — Checkout button misaligned on mobile Safari

- Created: 2026-09-15
- Labels: checkout
Pure front-end CSS issue, unrelated to payments retries.
""",
      item("issue", "PAY-470 Checkout button CSS", "2026-09-15",
           ["checkout"], "PAY-470", [SAM],
           "Unrelated checkout CSS bug (noise)."))


# --------------------------------------------------------------------------- #
# PRs
# --------------------------------------------------------------------------- #
def gen_prs():
    w("PRs/PR-1184.md", f"""
# PR #1184 — Add idempotency key header to retry client (draft)

- **Author:** Priya Nair ({PRIYA})
- **Opened:** 2026-10-13
- **Status:** Draft
- **Refs:** PAY-482, Docs/retry-idempotency-design.md

## Summary
Adds an `Idempotency-Key` header generated per payment attempt in the gateway
client, with a 24h key->result cache.

## Review comments
- **Sam:** header approach looks right; what happens on cache eviction mid-incident?
- **Priya:** good point — leaning toward a feature flag so we can ship behind it.
""",
      item("pr", "PR #1184 idempotency key header (draft)", "2026-10-13",
           ["retry-service", "gateway-client"], "PAY-482", [PRIYA, SAM],
           "Draft PR adding the idempotency key header; open question on cache "
           "eviction; Priya leaning toward a feature flag."))


# --------------------------------------------------------------------------- #
# Memory (pre-seeded so first answer works without ingestion)
# --------------------------------------------------------------------------- #
def gen_memory():
    w("Memory/Meeting Notes/2026-03-12-payments-standup-notes.md", """
# Meeting Notes — Payments Standup, 2026-03-12

**Summary:** Discussed duplicate charges on retry (PAY-482). Decided to pause the
retry change until the idempotency key design is finalized.

**Decisions:**
- PAUSED the retry change pending idempotency key design (owner: Priya Nair).

**Action items:**
- Priya: finalize idempotency key storage + TTL.

**People:** Dana Kim (host), Priya Nair, Sam Ortiz, Marcus Lee.
""",
      item("meeting", "Notes — Payments Standup Mar 12", "2026-03-12",
           ["retry-service", "gateway-client"], "PAY-482",
           [DANA, PRIYA, SAM, MARCUS],
           "Structured notes: retry change paused pending idempotency key design."))

    w("Memory/Meeting Notes/2026-10-10-payments-standup-notes.md", """
# Meeting Notes — Payments Standup, 2026-10-10

**Summary:** Oct 9 incident recap (37 customers double-charged). Priya has draft
PR #1184; design not yet signed off. Alex to take PAY-482.

**Decisions:** none (still paused).

**Action items:**
- Priya: leave notes on PR #1184 and the design doc for the next owner.

**People:** Dana Kim (host), Priya Nair, Sam Ortiz.
""",
      item("meeting", "Notes — Payments Standup Oct 10", "2026-10-10",
           ["retry-service", "gateway-client"], "PAY-482",
           [DANA, PRIYA, SAM],
           "Structured notes: incident recap, draft PR #1184, still paused."))

    # Decision record (shadowDecision)
    w("Memory/Decisions/retry-idempotency-keys.md", """
# Decision — Idempotency keys for retry double-charges

**Decision:** Pause the retry change until the idempotency key design is
finalized.
**Status:** paused
**Decided on:** 2026-03-12
**Owners:** priya@payments.example
**Components:** retry-service, gateway-client
**Confirmed:** yes

## Context
Double-charges occur on retry after gateway timeout (PAY-482). The idempotency
key design (Marcus Lee, Mar 3) is the fix but storage + TTL are unresolved, so
implementation is paused.
""",
      {
          "_template": "shadowDecision",
          "decision": "Pause the retry change until the idempotency key design is finalized",
          "status": "paused",
          "decidedOn": "2026-03-12",
          "owners": PRIYA,
          "components": ["retry-service", "gateway-client"],
          "sourceFileIds": "",  # filled by setup after uploads
          "confirmed": "yes",
      })

    # People cards (shadowPerson)
    people = [
        ("Priya Nair", PRIYA, "Senior Engineer", "Payments", DANA, "yes",
         ["retry-service", "gateway-client", "ledger"], "2026-10-13",
         "Owns retry-service and gateway-client. Wrote draft PR #1184 and the "
         "ledger at-least-once fix (PR #1190)."),
        ("Marcus Lee", MARCUS, "Senior Engineer", "Platform", EMAIL("jordan"), "no",
         ["retry-service", "gateway-client"], "2026-03-12",
         "Authored the idempotency design. Moved from Payments to Platform on "
         "Sept 22; no longer active on Payments."),
        ("Sam Ortiz", SAM, "Engineer", "Payments", DANA, "yes",
         ["payments-api", "checkout"], "2026-10-10",
         "On-call for the Oct 9 incident. Owns payments-api and checkout."),
        ("Dana Kim", DANA, "Engineering Manager", "Payments", "", "yes",
         [], "2026-10-10",
         "Engineering manager; hosts standups and the design review."),
        ("Alex Rivera", ALEX, "Engineer", "Payments", DANA, "yes",
         [], "2026-10-17",
         "New hire (started Oct 11). Assigned PAY-482 on Oct 17."),
    ]
    for name, email, role, team, mgr, active, expertise, last, blurb in people:
        slug = name.lower().replace(" ", "-")
        w(f"Memory/People/{slug}.md", f"""
# {name}

- **Role:** {role}
- **Team:** {team}
- **Manager:** {mgr or '—'}
- **Active on Payments:** {active}
- **Expertise:** {', '.join(expertise) or '—'}

{blurb}
""",
          {
              "_template": "shadowPerson",
              "name": name, "email": email, "role": role, "team": team,
              "manager": mgr, "active": active, "expertise": expertise,
              "lastActiveOn": last,
          })

    w("Memory/Handbook/how-this-team-works.md", """
# How the Payments team works

Standups Mon/Wed/Fri 10:00 (host Dana). gateway-client changes need a reviewer
from retry-service owners (Priya). Decisions live in standup notes and
Memory/Decisions. Incidents -> #payments-oncall.

## Recent decisions
- Mar 12: PAUSED the retry change pending idempotency key design (owner Priya).
""",
      item("doc", "How this team works", "2026-01-15",
           ["retry-service", "payments-api", "ledger", "gateway-client", "checkout"],
           "", [DANA, PRIYA, SAM],
           "Handbook copy plus recent-decisions section."))


# --------------------------------------------------------------------------- #
# EXPANDED CONTENT — more topics so new questions return real answers,
# and the three privacy scenarios (golden / failure / average).
# --------------------------------------------------------------------------- #
def gen_more_issues():
    w("Issues/PAY-501.md", f"""
# PAY-501 — Ledger webhook drops events under load

- **Created:** 2026-09-28
- **Reporter:** Sam Ortiz ({SAM})
- **Assignee:** Priya Nair ({PRIYA})
- **Labels:** ledger
- **Links:** PR #1190

## Description
Under high throughput the ledger webhook consumer drops events, so some charges
never get a ledger entry. Reconciliation then shows gaps. Needs an at-least-once
delivery guarantee with a dead-letter queue.
""",
      item("issue", "PAY-501 Ledger webhook drops events", "2026-09-28",
           ["ledger"], "PAY-501", [SAM, PRIYA],
           "Ledger webhook consumer drops events under load; charges miss ledger "
           "entries. Fix: at-least-once delivery + dead-letter queue. PR #1190."))

    w("Issues/PAY-512.md", f"""
# PAY-512 — Checkout page times out during flash sales

- **Created:** 2026-10-02
- **Reporter:** Dana Kim ({DANA})
- **Assignee:** Sam Ortiz ({SAM})
- **Labels:** checkout, payments-api
- **Links:** PR #1201

## Description
During flash sales the checkout page times out because the payments-api call is
synchronous and the connection pool is exhausted. Decided fix: make the capture
call async and raise the pool size. Shipped behind a flag.
""",
      item("issue", "PAY-512 Checkout timeout on flash sales", "2026-10-02",
           ["checkout", "payments-api"], "PAY-512", [DANA, SAM],
           "Checkout times out in flash sales — synchronous payments-api call "
           "exhausts the connection pool. Fix: async capture + larger pool, behind "
           "a flag. PR #1201."))

    w("Issues/PAY-523.md", f"""
# PAY-523 — Gateway client connection pool leak

- **Created:** 2026-10-08
- **Reporter:** Priya Nair ({PRIYA})
- **Assignee:** Priya Nair ({PRIYA})
- **Labels:** gateway-client
Open investigation: connections aren't returned to the pool after a timeout,
slowly starving the client. Possibly related to the retry path in PAY-482.
""",
      item("issue", "PAY-523 Gateway client pool leak", "2026-10-08",
           ["gateway-client"], "PAY-523", [PRIYA],
           "Open: gateway-client leaks connections after a timeout, starving the "
           "pool. Possibly linked to the PAY-482 retry path."))


def gen_more_prs():
    w("PRs/PR-1190.md", f"""
# PR #1190 — Ledger webhook: at-least-once + dead-letter queue

- **Author:** Priya Nair ({PRIYA})
- **Opened:** 2026-10-01
- **Status:** Merged (2026-10-06)
- **Refs:** PAY-501

## Summary
Adds an at-least-once consumer with an SQS dead-letter queue so ledger events
are never silently dropped. Reconciliation gaps closed in staging.
""",
      item("pr", "PR #1190 ledger at-least-once + DLQ (merged)", "2026-10-01",
           ["ledger"], "PAY-501", [PRIYA, SAM],
           "Merged PR giving the ledger webhook at-least-once delivery and a "
           "dead-letter queue. Closed reconciliation gaps."))

    w("PRs/PR-1201.md", f"""
# PR #1201 — Async capture + larger pool for checkout (merged)

- **Author:** Sam Ortiz ({SAM})
- **Opened:** 2026-10-04
- **Status:** Merged (2026-10-09)
- **Refs:** PAY-512

## Summary
Makes the payments-api capture call async and raises the connection pool size.
Shipped behind the `checkout_async_capture` flag; on at 50% in production.
""",
      item("pr", "PR #1201 async capture for checkout (merged)", "2026-10-04",
           ["checkout", "payments-api"], "PAY-512", [SAM, DANA],
           "Merged PR: async capture + larger pool for checkout flash-sale "
           "timeouts, behind a flag at 50% rollout."))


def gen_more_docs():
    w("Docs/ledger-architecture.md", f"""
# Ledger Architecture

**Owner:** Priya Nair ({PRIYA})
**Components:** ledger

The ledger is the source of truth for every charge and refund. Events arrive on
a webhook, are validated, then written as immutable entries. As of PR #1190 the
consumer is at-least-once with a dead-letter queue (PAY-501). Reconciliation
runs nightly and flags any order id with mismatched charge/refund totals.
""",
      item("doc", "Ledger Architecture", "2026-10-06",
           ["ledger"], "PAY-501", [PRIYA],
           "Ledger is the source of truth for charges/refunds; at-least-once "
           "webhook consumer with DLQ since PR #1190; nightly reconciliation."))

    w("Docs/new-hire-onboarding.md", f"""
# New Hire Onboarding — Payments

Welcome! Your first week:
1. Read the team handbook and this guide.
2. Get Box access to the Payments team space (ask Dana Kim, {DANA}).
3. Pick up your first issue — ask your onboarding buddy who to pair with.
4. Standups are Mon/Wed/Fri 10:00.

**Onboarding buddy:** Nina Patel ({NINA}) runs onboarding for new engineers.
**Your manager:** Dana Kim.
Ask Shadow anything — it has read the team's meetings, Slack, issues and docs.
""",
      item("doc", "New Hire Onboarding", "2026-10-01",
           ["payments-api"], "", [DANA, NINA, ALEX],
           "Onboarding guide for new Payments engineers; Nina Patel is the "
           "onboarding buddy, Dana is the manager; week-one checklist."))

    w("Docs/gateway-client-guide.md", f"""
# Gateway Client Guide

**Owner:** Priya Nair ({PRIYA})
**Components:** gateway-client, retry-service

The gateway client wraps all calls to the payment gateway vendor. Every call
carries a timeout and is retried by retry-service on failure. The idempotency
key work (PAY-482) lives here. Known open issue: a connection pool leak after
timeouts (PAY-523).
""",
      item("doc", "Gateway Client Guide", "2026-09-20",
           ["gateway-client", "retry-service"], "PAY-482,PAY-523", [PRIYA],
           "Guide to the gateway client: timeouts, retries, idempotency keys "
           "(PAY-482), and the open pool-leak issue (PAY-523)."))


def gen_more_meetings():
    w("Meetings/2026-10-06-payments-standup.txt", """
Payments Standup — 2026-10-06 10:00
Host: Dana Kim
Attendees: Dana Kim, Priya Nair, Sam Ortiz

[10:01] Dana: Ledger webhook fix — status?
[10:02] Priya: PR #1190 merged. At-least-once with a dead-letter queue. PAY-501
        closed. Reconciliation gaps are gone in staging.
[10:03] Sam: Checkout flash-sale timeout — PR #1201 is merged too, async capture
        behind a flag. Decided to roll out to 50% first.
[10:04] Dana: Good. Decision recorded: ship checkout async capture behind a flag,
        50% rollout. Confirmed.
""",
      item("meeting", "Payments Standup Oct 6", "2026-10-06",
           ["ledger", "checkout", "payments-api"], "PAY-501,PAY-512",
           [DANA, PRIYA, SAM],
           "Standup: ledger PR #1190 merged (PAY-501 closed); checkout PR #1201 "
           "merged, decided to roll out async capture behind a flag at 50%."))


def gen_more_slack():
    w("Slack/ledger-2026-10-06.md", """
# #payments-ledger — 2026-10-06

**Priya** 11:02 — #1190 is merged, ledger webhook is at-least-once now with a DLQ.
**Sam** 11:05 — nice, that closes PAY-501. Reconciliation looked clean overnight.
**Dana** 11:06 — great work. Let's watch the DLQ depth for a week.
""",
      item("slack", "#payments-ledger Oct 6", "2026-10-06",
           ["ledger"], "PAY-501", [PRIYA, SAM, DANA],
           "Ledger channel: PR #1190 merged, PAY-501 closed, watch DLQ depth."))

    w("Slack/onboarding-2026-10-11.md", """
# #payments-onboarding — 2026-10-11

**Nina** 09:00 — welcome Alex! I'm your onboarding buddy. Ping me anytime.
**Alex** 09:02 — thanks! where do I start?
**Nina** 09:03 — read the handbook, then grab Box access from Dana. Priya will
pair with you on your first issue.
""",
      item("slack", "#payments-onboarding Oct 11", "2026-10-11",
           ["payments-api"], "", [NINA, ALEX, DANA, PRIYA],
           "Onboarding channel: Nina welcomes Alex as onboarding buddy; start with "
           "the handbook, get Box access from Dana, pair with Priya."))


def gen_more_decisions():
    # a DECIDED + confirmed decision (checkout) -> green in the table
    w("Memory/Decisions/checkout-async-capture.md", """
# Decision — Checkout async capture behind a flag

**Decision:** Ship checkout async capture behind the `checkout_async_capture`
flag, rolling out to 50% first.
**Status:** decided
**Decided on:** 2026-10-06
**Owners:** sam@payments.example
**Components:** checkout, payments-api
**Confirmed:** yes

## Context
Checkout timed out in flash sales (PAY-512). PR #1201 makes capture async and
raises the pool; decided to roll out behind a flag at 50% before going to 100%.
""",
      {
          "_template": "shadowDecision",
          "decision": "Ship checkout async capture behind a flag, 50% rollout first",
          "status": "decided",
          "decidedOn": "2026-10-06",
          "owners": SAM,
          "components": ["checkout", "payments-api"],
          "sourceFileIds": "",
          "confirmed": "yes",
      })

    # a PROPOSED decision (ledger DLQ alerting) -> amber in the table
    w("Memory/Decisions/ledger-dlq-alerting.md", """
# Decision — Alert on ledger dead-letter-queue depth

**Decision:** Add paging alert when the ledger DLQ depth exceeds 100 for 5
minutes.
**Status:** proposed
**Decided on:** 2026-10-07
**Owners:** priya@payments.example
**Components:** ledger
**Confirmed:** pending

## Context
After PR #1190 the DLQ catches dropped events, but nothing pages if it fills.
Proposed an alert; awaiting on-call sign-off before enabling.
""",
      {
          "_template": "shadowDecision",
          "decision": "Alert on ledger DLQ depth > 100 for 5 minutes",
          "status": "proposed",
          "decidedOn": "2026-10-07",
          "owners": PRIYA,
          "components": ["ledger"],
          "sourceFileIds": "",
          "confirmed": "pending",
      })


def gen_more_people():
    people = [
        ("Jordan Blake", JORDAN, "Engineering Manager", "Platform", "", "yes",
         [], "2026-09-22",
         "Platform EM. Took Marcus Lee onto Platform in the Sept 22 reorg."),
        ("Nina Patel", NINA, "Engineer", "Payments", DANA, "yes",
         ["payments-api", "checkout"], "2026-10-11",
         "Onboarding buddy for new Payments engineers. Owns onboarding and pairs "
         "with new hires on their first issue."),
    ]
    for name, email, role, team, mgr, active, expertise, last, blurb in people:
        slug = name.lower().replace(" ", "-")
        w(f"Memory/People/{slug}.md", f"""
# {name}

- **Role:** {role}
- **Team:** {team}
- **Manager:** {mgr or '—'}
- **Active on Payments:** {active}
- **Expertise:** {', '.join(expertise) or '—'}

{blurb}
""",
          {
              "_template": "shadowPerson",
              "name": name, "email": email, "role": role, "team": team,
              "manager": mgr, "active": active, "expertise": expertise,
              "lastActiveOn": last,
          })


def gen_more_restricted():
    """Second restricted file so the 'average/partial' privacy scenario works:
    a budget doc on checkout that the asker cannot read, while PUBLIC checkout
    sources (PAY-512, PR #1201) remain readable — so an answer about checkout
    is partial: real public citations + a 'one source withheld' note."""
    w("Restricted/2026-10-05-checkout-budget.txt", """
Checkout Spend Review — 2026-10-05 15:00
Host: Dana Kim
Attendees: Dana Kim, Jordan Blake  (RESTRICTED — budget)

[15:01] Dana: Infra spend for the checkout async rollout. Numbers are sensitive.
[15:02] Jordan: Larger connection pools raise the bill ~12%. Approved for Q4 only.
[15:03] Dana: Keep the figure out of the team channels until finance confirms.
""",
      item("meeting", "Checkout Spend Review (budget)", "2026-10-05",
           ["checkout", "payments-api"], "PAY-512",
           [DANA, JORDAN],
           "Restricted budget review of the checkout async rollout infra spend; "
           "~12% cost increase approved for Q4. Figures not for team channels.",
           sensitivity="restricted"))


# --------------------------------------------------------------------------- #
# BULK CONTENT — FAANG-scale volume (100+ files). Deterministic (seeded RNG),
# so re-runs are stable and idempotent uploads don't duplicate. Hero IDs
# (PAY-455/470/482/501/512/523) and the privacy scenarios are NOT touched:
# bulk issues start at PAY-1000, bulk people are extra engineers.
# --------------------------------------------------------------------------- #
import random  # noqa: E402

COMPONENTS_ALL = ["retry-service", "payments-api", "ledger", "gateway-client",
                  "checkout"]

# a realistic multi-team Payments org (extra engineers for volume + who-to-ask)
BULK_PEOPLE = [
    # name, team, role, components
    ("Wei Zhang", "Payments", "Staff Engineer", ["payments-api", "ledger"]),
    ("Fatima Al-Sayed", "Payments", "Senior Engineer", ["gateway-client"]),
    ("Diego Morales", "Payments", "Engineer", ["checkout"]),
    ("Hannah Schmidt", "Payments", "Senior Engineer", ["ledger"]),
    ("Raj Patel", "Payments", "Engineer", ["payments-api"]),
    ("Yuki Tanaka", "Checkout", "Senior Engineer", ["checkout"]),
    ("Olivia Brown", "Checkout", "Engineer", ["checkout"]),
    ("Liam O'Connor", "Checkout", "Engineering Manager", []),
    ("Aisha Khan", "Ledger", "Staff Engineer", ["ledger"]),
    ("Tomas Novak", "Ledger", "Senior Engineer", ["ledger"]),
    ("Grace Kim", "Ledger", "Engineering Manager", []),
    ("Mohammed Hassan", "Platform", "Senior Engineer", ["gateway-client"]),
    ("Elena Petrova", "Platform", "Staff Engineer", ["gateway-client", "retry-service"]),
    ("Carlos Ruiz", "Gateway", "Senior Engineer", ["gateway-client"]),
    ("Sophie Martin", "Gateway", "Engineer", ["gateway-client"]),
    ("Kenji Yamamoto", "Gateway", "Engineering Manager", []),
    ("Nadia Rahman", "Risk", "Senior Engineer", ["payments-api"]),
    ("James Wilson", "Risk", "Engineer", ["payments-api"]),
    ("Priyanka Rao", "Data", "Staff Engineer", ["ledger"]),
    ("Lucas Silva", "Data", "Senior Engineer", ["ledger"]),
]


def _rng():
    return random.Random(48221)  # fixed seed -> deterministic output


def _date(rng, start=(2026, 1), end=(2026, 10)):
    import datetime
    s = datetime.date(start[0], start[1], 1)
    e = datetime.date(end[0], end[1], 15)
    d = s + datetime.timedelta(days=rng.randint(0, (e - s).days))
    return d.isoformat()


def gen_bulk():
    rng = _rng()
    emails = {p[0]: EMAIL(p[0].split()[0].lower().replace("'", "")) for p in BULK_PEOPLE}
    allp = list(emails.values()) + [DANA, PRIYA, SAM, NINA]

    # ---- people cards (20) ----
    for name, team, role, comps in BULK_PEOPLE:
        slug = name.lower().replace(" ", "-").replace("'", "")
        mgr = DANA if team == "Payments" else ""
        w(f"Memory/People/{slug}.md", f"""
# {name}

- **Role:** {role}
- **Team:** {team}
- **Active:** yes
- **Expertise:** {', '.join(comps) or '—'}

{name} is a {role.lower()} on the {team} team.
""",
          {"_template": "shadowPerson", "name": name, "email": emails[name],
           "role": role, "team": team, "manager": mgr, "active": "yes",
           "expertise": comps, "lastActiveOn": _date(rng)})

    # ---- issues (42: PAY-1000..PAY-1041) ----
    issue_titles = [
        "latency spike on {c}", "intermittent 5xx from {c}", "memory leak in {c}",
        "timeout under load on {c}", "race condition in {c}", "stale cache in {c}",
        "metric gap for {c}", "retry storm from {c}", "config drift in {c}",
        "slow query in {c}", "connection reset on {c}", "backpressure in {c}",
    ]
    statuses = ["open", "open", "open", "in progress", "closed", "closed"]
    for i in range(42):
        num = 1000 + i
        comp = COMPONENTS_ALL[i % len(COMPONENTS_ALL)]
        title = issue_titles[i % len(issue_titles)].format(c=comp)
        reporter = rng.choice(allp)
        assignee = rng.choice(allp)
        st = statuses[i % len(statuses)]
        d = _date(rng)
        w(f"Issues/PAY-{num}.md", f"""
# PAY-{num} — {title}

- **Created:** {d}
- **Reporter:** {reporter}
- **Assignee:** {assignee}
- **Status:** {st}
- **Labels:** {comp}

Tracked issue on {comp}. {title.capitalize()} observed in production and under
investigation by the owning team.
""",
          item("issue", f"PAY-{num} {title}", d, [comp], f"PAY-{num}",
               [reporter, assignee],
               f"{title.capitalize()} on {comp}; status {st}."))

    # ---- PRs (30) ----
    for i in range(30):
        num = 1300 + i
        comp = COMPONENTS_ALL[i % len(COMPONENTS_ALL)]
        author = rng.choice(allp)
        merged = i % 3 != 0
        d = _date(rng)
        w(f"PRs/PR-{num}.md", f"""
# PR #{num} — {'fix' if merged else 'wip'} for {comp}

- **Author:** {author}
- **Opened:** {d}
- **Status:** {'Merged' if merged else 'Draft'}
- **Labels:** {comp}

{'Merged change' if merged else 'Draft change'} touching {comp}.
""",
          item("pr", f"PR #{num} {comp} change", d, [comp], f"PAY-{1000 + (i % 42)}",
               [author], f"{'Merged' if merged else 'Draft'} PR on {comp}."))

    # ---- meetings (28 standups Jan..Oct) ----
    for i in range(28):
        import datetime
        base = datetime.date(2026, 1, 7) + datetime.timedelta(days=i * 9)
        d = base.isoformat()
        att = rng.sample(allp, 4)
        comp = COMPONENTS_ALL[i % len(COMPONENTS_ALL)]
        w(f"Meetings/{d}-eng-standup.txt", f"""
Engineering Standup — {d} 10:00
Attendees: {', '.join(att[:4])}

Discussed {comp} work in flight, PR review queue, and on-call handoff.
No blocking decisions recorded.
""",
          item("meeting", f"Eng Standup {d}", d, [comp], "", att,
               f"Routine standup covering {comp}; no blocking decisions."))

    # ---- slack threads (16) ----
    channels = ["payments-eng", "checkout-eng", "ledger-eng", "gateway-eng",
                "incidents", "deploys", "oncall", "random"]
    for i in range(16):
        ch = channels[i % len(channels)]
        comp = COMPONENTS_ALL[i % len(COMPONENTS_ALL)]
        d = _date(rng)
        a, b = rng.sample(allp, 2)
        w(f"Slack/{ch}-{d}-{i}.md", f"""
# #{ch} — {d}

**{a.split('@')[0]}** 09:{10 + i:02d} — status on {comp}?
**{b.split('@')[0]}** 09:{12 + i:02d} — green, deploy went out, metrics nominal.
""",
          item("slack", f"#{ch} {d}", d, [comp], "", [a, b],
               f"Routine {ch} chatter about {comp}; deploy nominal."))

    # ---- docs / RFCs (120 -> "Docs in Box" lands in the 90-150 range) ----
    doc_kinds = ["Design", "RFC", "Runbook", "Postmortem", "Spec", "ADR",
                 "Playbook", "Guide", "Proposal", "Review"]
    doc_topics = [
        "idempotency", "sharding", "rate limiting", "circuit breaker",
        "dead-letter queue", "reconciliation", "capacity planning",
        "schema migration", "rollout strategy", "observability",
        "incident response", "SLA/SLO", "data retention", "failover",
        "load shedding", "canary deploy", "feature flags", "audit logging",
    ]
    for i in range(120):
        comp = COMPONENTS_ALL[i % len(COMPONENTS_ALL)]
        kind = doc_kinds[i % len(doc_kinds)]
        topic = doc_topics[i % len(doc_topics)]
        author = rng.choice(allp)
        d = _date(rng)
        w(f"Docs/{comp}-{kind.lower()}-{topic.replace(' ', '-').replace('/', '-')}-{i}.md", f"""
# {kind}: {topic} in {comp}

**Author:** {author}
**Date:** {d}
**Components:** {comp}

{kind} describing {topic} for the {comp} subsystem: goals, design, trade-offs,
and operational notes. Reviewed and approved by the owning team.
""",
          item("doc", f"{kind}: {topic} in {comp}", d, [comp], "", [author],
               f"{kind} on {topic} for {comp}: design and operational notes."))

    n = sum(1 for _ in DATA.rglob("*") if _.is_file() and not _.name.endswith(".meta.json"))
    print(f"  bulk generated — total source files now ~{n}")


# --------------------------------------------------------------------------- #
# Live demo file (uploaded DURING the demo, not by setup)
# --------------------------------------------------------------------------- #
def gen_live():
    w("2026-10-17-payments-standup.txt", """
Payments Standup — 2026-10-17 10:00
Host: Dana Kim
Attendees: Dana Kim, Priya Nair, Sam Ortiz, Alex Rivera

[10:01] Dana: Welcome Alex — you've got PAY-482.
[10:02] Priya: Good timing. The idempotency key design is basically settled now —
        key per payment attempt, 24h TTL, stored in the gateway client.
[10:03] Priya: I propose we ship PR #1184 behind a feature flag next sprint.
[10:04] Dana: Let's go with that unless anyone objects by Monday.
[10:05] Sam: No objection here.
[10:06] Alex: Got it, I'll start reading the design doc and the PR.
""",
      item("meeting", "Payments Standup Oct 17", "2026-10-17",
           ["retry-service", "gateway-client"], "PAY-482",
           [DANA, PRIYA, SAM, ALEX],
           "Standup where Priya says the idempotency design is basically settled "
           "and proposes shipping PR #1184 behind a feature flag next sprint; Dana "
           "agrees unless objections by Monday."),
      base=LIVE)


def main():
    for area in ["Org", "Docs", "Meetings", "Restricted", "Quarantine", "Slack",
                 "Issues", "PRs", "Memory"]:
        (DATA / area).mkdir(parents=True, exist_ok=True)
    LIVE.mkdir(parents=True, exist_ok=True)
    gen_org()
    gen_docs()
    gen_meetings()
    gen_restricted()
    gen_quarantine()
    gen_slack()
    gen_issues()
    gen_prs()
    gen_memory()
    # expanded content + privacy scenarios
    gen_more_issues()
    gen_more_prs()
    gen_more_docs()
    gen_more_meetings()
    gen_more_slack()
    gen_more_decisions()
    gen_more_people()
    gen_more_restricted()
    gen_bulk()
    gen_live()
    n = sum(1 for _ in DATA.rglob("*") if _.is_file() and not _.name.endswith(".meta.json"))
    m = sum(1 for _ in DATA.rglob("*.meta.json"))
    print(f"Wrote {n} source files + {m} meta sidecars under {DATA}")
    print(f"Wrote live demo file under {LIVE}")


if __name__ == "__main__":
    main()
