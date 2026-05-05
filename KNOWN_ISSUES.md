# Known issues

Things that are worth fixing but haven't been, with enough context that
the next person to look doesn't have to re-derive the problem. If you
hit one of these and it bites, please file an issue or PR.

## Silent subquery failures dead-end the orchestrator at finalization

**Where**: `tool_run_parallel_research` in
`openai_agents/workflows/interactive_research_workflow.py`, plus
`FinalizeReportRequest.search_summaries` (`min_length=3`) in
`openai_agents/workflows/research_agents/orchestrator_agent.py`.

**Symptom**: if more than `dispatched_subqueries - 3` of the parallel
research workers raise, the orchestrator only sees the surviving
`SearchSummary` objects, then trips the `min_length=3` validator on
`search_summaries` when emitting `FinalizeReportRequest` — with no clear
signal back to the agent about *why*.

**Why it's like this**: `tool_run_parallel_research` catches and logs
exceptions per subquery and returns the survivors. That kept early
prototypes resilient to flaky `WebSearchTool` calls during recording, at
the cost of hiding genuine failures from the agent.

**Possible fixes**:
- Surface a structured per-subquery failure (e.g. `SubqueryResult` union
  of summary or error) so the agent can decide to retry, drop, or fail.
- Or: raise from the tool when survivors fall below the
  `FinalizeReportRequest` floor, letting the orchestrator's normal
  retry / replanning surface kick in.

The inline code comments cross-link both ends of the contract.

## Worker-death-mid-flight recipe needs more work

**Where**: `scripts/crash-worker`, `scripts/start-clean-worker [name]`,
the orchestrator's parallel tool calls in
`openai_agents/workflows/research_agents/orchestrator_agent.py`.

**Symptom**: there is no documented turn-key way to demo "kill the
worker holding the in-flight activity and watch Temporal reassign it."
The pieces exist — a multi-instance worker launcher, a SIGKILL helper
keyed by pid file — but knowing *which* of the running workers picked
up the data-warehouse activity in real time is awkward (you have to
look up the activity's worker identity in the Temporal UI under demo
time pressure), and the orchestrator runs `query_data_warehouse` and
`generate_research_image` as parallel tool calls, so the wrong kill
also orphans the image activity.

**Why it's like this**: the recipe was drafted, but in practice during
recordings we couldn't reliably identify the right worker fast enough
to be confident in the beat. Pulled from the README rather than ship
something that fails on stage.

**Possible fixes**:
- Make the crash-worker mechanism activity-aware: pick the activity to
  fail by ID, look up its current worker through the SDK or Temporal
  API, then SIGKILL that one specifically.
- Or: pin the data-warehouse activity to a dedicated task queue and a
  dedicated worker, so killing "the warehouse worker" is unambiguous
  and doesn't touch the image worker.
- Or: switch to an activity-internal failure that the workflow surface
  treats as a worker death (`activity.fail` with a non-retryable error
  after partial work), trading literal-worker-death for a
  more reproducible reset.

`scripts/crash-worker` and the multi-instance support in
`scripts/start-clean-worker` are kept for ad-hoc experiments while
this lands.

## `RETRY_FAILURES` is capped at 9 by `max_attempts=10`

**Where**: retry policy on `query_data_warehouse` in
`orchestrator_agent.py`.

**Symptom**: setting `DEMO_DATA_WAREHOUSE_RETRY_FAILURES` to 10 or above
exhausts retries and fails the workflow rather than recovering.

**Why it's like this**: `max_attempts=10` is a deliberate ceiling — the
demo wants the failure-recovery beat to *recover*, not fail outright. If
you want a longer beat, raise both `max_attempts` and the env value
together; also widen `schedule_to_close_timeout` (currently 120s) since
the total wall time grows linearly with attempts.

## OpenAI Agents SDK tracing is globally disabled

**Where**: `set_tracing_disabled(True)` in
`openai_agents/run_worker.py`.

**Symptom**: SDK trace data is not sent to OpenAI's `/v1/traces/ingest`
endpoint.

**Why it's like this**: the endpoint isn't enabled for many keys/orgs and
returns 400, which spams the worker terminal during demos. Tracing is
turned off at worker import time so the terminal stays clean.

**If you want traces**: remove the `set_tracing_disabled(True)` call (or
gate it behind an env var).

## Aeonik fonts are inherited from upstream

**Where**: `ui/public/fonts/Aeonik-*.otf`, referenced from
`ui/src/css/styles.css`.

**Symptom**: 14 commercial OpenType files (CoType Foundry) ship in this
repo and are loaded by the UI.

**Why it's like this**: inherited from the upstream subtree
(`steveandroulakis/openai-agents-demos`), not introduced by this fork.
Redistribution of commercial fonts is a license concern.

**Possible fix**: swap to a free Aeonik-alike (Inter, Geist) and update
the `@font-face` declarations in `styles.css`, or strip the fonts and
rely on the system `sans-serif` fallback.

## No automated tests

**Symptom**: changes to the workflow / orchestrator / activities are
verified by running the demo end-to-end against a real OpenAI key.

**Why it's like this**: this repo is a demo, not a library. Adding tests
is fine; nobody's done it.

## Image-model default may drift

**Where**: `OPENAI_IMAGE_MODEL` default in
`openai_agents/workflows/image_generation_activity.py` (currently
`gpt-image-2-2026-04-21`).

**Symptom**: pinned to a specific dated revision so demo output stays
deterministic. OpenAI may deprecate or rename this revision over time.

**Possible fix**: bump the default when the model is rotated; the env
var override is the documented escape hatch in the meantime.
