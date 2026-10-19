<div align="center">

# 👻 Shadow

### The AI teammate that remembers everything — and only tells you what you're allowed to know.

Shadow learns everything a team says across **Box, GitHub, Slack, Confluence, and calendars**,
builds one searchable memory, and answers anyone's questions with **cited sources** —
strictly within **their own permissions**.

`Python` · `FastAPI` · `React + TypeScript` · `AWS Bedrock (Strands + Titan)` · `pluggable connectors`

</div>

---

## Why Shadow is different

Most "chat with your docs" tools do two things badly: they're wired to **one** source, and they
**leak** — if the model retrieved it, you see it. Shadow is built the opposite way.

- 🔌 **Pluggable by design.** Every source — Box, GitHub, Slack, Confluence, Calendar — normalizes to
  one `SourceRecord`. The entire reasoning core is source-agnostic. Adding a source is **one file**.
- 🔒 **Permissions are the product.** Retrieval recalls candidates; then a per-asker ACL filter drops
  anything you can't read — enforced identically across every source. **Ask the same question as two
  people, get two different answers.** That's not a feature flag; it's the architecture.
- 🧠 **One semantic index over everything.** AWS Bedrock Titan embeddings (1024-dim) rank by meaning
  across all sources at once, so an answer can cite a GitHub PR and a Box doc in the same breath.
- 🤖 **Proactive, not just reactive.** On ingest, Shadow detects notable changes (a decision moved, a
  PR landed) and notifies the owner — unprompted.
- 🛡️ **Honest by construction.** Every connector degrades gracefully without its token; a golden-eval
  CI harness guards retrieval and the permission boundary on every push.

---

## How it works

```
  Sources (plugins)              Core (source-agnostic)                 Surface
┌──────────────┐
│ Box          │ ─┐
│ GitHub       │  │   fetch ──► SourceRecord ─┐
│ Slack        │  ├──────────────────────────► ingest ──► store ──► semantic index
│ Confluence   │  │     (one normalized shape) │          (Titan embeddings)
│ Calendar     │ ─┘                            │
└──────────────┘                               ▼
                                   retrieve  (semantic + exact match)
                                               ▼
   identity map ─────────────────►  ACL filter  (per-asker permission)
                                               ▼
                                     answer  (cited prose + who-to-ask + decision)
                                               │
                                     FastAPI  +  React/TS UI
                                               │
                            ingest also ──► proactively notify owners
```

**The one idea:** every source becomes one `SourceRecord`, so the core never knows or cares where a
fact came from. Permissions, retrieval, ranking, and answering all operate on that single shape.

---

## The connector contract

Adding a source is implementing one small interface (`connectors/base.py`):

```python
class Connector(Protocol):
    name: str
    def discover(self, since) -> Iterable[SourceRef]:   ...   # what's new
    def fetch(self, ref) -> SourceRecord:                ...   # normalize one item
    def resolve_acl(self, ref) -> Acl:                   ...   # map native perms -> one model
    def subscribe(self) -> WebhookSpec | None:           ...   # optional push
```

The registry auto-discovers any `*_connector.py` — drop the file, add one line of config, done.
Five ship today: **Box, GitHub, Slack, Confluence, Google Calendar.**

---

## Permissions, done right

Each source expresses access differently; Shadow maps them all onto one model:

| Source | Native permission | Normalized to |
|---|---|---|
| Box | folder collaborations | live `can_read` probe per asker |
| GitHub | repo visibility / collaborators | public → internal, private → restricted + readers |
| Slack | channel membership | public → internal, private → restricted + members |
| Confluence | space / page restrictions | restricted → page's allowed users & groups |
| Calendar | event attendees | restricted → attendees only |

At answer time, candidates are recalled, then **every record the asker can't read is dropped** — and
withheld sources are reported ("1 source was withheld"), never leaked.

---

## Quick start

