<div align="center">

# 👻 Shadow

### The AI teammate that remembers everything — and only tells you what you're allowed to know.

Shadow learns everything a team says across **Box, GitHub, Slack, Confluence, and calendars**,
builds one searchable memory, and answers anyone's question with **cited sources** —
strictly within **their own permissions**.

`Python 3.11+` · `FastAPI` · `React + TypeScript` · `AWS Bedrock (Strands + Titan)` · `Postgres/pgvector` · `pluggable connectors`

</div>

---

## The idea in one sentence

**Every source becomes one `SourceRecord`, so the reasoning core never knows or cares where a fact
came from** — and the same per-asker permission filter runs over that single shape for every source.
That is what makes Shadow both *pluggable* (a new source is one file) and *safe* (ask the same
question as two people, get two different answers — by construction, not by a flag).

---

## Why Shadow is different

Most "chat with your docs" tools fail two ways: they're wired to **one** source, and they **leak** —
if the model retrieved it, you see it. Shadow is built the opposite way.

- 🔌 **Pluggable by design.** Box, GitHub, Slack, Confluence, Calendar all normalize to one
  `SourceRecord`. The entire reasoning core is source-agnostic — it imports no connector and no
  vendor. Adding a source is **one file**.
- 🔒 **Permissions are the product.** Retrieval recalls candidates; a per-asker ACL filter then drops
  anything you can't read, enforced identically across every source. Withheld sources are *reported*
  ("1 source was withheld"), never leaked.
- 🧠 **One semantic index over everything.** AWS Bedrock Titan embeddings (1024-dim) rank by meaning
  across all sources at once, so one answer can cite a GitHub PR and a Box doc together.
- 📈 **Built to scale.** Recall runs in-process over JSONL for the demo, or pushes nearest-neighbour
  search **into Postgres/pgvector** (`ORDER BY embedding <=> query`) for real volume — the same seam,
  swapped by one env var.
- 🔑 **Real identity.** A signed session cookie carries who you are — never a request parameter. Flip
  on Box OAuth 2.0 and the dev login retires automatically; nothing downstream changes.
- 🤖 **Proactive.** On ingest, Shadow detects notable changes (a decision moved, a PR landed) and can
  notify the owner — unprompted.
- 🛡️ **Honest by construction.** Every dependency degrades gracefully when absent; a two-lane CI
  (offline fast lane + agent-path lane) guards retrieval and the permission boundary on every push.

---

## How it works

```
  Sources (plugins)              Core (source-agnostic)                 Surface
┌──────────────┐
│ Box          │ ─┐
│ GitHub       │  │   fetch ──► SourceRecord ─┐
│ Slack        │  ├──────────────────────────► ingest ──► store ──► semantic index
│ Confluence   │  │     (one normalized shape) │       (JSONL | pgvector)
│ Calendar     │ ─┘                            │
└──────────────┘                               ▼
                                   retrieve   (semantic + exact match;
                                               ANN in Postgres when pgvector)
                                               ▼
   identity map ─────────────────►  ACL filter  (per-asker permission)
                                               ▼
                                     answer   (cited prose + who-to-ask + decision)
                                               │
                         signed session ──► FastAPI  +  React/TS UI
                         (dev login | Box OAuth)      │
                                     ingest also ──► proactively notify owners
```

---

## The connector contract

Adding a source is implementing one small interface (`connectors/base.py`):

```python
class Connector(Protocol):
    name: str
    def discover(self, since) -> Iterable[SourceRef]:   ...   # what's new
    def fetch(self, ref) -> SourceRecord:                ...   # normalize one item
    def resolve_acl(self, ref) -> Acl:                   ...   # map native perms -> one model
    def subscribe(self) -> WebhookSpec | None:           ...   # optional push event
```

The registry auto-discovers any `*_connector.py` — drop the file, add one line of config, done.
Five ship today: **Box, GitHub, Slack, Confluence, Google Calendar.**

---

## Permissions, done right

Each source expresses access differently; Shadow maps them all onto one model:

| Source | Native permission | Normalized to |
|---|---|---|
| Box | folder collaborations | live `can_read` probe, per asker |
| GitHub | repo visibility / collaborators | public → internal · private → restricted + readers |
| Slack | channel membership | public → internal · private → restricted + members |
| Confluence | space / page restrictions | restricted → page's allowed users & groups |
| Calendar | event attendees | restricted → attendees only |

