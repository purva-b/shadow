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
fixes. The code reduces the work per answer; wall-clock should land under 5s when
Box is responsive. Re-measure when Box is healthy before calling this "Done".

## Later
- **Unify `agents/` → `core/`**: two answer paths exist (`agents/answer.py` Box
  pipeline, `core/answer_connectors.py` connector path, now the default). Fold the
  Box pipeline's structured-field logic fully into `core/` and retire the
  duplicate. Belongs with Week 4's answer-agent unification.
- **Restricted-only refusal messaging on the connector path**: the connector path
  never leaks (ACL filter blocks unreadable records) and notes a withholding, but
  it lacks the Box pipeline's explicit "the only source is restricted → full
  refusal" message. Add when unifying the answer paths.
- **Box OAuth**: Week 1 used a dev login only (per owner's call). Real OAuth app +
  session login is still needed; scaffold exists conceptually, not built.
- **pyright `basic` → `standard`**: tighten once `core/` is fully typed.