```bash
cd shadow

# 1. venv (Python 3.11+, built on 3.13)
python3.13 -m venv .venv
.venv/bin/pip install box-sdk-gen fastapi "uvicorn[standard]" boto3 \
    python-dotenv requests pyyaml
# optional (agent orchestration / deploy):
#   strands-agents bedrock-agentcore bedrock-agentcore-starter-toolkit

# 2. fill in .env  (see below)

# 3. build the Box corpus (deterministic generator + uploader)
.venv/bin/python seed/generate_data.py
.venv/bin/python seed/setup_box.py      # writes seed/ids.json, prints root id

# 4. build the React UI (served by FastAPI)
cd web/ui && npm install && npm run build && cd ../..

# 5. run
.venv/bin/python -m uvicorn web.server:app --host 127.0.0.1 --port 8099
#    open http://127.0.0.1:8099

# 6. pull a source into the unified index (example: Box)
curl -X POST "http://127.0.0.1:8099/api/sync/box?limit=300"
```

### Add another source

```bash
# GitHub
export GITHUB_TOKEN=...   GITHUB_REPO=owner/name
curl -X POST http://127.0.0.1:8099/api/sync/github

# Slack / Confluence / Calendar — set that connector's env, then:
curl -X POST http://127.0.0.1:8099/api/sync/<connector>
```

Enabled connectors, team components, and the cross-source identity map live in
`config/workspace.yaml` — a new team is a new config, not new code.

---

## .env

```
BOX_CLIENT_ID=...
BOX_CLIENT_SECRET=...
BOX_ENTERPRISE_ID=...
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
AWS_REGION=us-east-1
# per-connector (optional, set the ones you use):
GITHUB_TOKEN=            GITHUB_REPO=
SLACK_BOT_TOKEN=         SLACK_CHANNELS=
CONFLUENCE_BASE_URL=     CONFLUENCE_EMAIL=     CONFLUENCE_API_TOKEN=
GCAL_ACCESS_TOKEN=       GCAL_CALENDAR_ID=
```

The Box app is a **Server app with Client Credentials Grant (CCG)**, authorized in the Admin Console
with: Read/write all files, **Manage AI**, Manage users, Manage groups, Manage webhooks, Manage
enterprise properties, and **Generate user access tokens**.

---

## Layout

```
connectors/   the plugin layer — base interface, registry, 5 source adapters
core/         source-agnostic engine: ingest, retrieve, embeddings, acl,
              identity, store, notify, sync, wiring
agents/       Box-native answer/ingest pipeline + Strands/Bedrock helpers
web/          FastAPI backend + React/TS single-page UI
seed/         deterministic data generator + Box setup
scripts/      golden eval, interface test, smoke + reset
config/        per-workspace config (components, connectors, identity map)
```

- **`connectors/`** never imports `core/` — strict layering keeps sources pluggable.
- **`core/acl.py`** holds the permission model; `core/wiring.py` registers per-source live probes at
  the edge so the core stays source-agnostic.
- **`core/retrieve.py`** blends semantic cosine similarity with an exact-match boost and a relevance
  floor (so off-topic questions correctly return nothing).

---

## Tests & CI

```bash
.venv/bin/python scripts/eval_golden.py      # retrieval + permission boundary (golden cases)
.venv/bin/python scripts/test_phase1.py      # connector interface + identity + ACL
```

Both run on **every push** via GitHub Actions (`.github/workflows/ci.yml`), fully offline — no AWS or
source credentials required for the test lane. The golden eval already caught a real relevance bug
before it shipped.

---

## Graceful degradation

Nothing hard-fails on a missing dependency:

- **Embeddings unavailable** → retrieval falls back to the exact/keyword lane.
- **Bedrock down** → who-to-ask and decision detection use deterministic heuristics.
- **A connector's token missing** → that connector yields nothing instead of crashing.
- **DynamoDB absent** → the activity feed writes a local JSONL file.

---

## Status

~80% of a real product. Engine, permissions, retrieval, the 5-connector plugin model, unified
cross-source answers, proactive notifications, webhook receivers, and CI are **built and tested**.
Box + GitHub are live-verified end-to-end; Slack/Confluence/Calendar are complete code awaiting live
tenants. Deployment (Lambda/AgentCore/DynamoDB) is coded, not yet deployed.

See [`STATUS.md`](STATUS.md) for the full build table and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
connector design.

---

<div align="center">
<sub>Built as a deep-dive into permission-aware retrieval and pluggable-source architecture.</sub>
</div>
