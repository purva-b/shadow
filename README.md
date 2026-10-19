# Shadow — the team's last member

Shadow shadows a software team's meetings, Slack, issues, PRs, docs and org
chart (all stored in **Box**), builds structured team memory, and answers a new
hire's questions with cited sources — or tells them exactly who to ask. Box is
Shadow's memory and permission layer; AWS (Bedrock) is where Shadow thinks.

This repo is the Payments-team demo: new hire **Alex Rivera** is assigned
**PAY-482** ("Duplicate charges when payment retries after gateway timeout").

## Quick start

```bash
cd shadow
# 1. create the venv (Python 3.11+; this repo was built on 3.13)
python3.13 -m venv .venv
.venv/bin/pip install box-sdk-gen fastapi "uvicorn[standard]" boto3 python-dotenv requests
# optional (agent hosting / deploy): strands-agents bedrock-agentcore bedrock-agentcore-starter-toolkit

# 2. fill in .env (see below)

# 3. build the Box environment from scratch
.venv/bin/python seed/generate_data.py
.venv/bin/python seed/setup_box.py          # writes seed/ids.json, prints root folder id
#    -> copy the printed root id into SHADOW_ROOT_FOLDER_ID in .env

# 4. (optional) create the activity table
.venv/bin/python agents/dynamodb_setup.py

# 5. smoke test (Checkpoint 1)
.venv/bin/python scripts/smoke_test.py

# 6. build the React + TypeScript frontend (served by FastAPI)
cd web/ui && npm install && npm run build && cd ../..

# 7. run the app
.venv/bin/python -m uvicorn web.server:app --host 127.0.0.1 --port 8099
#    open http://127.0.0.1:8099
```

Reset between demo runs (≈7s): `.venv/bin/python scripts/reset_demo.py`

## Frontend (React + TypeScript)

The UI is a Vite + React + TS single-page app in `web/ui/`, built to
`web/static-react/`, which `web/server.py` serves automatically (it falls back
to the plain-HTML UI in `web/static/` if no React build is present). The backend
and its `/api` contract are identical for both.

```bash
cd web/ui
npm install            # once
npm run build          # -> web/static-react/ ; FastAPI then serves it
npm run dev            # hot-reload dev server on :5173, proxies /api to :8099
```

Requires Node 18+ (built on Node 26). For a demo, `npm run build` once and run
the uvicorn server; for editing the UI, run `npm run dev` alongside uvicorn.

## .env

```
BOX_CLIENT_ID=...
BOX_CLIENT_SECRET=...
BOX_ENTERPRISE_ID=...
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
AWS_REGION=us-east-1
AGENTCORE_RUNTIME_ARN=        # set after `agentcore launch`
SHADOW_ROOT_FOLDER_ID=        # printed by setup_box.py
# webhook (optional): BOX_WEBHOOK_KEY_1, BOX_WEBHOOK_KEY_2, BOX_WEBHOOK_ID
```

The Box app must be a **Server app with Client Credentials Grant (CCG)**,
authorized in the Admin Console, with scopes: Read/write all files, **Manage
AI**, Manage users, Manage groups, Manage webhooks, Manage enterprise
properties, and **Generate user access tokens** enabled. Re-authorize the app
whenever you add a scope, or its tokens won't carry it.

## Architecture

```
Box (memory + permissions)                AWS (reasoning)
  Sources/ Memory/ Restricted/ Quarantine   Bedrock (Claude via BEDROCK_MODEL_ID)
  metadata templates: shadowItem,           AgentCore runtime (ingest | ask)
    shadowDecision, shadowPerson            Lambda Function URL (Box webhook)
  App Users = team identities               DynamoDB shadow_activity (feed only)
  Box AI (extract_structured, ai/ask)
```

- `shadow_box/` — CCG client (`client.py`) + Box operations (`tools.py`). Answers
  call Box **as the asking App User**, so Box permissions (not prompts) decide
  what's visible.
- `agents/` — `answer.py` (ask path), `ingest.py` (Scribe / Librarian /
  Cartographer), `app.py` (AgentCore entrypoint, routes `mode`), `activity.py`
  (feed), `common.py` (Bedrock + ids helpers).
- `lambda/webhook.py` — verifies the Box signature, invokes AgentCore.
- `web/` — FastAPI backend + single-page UI (no build step).
- `seed/` — deterministic data generator, Box setup, `ids.json`.
- `scripts/` — `smoke_test.py`, `reset_demo.py`.

### Graceful degradation (brief §13)
Every path degrades so the demo never hard-fails:
- **Box AI down** → answers fall back to metadata summaries; ingestion extracts
  from pre-seeded metadata.
- **Bedrock down** → who-to-ask and decision-change use deterministic heuristics.
- **AgentCore not deployed** → `python agents/app.py` serves the same handler on
  :8080; or the backend calls `agents.ingest.ingest` directly.
- **Webhook doesn't fire** → "Ingest latest file" button.
- **DynamoDB absent** → activity feed writes `web/activity_log.jsonl`.

## Demo script

1. Open Shadow as **Alex**. Click **"Explain my assigned issue, PAY-482."** Show
   the cited sources, the **Paused · Mar 12** status, and who to ask (**Priya**;
   note **Marcus** wrote the design but moved to Platform on Sept 22).
2. Ask **"What was discussed in the Oct 14 design review about vendor pricing?"**
   Shadow refuses — the source is in `Restricted/`, which Alex's Box identity
   can't read — and names Priya/Dana. Switch to the Box tab: show `Restricted/`
   has only Dana and Priya as collaborators.
3. Drag `seed/live/2026-10-17-payments-standup.txt` into `Sources/Meetings/` in
   Box (or click **Ingest latest file**). Open **Team memory**: watch Scribe /
   Librarian / Cartographer rows appear; the decision shows **Decided ·
   awaiting confirmation**; show the task assigned to **Dana** in Box.
4. Click **Simulate Dana confirming** (or complete the task in Box). The
   decision turns **green / Decided**; show the decision file's version history
   in Box.
5. Ask the **PAY-482** question again — the answer now reflects the **decided**
   status.

## Status at hand-off

Live-verified against the real Box tenant + AWS account:

- Box environment builds from scratch (`setup_box.py`): templates, 5 App Users,
  `payments-team` group, folder tree, permissions, 23 seeded files + metadata.
- Smoke test: 6/7 pass. The 7th (Box AI Ask) needs the app re-authorized for the
  Manage AI scope — code degrades to metadata until then.
- All five demo beats verified end-to-end through the agents and the API,
  including the Box-permission privacy refusal and the Dana confirmation task.
- Bedrock: the account must have the **Anthropic use-case form** submitted in the
  Bedrock console before Claude invokes succeed; code uses deterministic
  fallbacks until then.
```
