# Shadow — Architecture & Connector Plugin Design

Shadow is an AI teammate that learns everything your team says (meetings, chats,
issues, docs) and answers anyone's questions with cited sources, strictly within
their own permissions.

This document describes how Shadow moves from a Box-only demo to a **fully
functional project with pluggable connectors** (Slack, Confluence, Calendars,
GitHub, …).

---

## 1. The core idea that makes plugins possible

Every source — a Slack thread, a Confluence page, a GitHub PR, a calendar event,
a Box file — is reduced to **one normalized record** before Shadow touches it:

```
SourceRecord
  source        "slack" | "confluence" | "github" | "calendar" | "box" | ...
  external_id   stable id in the origin system
  kind          meeting | chat | issue | pr | doc | event | org
  title         human title
  occurred_on   ISO date
  people        [ emails / stable handles ]
  components    [ team-defined tags ]      # was the fixed 5-value enum
  issue_ids     [ "PAY-482", "JIRA-123" ]
  body          plain text (already OCR'd / rendered)
  acl           who may read it (see §4)
  url           deep link back to the origin
```

Once a connector emits `SourceRecord`s, the **rest of Shadow does not know or
care where they came from**. Ingest (Scribe → Librarian → Cartographer),
retrieval, permission filtering, and the answer agent all operate on the
normalized shape. Adding a connector is then a self-contained plugin, not a
rewrite.

---

## 2. Current coupling (what has to be broken)

The demo wired Box into every layer. These are the exact seams to cut:

| Coupling today | File | Fix |
|---|---|---|
| `_download_text` calls the Box content API directly | `agents/ingest.py` | Connector supplies `body`; ingest never fetches |
| Metadata extraction assumes Box AI on a Box file id | `agents/ingest.py` | Extraction runs on `SourceRecord.body`, source-agnostic |
| People hardcoded `@payments.example` + `NAME_TO_EMAIL` | `agents/ingest.py` | Identity map is per-workspace config (§5) |
| Components are a fixed 5-value enum | `setup_box.py`, `answer.py` | Components are workspace-configured tags |
| Retrieval = regex `PAY-\d+` + 6-word dictionary | `answer.py` | Semantic index over `SourceRecord.body` (§6) |
| Permission = Box collaborations only | `tools.py can_read` | Pluggable ACL resolver per source (§4) |

---

## 3. The connector contract

A connector is a plugin that implements one small interface. See
`connectors/base.py` for the runnable version.

```python
class Connector(Protocol):
    name: str                      # "slack", "github", ...

    def discover(self, since: datetime | None) -> Iterable[SourceRef]:
        "List what is new/changed since a watermark (for polling)."

    def fetch(self, ref: SourceRef) -> SourceRecord:
        "Pull one item and normalize it to a SourceRecord."

    def subscribe(self) -> WebhookSpec | None:
        "Optional: register a push webhook so ingest fires on change."

    def resolve_acl(self, ref: SourceRef) -> Acl:
        "Who may read this item, in the origin system's terms."
```

Two delivery modes, both end at the same `ingest(record)`:

```
PUSH   origin webhook ──► /api/webhook/<connector> ──► connector.fetch ──► ingest
POLL   scheduler tick ──► connector.discover(since) ──► fetch ──► ingest
```

---

## 4. Permissions: the part most products get wrong

Shadow's whole value is **answering within the asker's permissions**. Each
source system expresses permissions differently, so every connector maps its
native ACL onto one model:

```
Acl
  visibility   public | internal | restricted
  allow        [ principals who may read ]   # emails / group ids
  deny         [ explicit exclusions ]
```

| Source | Native permission | Mapped to Acl |
|---|---|---|
| Box | folder collaborations | allow = collaborators |
| Slack | channel membership (private vs public) | private → allow = members |
| Confluence | space + page restrictions | allow = space members − page restrictions |
| GitHub | repo collaborators / org teams | allow = repo readers |
| Calendar | event attendees + calendar sharing | allow = attendees + shared-with |

At answer time, retrieval recalls candidates, then **drops any record whose Acl
does not admit the asker** — exactly like the current Box `can_read` filter, but
now driven by the normalized Acl so it works across every source.

---

## 5. Identity: one person, many handles

`priya@payments.example` in Box, `@priya` in Slack, `pnair` on GitHub, and
`Priya Nair` in a calendar invite are the same human. A per-workspace **identity
map** resolves all handles to one canonical principal, so permissions and
people-cards stay correct across sources.

```
identity.yaml
  - canonical: priya@acme.com
    box:    priya@payments.example
    slack:  U024BE7LH
    github: pnair
    aliases: ["Priya Nair", "Priya"]
```

This replaces the hardcoded `NAME_TO_EMAIL` dict.

---

## 6. Retrieval: from regex to semantic