At answer time, candidates are recalled, then **every record the asker can't read is dropped**. If the
*only* relevant source is one you can't see, Shadow refuses and points you at a readable owner — it
never cites, quotes, or hints at the restricted record.

---

## Identity & login

Identity always comes from a **signed session cookie** (HMAC-SHA256), decided server-side — a client
can never assert who it is by passing a `user_id`. Two ways to mint that cookie:

| Mode | When | How |
|---|---|---|
| **Dev login** | local demo (`SHADOW_DEV=1`) | `POST /api/login {name}` picks a seeded App User |
| **Box OAuth 2.0** | configured (`BOX_OAUTH_CLIENT_ID` set) | `/auth/login` → Box → `/auth/callback` reads the real Box user |

When a Box OAuth app is configured, the dev login **automatically retires** (returns 403) and `/api/me`
reports `auth_mode: "oauth"` so the UI sends users to `/auth/login`. The OAuth callback mints the same
signed cookie the rest of the app already reads — so turning OAuth on changes *how* identity is
established, not a single call site that consumes it.

**Register the OAuth app:** a Box Custom App with *User Authentication (OAuth 2.0)*, redirect URI
`<PUBLIC_URL>/auth/callback` (dev: `http://127.0.0.1:8099/auth/callback`). Supply
`BOX_OAUTH_CLIENT_ID` / `BOX_OAUTH_CLIENT_SECRET` (and `SHADOW_PUBLIC_URL` in a deployment).

---

## Storage: JSONL → pgvector

The store is a facade over a pluggable **backend**, chosen by `SHADOW_STORE_BACKEND`:

| Backend | Recall | Use |
|---|---|---|
| `jsonl` *(default)* | in-process cosine scan, `O(records)` per query | demo / single node — zero infra |
| `pgvector` | `ORDER BY embedding <=> :q LIMIT k` **inside Postgres** (ivfflat index) | real volume / concurrency / durability |

Both backends share one public API (`put` / `all` / `item_for` / `count`), so `retrieve`, `ingest`,
and the server are identical either way. The backend advertises whether it can do vector recall; when
it can, the engine pushes nearest-neighbour search into the database instead of scanning in Python —
the same Titan embedding now feeds a DB index. Turning it on:

```bash
pip install -e '.[pgvector]'            # psycopg + pgvector driver (optional extra)
export DATABASE_URL=postgresql://user:pass@host/shadow
export SHADOW_STORE_BACKEND=pgvector    # jsonl is the default; this opts in
```

The schema (vector extension, `shadow_records` table, ivfflat index) is created on first write. The
default install needs none of this — pgvector is a strictly optional extra.

---

## Quick start

```bash
cd shadow

# 1. venv (Python 3.11+, developed on 3.13) + install
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'          # core + test tools
#   optional extras:  '.[agents]'  (Strands/Bedrock)   '.[pgvector]'  (Postgres backend)

# 2. fill in .env  (see below)

# 3. build the Box corpus (deterministic generator + uploader)
.venv/bin/python seed/generate_data.py
.venv/bin/python seed/setup_box.py         # writes seed/ids.json, prints root id

# 4. build the React UI (served by FastAPI)
cd web/ui && npm install && npm run build && cd ../..

# 5. run  (dev login enabled; omit SHADOW_DEV once Box OAuth is configured)
SHADOW_DEV=1 .venv/bin/python -m uvicorn web.server:app --host 127.0.0.1 --port 8099
#    open http://127.0.0.1:8099

# 6. pull a source into the unified index (example: Box)
curl -X POST "http://127.0.0.1:8099/api/sync/box?limit=300"
```

### Add another source

```bash
export GITHUB_TOKEN=...  GITHUB_REPO=owner/name
curl -X POST http://127.0.0.1:8099/api/sync/github
# Slack / Confluence / Calendar — set that connector's env, then sync/<connector>
```

Enabled connectors, team components, and the cross-source identity map live in
`config/workspace.yaml` — a new team is a new config, not new code.

---

## .env

```
# Box (server-to-server corpus access: CCG)
BOX_CLIENT_ID=...
BOX_CLIENT_SECRET=...
BOX_ENTERPRISE_ID=...
# Box OAuth 2.0 (user login — optional; when set, retires the dev login)
BOX_OAUTH_CLIENT_ID=
BOX_OAUTH_CLIENT_SECRET=
SHADOW_PUBLIC_URL=http://127.0.0.1:8099        # callback host for OAuth
# AWS Bedrock
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
AWS_REGION=us-east-1
# session signing (set a real value in any shared deployment)
SHADOW_SESSION_SECRET=
# storage (optional — defaults to jsonl)
SHADOW_STORE_BACKEND=jsonl
DATABASE_URL=                                   # required only for pgvector
# per-connector (optional, set the ones you use)
GITHUB_TOKEN=            GITHUB_REPO=
SLACK_BOT_TOKEN=         SLACK_CHANNELS=
CONFLUENCE_BASE_URL=     CONFLUENCE_EMAIL=     CONFLUENCE_API_TOKEN=
GCAL_ACCESS_TOKEN=       GCAL_CALENDAR_ID=
```

