# Known issues

Things that are worth fixing but haven't been, with enough context that
the next person to look doesn't have to re-derive the problem. If you
hit one of these and it bites, please file an issue or PR.

## Local fallback answers from model knowledge

**Where**: `_LocalModel` in `openai_agents/model_provider.py`.

**Symptom**: Ollama cannot run hosted tools, so a research worker served by
the local model drops web search and answers from the model's own
knowledge. It is told to begin with `MEMORY_ANSWER_PREFIX`, and the
orchestrator is told to disclose such answers in the report, but both rely
on the model following instructions; nothing enforces the label in code.

**Check**: `uv run python scripts/validate_local_model.py` with Ollama up
shows whether the configured model follows it.

## Workflow code changes don't carry over running workflows

**Symptom**: after changing the workflow's code (for example its run
signature or its update handlers), runs started under the old code can't
continue, or be reloaded in the UI, on a worker running the new code.

**Why it's like this**: the demo uses no workflow versioning; every run is
short-lived and disposable.

**What to do**: after upgrading, start new runs. For anything long-lived, use
[Temporal workflow versioning](https://docs.temporal.io/develop/python/workflows/versioning).

## `RETRY_FAILURES` is capped at 9 by `max_attempts=10`

**Where**: retry policy on `query_data_warehouse` in
`orchestrator_agent.py` (the `DATA_WAREHOUSE_MAX_ATTEMPTS` constant), which
`demo_config.py` also validates against.

**Symptom**: setting `DEMO_DATA_WAREHOUSE_RETRY_FAILURES` to 10 or above is
rejected at worker startup — that many failures would exhaust the retry
budget instead of recovering.

**Why it's like this**: `max_attempts=10` is a deliberate ceiling — the
demo wants the failure-recovery beat to *recover*, not fail outright. If
you want a longer beat, raise both `DATA_WAREHOUSE_MAX_ATTEMPTS` and the env
value together; also widen `schedule_to_close_timeout` (currently 120s) since
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

**Symptom**: four commercial OpenType files (CoType Foundry) ship in this
repo and are loaded by the UI.

**Why it's like this**: inherited from the upstream subtree
(`steveandroulakis/openai-agents-demos`), not introduced by this fork.
Redistribution of commercial fonts is a license concern.

**Possible fix**: swap to a free Aeonik-alike (Inter, Geist) and update
the `@font-face` declarations in `styles.css`, or strip the fonts and
rely on the system `sans-serif` fallback.

## Model failures give a generic message

**Symptom**: when every model attempt is exhausted, the workflow fails with
"Research could not be completed: Activity task failed". The underlying
error is in the cause chain and the Temporal UI, not in the message.

## No CI

**Symptom**: `pytest` and `ruff` run only locally. The tests use the Temporal
time-skipping test server, except `test_worker_restart.py`, which starts a
local dev server (the installed `temporal` CLI, or one the SDK downloads):
the time-skipping server rejects an update on a cache-less worker that the
real server accepts.

## Image-model default may drift

**Where**: `OPENAI_IMAGE_MODEL` default in
`openai_agents/workflows/image_generation_activity.py` (currently
`gpt-image-2-2026-04-21`).

**Symptom**: pinned to a specific dated revision so demo output stays
deterministic. OpenAI may deprecate or rename this revision over time.

**Possible fix**: bump the default when the model is rotated; the env
var override is the documented escape hatch in the meantime.