The demo matched `PAY-\d+` and a 6-word component dictionary — anything else
found nothing. The functional version indexes `SourceRecord.body`:

```
ingest ──► embed(body) ──► vector index (pgvector / OpenSearch Serverless)
ask    ──► embed(question) ──► top-k recall ──► ACL filter ──► answer agent
```

Keyword + issue-id matching stays as a fast exact-match lane; the vector index
is the recall layer for natural-language questions. Both feed the same
permission filter.

---

## 7. Target layout

```
shadow/
  connectors/
    base.py            # Connector protocol, SourceRecord, Acl, SourceRef
    registry.py        # discover + load enabled connectors
    box_connector.py   # existing Box behavior, refactored to the interface
    slack_connector.py
    confluence_connector.py
    github_connector.py
    calendar_connector.py
  core/
    ingest.py          # Scribe/Librarian/Cartographer on SourceRecord
    retrieve.py        # semantic + exact recall, ACL filter (DB-side when pgvector)
    structured.py      # people cards, org chart, decisions, who-to-ask (owner-ranked)
    answer_connectors.py  # source-agnostic answer path over the store
    agent_answer.py    # optional Strands agent orchestration
    identity.py        # handle → canonical principal
    acl.py             # Acl model + "can this principal read" check
    store.py           # StoreBackend facade: jsonl (default) | pgvector
    config.py          # secrets resolution: env → Secrets Manager → .env
    token_store.py     # per-(user, connector) OAuth tokens (DynamoDB | local)
    llm.py             # Strands/Bedrock model layer
  web/
    server.py          # FastAPI: /api/*, /auth/*, /connect/*, SPA routes
    auth.py            # signed session cookie (identity is NEVER client-asserted)
    oauth.py           # Box OAuth 2.0 user login (/auth/login, /auth/callback)
    connector_oauth.py # per-connector OAuth (Slack, GitHub, Confluence, Calendar)
    ui/                # React/TS SPA: chat popup, LoginPage, ConnectionsPage
  config/
    workspace.yaml     # components, connectors enabled, identity map, taxonomy
```

---

## 9. Web surface: identity, login, and connecting sources

Shadow is a floating **chat popup** plus two full pages, all served by the one
FastAPI app from the built React SPA.

**Identity is always a signed session cookie — never the request body.**
`web/auth.py` HMAC-signs the user id into `shadow_session`; every request reads
identity through `current_user_id` / `require_user_id`, so a caller cannot
impersonate anyone by passing a `user_id`. A tampered cookie fails the
`compare_digest` check and is treated as logged out.

**User login = Box OAuth** (`web/oauth.py`). `/auth/login` → Box authorize (with
a signed anti-CSRF `state`) → `/auth/callback` verifies state, exchanges the
code for a token, reads `/users/me` to learn who you are, then mints the signed
cookie. Box authenticates; Shadow only authorizes. The flow is dormant until a
Box OAuth app is configured (`BOX_OAUTH_CLIENT_ID`); until then a dev login mints
the same cookie, so every downstream reader is identical in both modes.

**Connecting sources = per-connector OAuth** (`web/connector_oauth.py`). A
signed-in user connects Slack / GitHub / Confluence / Calendar through the same
three-legged dance; the grant is THEIRS, so the token is stored per
`(user_id, connector)` in `core/token_store.py` and a later sync for that user
reads that user's own token — seeing only what that user can see. Each provider
is inert until its OAuth app is registered and its creds set; the `/connections`
page shows a real **Connect** button when configured, else **setup steps**. No
fake buttons: an unconfigured sync returns `reason:"not_configured"` rather than
a hollow success.

```
/login        → Sign in with Box (OAuth)         → mints shadow_session
/connections  → connect/sync each source         → /connect/<name> OAuth
/             → the floating chat popup           → /api/ask (ACL-filtered)
```

---

## 10. Migration path (incremental, each step shippable)

1. **Extract `SourceRecord` + `Connector` interface** (`connectors/base.py`).
2. **Wrap today's Box flow as `box_connector.py`** — no behavior change, proves
   the interface. (scaffolded in this commit)
3. **Make ingest consume `SourceRecord`** instead of a Box file id.
4. **Add the identity map + Acl model**, replacing the hardcoded dicts.
5. **Add the semantic index** (retrieval upgrade, §6).
6. **Add connectors one at a time**: Slack → GitHub → Confluence → Calendar.
   Each is a new file + a `registry` entry; nothing else changes.
7. **Per-workspace config** (`workspace.yaml`): components, enabled connectors,
   identity — this is what makes it team-agnostic.

Each connector added after step 3 is a pure plugin: Shadow answers across it the
day it lands, with permissions enforced, because every layer below the connector
already speaks `SourceRecord`.
