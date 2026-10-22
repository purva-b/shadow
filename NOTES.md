# Build notes

Running log for the 6-week portfolio build. "Later" holds work deferred out of
the current week's scope so it isn't forgotten.

## Week 1

### Latency (step 3) — honest status
Client/token caching and the shared `requests.Session` are in, and `_decisions`
now makes **1 Box call instead of 1 + N** (verified by counting HTTP calls, not
wall-clock). The `<5s` "Done when" could NOT be demonstrated on wall-clock during
this work because **Box itself was intermittently timing out** (the 30s request
ceiling was being hit on live reads) — a server-side condition no client caching
fixes.

RE-MEASURED (2026-10-19, later): with people-cards AND the org chart now cached
and Box responsive, warm answers landed at **2.4s and 3.9s — under the 5s
target**. Early samples in the same run were 30s+ when Box itself timed out on the
one remaining cold Box read (`_decisions`). Conclusion: <5s is ACHIEVED when Box
is healthy; the variance is Box's server-side latency, not our code.

## Later
- **Unify `agents/` → `core/`**: DONE (Week 4). The Box pipeline's structured
  logic moved to `core/structured.py` and the reasoning layer to `core/llm.py`;
  `core/` no longer imports `agents` at all. `agents/answer.py` re-exports for
  back-compat.
- **Restricted-only refusal messaging on the connector path**: DONE (Week 4).
  `core/answer_connectors.py` now gives a full refusal a readable-owner pointer
  (parity with the Box pipeline), proven leak-free by `tests/test_connector_refusal.py`.
- **pyright `basic` → `standard`**: DONE (Week 4). Tightened in `pyproject.toml`;
  it caught a real `log_message` override-signature bug in `agents/app.py`.
- **Box OAuth**: Week 1 used a dev login only (per owner's call). Real OAuth app +
  session login is still needed; scaffold exists conceptually, not built.
  (Scheduled for Week 6 — the deploy/secrets week.)
