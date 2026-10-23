# Review notes: `demo-recovery-guardrails`

Code review of the branch (commits `07f18ea`..`0d39633` plus uncommitted work),
written 2026-10-22.

## Summary

The branch hardens the research demo against model failures:

- per-attempt model fallback: gpt-5, then gpt-5-mini, then local Ollama
- validated env config (`openai_agents/demo_config.py`)
- a temporalio upgrade past the payload-converter poison-pill bug

As committed, it also added a deterministic no-model "floor" report and an
opt-in wall-clock budget, so that every run would end in a report.

## Decision: don't fake success

Robustness means two things: give work enough time to finish (no false
timeouts), and fall back to a local model when cloud models fail. It does not
mean reporting success that didn't happen. So:

- The floor report is removed (`deterministic_report.py` and its tests). It
  marked runs "completed" even when no research or writing happened.
- The wall-clock budget (`DEMO_TOTAL_BUDGET_SECONDS`) is removed. It cut runs
  short to return the floor, and because it counted the user's answer time it
  was a false timeout.
- If every model layer fails, the workflow fails with a non-retryable
  "Research could not be completed" error. The backend reports `failed`, and
  the UI says so and points to the Temporal UI.

This is consistent with the independent assessment below (#1, #5, #6).

## Findings

### Fixed

1. **The floor report and budget timer** are removed (see the decision above).
2. **Errors around synthesis are narrowed.** Only `ActivityError`,
   `AgentsException` and `AgentsWorkflowError` count as "the agent could not
   produce a report" and fail the run (see `_SYNTHESIS_FAILURES` in
   `interactive_research_workflow.py`). Anything else fails the workflow task
   so it can be fixed and replayed. Without the explicit non-retryable
   `ApplicationError`, an `AgentsException` such as `MaxTurnsExceeded` would
   leave the workflow retrying its task forever.
3. **The local layer was starved in the "cloud hangs" case.** With
   schedule-to-close at 240s, two hung cloud attempts (2 x (100 + 3) = 206s)
   left only about 34s of the local model's 90s timeout. The default is now
   300s, and the loader enforces
   `(local_attempt - 1) * (start_to_close + retry_delay) + local_http_timeout <= schedule_to_close`.
