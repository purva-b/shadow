# Shadow — Project Status

Shadow is an AI teammate that learns everything a team says (meetings, chats,
issues, docs) and answers anyone's questions with cited sources, strictly within
their own permissions.

This is a living status for the project as a **general product** — a
permission-aware knowledge agent over pluggable sources. It is not scoped to any
one team, dataset, or set of example questions.

_Last updated: 2026-10-19._

---

## Architecture (what the product IS)

```
Sources (plugins)            Core (source-agnostic)            Surface
┌────────────┐
│ Box        │ ─┐
│ GitHub     │  │  fetch → SourceRecord ┐
│ Slack      │  ├─────────────────────► │ ingest → store → one semantic index
│ Confluence │  │   (normalized shape)   │        (Bedrock Titan embeddings)
│ Calendar   │ ─┘                        │
└────────────┘                           ▼
                              retrieve (semantic + exact)
                                        ▼
                              ACL filter (per-asker)  ◄── identity map
                                        ▼
                              answer (prose + citations
                              + who-to-ask + decision)  ──► FastAPI + React UI
                                        │
                              ingest also → proactive notify owners
```

Every source normalizes to one `SourceRecord`; the whole core is source-agnostic.
Adding a source is one connector file + a config line. Nothing in the core is
tied to a specific team or question set.

---

## Build status

| Area | State | Notes |
|---|---|---|
| Connector interface + registry | **Done** | `SourceRecord`, protocol, auto-discovery |
| Box connector | **Done, live** | Full corpus (259 records) synced + embedded |
| GitHub connector | **Done, live-verified** | Issues/PRs, repo-visibility ACL |
| Slack / Confluence / Calendar | **Built, untested live** | Real code; need live tokens to exercise |
| Identity resolution | **Done** | One person across all source handles |
| Permission filter (ACL) | **Done** | Normalized Acl + per-source live probe (Box) |
| Semantic retrieval | **Done** | Titan embeddings, pre-embedded at sync, relevance floor |
| Unified answer path | **Done** | Cross-source answer keeps who-to-ask + decision |
| Proactive notifications | **Done** | Notable record → notify owners; pluggable sender |
| Webhook receivers | **Done (generic)** | `/api/webhook/<connector>`, signature-verified |
| Tests + CI | **Done** | Golden eval + interface test, run on every push |
| Deployment | **Not done** | Runs locally; Lambda/AgentCore/DynamoDB coded, not deployed |

**Overall: ~80% of a real product.** The engine, permissions, retrieval, and
plugin model are complete and tested. The gap is live-proving the remaining
three connectors and deploying.

---

## What's strong

- **The connector abstraction.** One normalized record, a clean protocol, an
  auto-discovering registry. New sources are additive, not invasive.
- **The permission model.** A normalized ACL enforced identically across every
  source, with a per-source live probe where membership is authoritative (Box).
  The asker's permissions decide visibility — not application logic.
- **One semantic index over all sources.** Box, GitHub, and any future connector
  share the same recall + ranking, so an answer can cite across sources.
- **Honest degradation.** Every connector works without its token; the LLM path
  falls back offline. Nothing hard-crashes on a missing dependency.
- **Regression protection.** A golden eval pins retrieval + the permission
  boundary; it already caught a real bug (off-topic questions returning a record).

---

## What's left (the finish list)

1. **Live-test Slack, Confluence, Calendar** against real tenants (need tokens).
   The code paths are real but unexercised.
2. **Deploy** — container to App Runner/ECS with a public URL; deploy the
   DynamoDB activity table (kill the JSONL fallback).
3. **Durable store at scale.** The record store is a JSON file today (explicitly
   a placeholder). Swap to pgvector / OpenSearch behind the same `put/all` API
   for real volume and concurrency.
4. **Secrets + OAuth.** Per-connector tokens belong in Secrets Manager with
   refresh, not `.env`.
5. **Real notification delivery.** The detection + targeting is done; register a
   Slack/email sender for actual push.

---

## Design principles (to keep it general)

- **No source is special.** Box is a connector like any other; the core must
  never import a specific connector. (`core/wiring.py` registers source probes
  at the edge to keep this true.)
- **No question is special.** Retrieval and who-to-ask resolve from data
  (components, people cards, embeddings), not from any hardcoded question list.
  Example questions are test fixtures, not product logic.
- **Permissions are the product, not a feature.** Any change that lets an answer
  include a source the asker can't read is a defect, and the golden eval guards it.
- **Team-agnostic by config.** Components, enabled connectors, and the identity
  map live in `config/workspace.yaml`; a new team is a new config, not new code.