In production, secrets resolve **env → AWS Secrets Manager (`SHADOW_SECRETS_ARN`) → `.env`**, fail-open:
an unreachable secret never crashes boot (`core/config.py`). The Box CCG app is authorized in the
Admin Console with Read/write all files, **Manage AI**, Manage users/groups/webhooks, Manage
enterprise properties, and **Generate user access tokens**.

---

## Layout

```
connectors/   the plugin layer — base interface, registry, 5 source adapters
core/         source-agnostic engine: ingest · retrieve · embeddings · acl · identity
              · store (jsonl|pgvector) · structured · llm · config · notify · sync · wiring
agents/       Box-native answer/ingest pipeline (re-exports core/structured) + Bedrock helpers
web/          FastAPI backend · React/TS SPA · auth (signed session) · oauth (Box OAuth 2.0)
seed/         deterministic data generator + Box setup
scripts/      golden eval · interface test · smoke + reset
deploy/        single-container entrypoint (DynamoDB provision) + App Runner runbook
config/        per-workspace config (components, connectors, identity map, taxonomy)
tests/        pytest suite (hermetic — no live AWS/Box needed)
```

- **`connectors/` never imports `core/`, and `core/` never imports `agents/`** — strict layering keeps
  sources pluggable and the engine vendor-agnostic.
- **`core/acl.py`** holds the permission model; **`core/wiring.py`** registers per-source live probes
  at the edge so the core stays source-agnostic.
- **`core/retrieve.py`** blends semantic cosine similarity with an exact-match boost and a relevance
  floor (off-topic questions correctly return nothing).

---

## Deploy

One container runs the API and the built UI. `deploy/entrypoint.py` idempotently provisions DynamoDB
for the activity feed; `/health` is the readiness probe. See [`deploy/README.md`](deploy/README.md) for
the App Runner / ECS runbook and the Lambda → AgentCore → Bedrock production path (coded; the demo
runs the same agents locally).

```bash
docker build -t shadow .
docker run -p 8099:8099 --env-file .env shadow
```

---

## Tests & CI

```bash
.venv/bin/python -m pytest              # full hermetic suite
.venv/bin/python scripts/eval_golden.py # retrieval + permission boundary (golden cases)
```

**Every push** runs two GitHub Actions lanes (`.github/workflows/ci.yml`):

- **`check`** — ruff · pyright (standard) · pytest, fully offline. No AWS or source credentials, and it
  runs with `seed/ids.json` absent (the server degrades, it does not crash).
- **`agent-path`** — installs the `[agents]` extra and smoke-tests the Strands/Bedrock wiring that the
  fast lane deliberately skips.

The golden eval already caught a real relevance bug before it shipped.

---

## Graceful degradation

Nothing hard-fails on a missing dependency:

- **Embeddings unavailable** → retrieval falls back to the exact/keyword lane.
- **Bedrock down** → who-to-ask and decision detection use deterministic heuristics.
- **A connector's token missing** → that connector yields nothing instead of crashing.
- **DynamoDB absent** → the activity feed writes a local JSONL file.
- **`seed/ids.json` absent** → the server still imports and `/health` answers; Box endpoints degrade.
- **Secrets Manager unreachable** → config falls open to env/`.env`.

---

## Status

Engine, per-asker permissions, the 5-connector plugin model, unified cross-source answers, data-driven
who-to-ask, proactive notifications, webhook receivers, a durable pgvector store, Box OAuth login, a
single-container deploy, and two-lane CI are **built and tested**. Box + GitHub are live-verified
end-to-end; Slack/Confluence/Calendar are complete code awaiting live tenants. The AWS deploy
(Lambda/AgentCore/DynamoDB) is coded and deploy-ready, not yet pushed live.

See [`STATUS.md`](STATUS.md) for the full build table and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
connector design.

---

<div align="center">
<sub>Built as a deep-dive into permission-aware retrieval and pluggable-source architecture.</sub>
</div>