4. **The local layer received requests it can't serve.** The research workers
   use the hosted `WebSearchTool`, which Ollama's Chat Completions API cannot
   run, so their local attempt always failed. On the local attempt the hosted
   tools are now dropped and the model answers from its own knowledge, told to
   begin with `MEMORY_ANSWER_PREFIX` ("From model knowledge, not a web
   search:"), so the answer is never mistaken for search results
   (`_LocalModel` in `model_provider.py`).
5. **Ending the run mid-flight.** If the user ends the run and the model then
   fails, the result is "Research ended by user", not a failure. There is a
   regression test.
6. **Missing config validation.** The loader now enforces:
   - `local_attempt` is between 1 and `max_attempts`
   - the local HTTP timeout is less than start-to-close
   - model and image attempt counts are at least 1
7. **One source for config.** The orchestrator reads `image_max_attempts`
   through `load_demo_settings()` rather than its own `os.getenv`.
8. **Wrong comment on `IMAGE_MAX_ATTEMPTS`.** It claimed reading the value at
   import time keeps replay consistent across workers. It is actually safe
   because Temporal does not check retry options on replay.
9. **Undocumented 4xx behavior.** The plugin marks 4xx responses other than
   408, 409 and 429 as non-retryable, so a bad model name, an auth failure or
   a too-long context skips every fallback layer and fails the run. This is
   documented in `.env-sample` and `model_provider.py`.
10. **Small fixes:**
    - KNOWN_ISSUES and the CHANGELOG are updated
    - `scripts/demo_smoke.py` no longer has a "degraded floor" outcome
    - the fallback tests moved from `tests/test_bounded_synthesis.py` to
      `tests/test_model_fallback.py`

### Open

- **The from-memory label relies on the model following the instruction.**
  Nothing enforces it in code. Run `scripts/validate_local_model.py` with
  Ollama up to check qwen2.5:14b follows it. If it doesn't reliably, add the
  label in code (for example, in the workflow when a summary came from a
  local attempt).
- **No completion check against workflow state** (assessment #2).
  `FinalizeReportRequest.warehouse_summary` and `search_summaries` are
  model-supplied and never checked against what the tools recorded, so a
  model can claim research it didn't do. This is the remaining
  "success that didn't happen" gap.
- **Generic failure message.** When the model layers are exhausted, the
  message reads "Research could not be completed: Activity task failed". The
  detail is in the cause chain and the Temporal UI, not in the message itself.
- **The UI doesn't handle the `ended` status** (a pre-existing gap): it keeps
  polling.
- **The local `.env` loads at import time.** `load_dotenv()` runs when
  `research_worker_agent.py` is imported, so tests depend on the local `.env`.
- **No project-level dependency cooldown.** `pyproject.toml` has no
  `exclude-newer` and relies on the global `~/.config/uv/uv.toml`.
- **The `openai<2.45` cap.** Lift it once openai-agents >= 0.18.1 is past the
  cooldown.
- **Untracked files:** `.playwright-mcp/`, `soak_results.jsonl` and
  `validate-image-fix.png` should not be committed; `RUN.md` duplicates
  README/TALK_RUNBOOK.
- **"Image generation is broken" was the image cache.** The local `.env` set
  `DEMO_RESEARCH_IMAGE_PATH`, so the image activity returned the same cached
  May image in about 5ms on every run (and every soak run reports that same
  path). The line is now commented out, and the worker banner shows
  `image='live'` or `image='CACHED ...'`. The image-path fix (removing
  `image_path` from `FinalizeReportRequest`) is a separate, real fix.
- **The local `.env` sets `ORCHESTRATOR_MODEL='gpt-5-mini'`**, the same model
  as the cloud fallback, so attempt 2 is a plain retry rather than a switch to
  a faster model.
- **`qwen2.5:14b-instruct` is not pulled on the dev machine**, so the local
  fallback fails there until `ollama pull qwen2.5:14b-instruct` (or
  `DEMO_MODEL_LOCAL='qwen2.5:3b'`). `scripts/start-demo` warns about this.

## Test coverage

There are 38 passing tests and 1 skipped. Nothing runs in CI: there is no
`.github/workflows`.

| Area | File | Real vs. fake |
|---|---|---|
| Model fallback / failure | `test_model_fallback.py` | Real workflow + Temporal plugin on the time-skipping test server; **fake model** (`ChaosModelProvider`) |
| Config validation | `test_demo_config.py` | Unit |
| Attempt routing, hosted-tool routing, settings scrub | `test_model_provider.py` | Unit, with a faked `activity.info()` |
| Warehouse failure injection | `test_demo_activities.py` | `ActivityEnvironment` |
| Payload round-trip, image path | `test_payload_roundtrip.py`, `test_image_path_ground_truth.py` | Unit |
| Worker restart | `test_worker_restart.py` | **Skipped** unless `TEMPORAL_TEST_ADDRESS` is set |

The backend's `failed` status was checked once with a scratch script against
the time-skipping server. It is not a committed test.

The only real end-to-end check is the manual `scripts/demo_smoke.py`, which
uses a real server, worker and OpenAI. `soak_results.jsonl` holds 12 runs, all
successful, taking 83-125s. These were happy path only: no failure injection,
no worker kill, and a cached image.

### Gaps

- `AttemptAwareOpenAIProvider` never runs inside a real model activity in
  tests. The Ollama path is checked only by the manual
  `scripts/validate_local_model.py`.
- No test runs the full agent flow: two elicitations, then parallel research,
  then warehouse and image, then finalize. The happy-path test returns the
  final report on the first turn and skips every tool.
- The 4xx skip-all-fallbacks behavior is untested.
- There is no test that an unexpected exception fails the workflow task
  rather than the workflow.
- The worker-restart test, which covers the demo's durability moment, is
  skipped by default.
- The UI and FastAPI backend have no tests.

## TODO

- [ ] Gitignore or delete the untracked files (`.playwright-mcp/`,
      `soak_results.jsonl`, `validate-image-fix.png`) and fold `RUN.md` into
      the README (`scripts/start-demo` supersedes it)
- [ ] Run `scripts/validate_local_model.py` with Ollama up to confirm the
      local model labels from-memory answers
- [ ] Check completion against workflow state before accepting
      `FinalizeReportRequest` (warehouse summary and search summaries
      recorded by the tools), and make the happy-path test go through the tools
- [ ] Full-flow workflow test with a scripted fake model: elicitations,
      parallel research with a fake worker model, warehouse and image
      activities registered, finalize; assert the image path comes from the
      image activity
- [ ] Test `AttemptAwareOpenAIProvider` inside real activity retries by
      wrapping fake clients in place of `ChaosModelProvider`
- [ ] Test that an OpenAI 400 skips the fallback layers and fails the run
- [ ] Backend test for the `failed` status (from the scratch check)
- [ ] Handle the `ended` status in the UI
- [ ] Run the worker-restart test by default against a test-started dev server
- [ ] Add failure-injection (`DEMO_DATA_WAREHOUSE_RETRY_FAILURES=8`),
      worker-kill and cloud-outage (local fallback) modes to
      `scripts/demo_smoke.py`, plus a run with live image generation
- [ ] Add CI running ruff and pytest
- [ ] Add `exclude-newer = "7 days"` to `[tool.uv]` in `pyproject.toml`
- [ ] Lift the `openai<2.45` cap once openai-agents >= 0.18.1 clears the
      cooldown

## Independent assessment: architecture and demo credibility

This assessment prioritizes a compelling technical demo and patterns engineers
can reasonably copy. Building a useful research application is not a goal.

### Overall judgment

The project has a sound core: an agent loop with durable human input, parallel
research, external activities, and inspectable recovery. Keep that architecture.
The branch improves retry discipline and preserves partial findings, but its
emphasis on always producing a report adds complexity without necessarily
strengthening the durability demonstration.

The recommended direction is to simplify recovery semantics and separate
presentation controls from application behavior.

### Design concerns

1. **Schema validity is not successful research.** The deterministic floor can
   produce a valid document when no research happened. Its markdown discloses
   the limitation, but the workflow and UI classify it as completed research.
   Represent complete, partial, cancelled, and failed outcomes explicitly.
   Preserve available evidence without manufacturing the appearance of
   completion.

2. **Required output fields do not prove tool execution.** A model can supply
   warehouse context and search summaries without calling either tool. The
   happy-path test bypasses all research steps by returning a final report
   immediately. Check completion against recorded workflow state. The
   image-path fix establishes the right ownership principle; apply it to
   research evidence too.

3. **The orchestration policy needs a clearer purpose.** Exactly two questions,
   four to six searches, mandatory warehouse and image calls, and prescribed
   ordering give the agent little meaningful discretion. Keep agent-led
   orchestration where it chooses research strategy. Express essential
   prerequisites in code. If the exact sequence is itself required, a workflow
   controlling that sequence would be simpler.

4. **Simulation belongs behind replaceable adapters.** Failure injection,
   artificial delays, cached imagery, simulated costs, and fixture data should
   be separate from the core workflow. A small coherent dataset is sufficient;
   a real warehouse is unnecessary. Returning neighborhood commentary for
   unrelated technical questions weakens credibility.

5. **The local fallback is an optional experiment.** Its application to hosted
   web-search requests loses evidence when those requests fail; swallowing
   that failure does not make the fallback effective. Prefer removing the
   default local dependency. If retained, isolate it to a writing capability
   with a supported contract.

6. **Timing policy should reflect the presentation goal.** Per-call timeouts
   do not establish a useful overall presentation deadline. The optional timer
   includes human waits, and environment-dependent timer creation complicates
   replay. Record execution policy with the workflow and distinguish
   machine-work budgets from human waiting.

### Recommended branch direction

Keep bounded retries, payload-converter regression coverage, retained findings,
authoritative image paths, and narrower exception handling.

Prioritize honest partial outcomes, completion checks against workflow state,
and separation of demo adapters. Demonstrate two observable guarantees:
completed work survives process death, and recoverable failures retry without
repeating earlier completed steps.

The existing TODO list improves implementation coverage. These architectural
changes determine whether the implementation teaches the intended lessons.
