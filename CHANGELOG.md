# Changelog

This file summarizes the diff between this fork and the upstream
[steveandroulakis/openai-agents-demos](https://github.com/steveandroulakis/openai-agents-demos)
snapshot it was forked from. Per-commit history is in `git log`.

## Demo control surface

- **`DEMO_PROFILE` removed.** The `clean` / `recovery` profiles are gone.
  Failure injection is now driven directly by
  `DEMO_DATA_WAREHOUSE_RETRY_FAILURES` (an integer 0–9): `0` is a clean
  run and `1`–`9` fail that many leading attempts and recover on the next.
  The value is validated at startup against the lookup's shared
  `DATA_WAREHOUSE_MAX_ATTEMPTS` (10) retry budget, so `10` or more is
  rejected rather than exhausting retries. If `DEMO_PROFILE` is still set in
  the environment, the worker fails fast at startup and points the user at
  the new knob.
- **`scripts/start-clean-worker` removed.** The worker is launched with a
  plain command — `uv run --env-file .env openai_agents/run_worker.py` —
  optionally prefixed with a shell export
  (`DEMO_DATA_WAREHOUSE_RETRY_FAILURES=8 …`) for the recovery beat. A
  shell export wins over `.env`, where the knob is commented out so the
  default applies.
- **Resolved-values startup banner.** The banner no longer prints
  `profile=…`. It now prints the resolved settings the worker will
  actually use, e.g. `warehouse_retry_failures=8 warehouse_outcome='recovers
  on attempt 9' warehouse_max_attempts=10 model_max_attempts=3 …`, so docs
  don't have to repeat numbers that can drift.
- **One-command launch.** `scripts/start-demo` validates the configuration,
  reuses a reachable Temporal server or starts a dev server, starts Ollama
  if the local fallback needs it, and runs the worker (in a restart loop for
  the crash beat) and backend in tmux windows. `scripts/stop-demo` stops it.
- **PID file rename.** The worker writes `.demo-worker.pid` on startup
  (was `.demo-worker-clean.pid`), and `scripts/crash-worker` targets it by
  default. Extra instances set `DEMO_WORKER_PID_FILE`
  (e.g. `.demo-worker-2.pid`) and are killed with `crash-worker 2`;
  `scripts/stop-demo-workers` still tracks `.demo-worker*.pid`.

- **Model fallback across retries.** Each model call gets generous
  timeouts, and Temporal retries move from the primary cloud model to a
  faster cloud model to a local Ollama model (`DEMO_MODEL_*`, see
  `.env-sample`). Ollama can't run web search, so on the local attempt the
  research workers answer from the model's own knowledge, and their
  summaries are labeled "From model knowledge, not a web search:". If every
  attempt fails, the
  workflow fails and the UI says so; the run never reports success it
  didn't earn. The worker validates the timeouts at startup, including that
  the local attempt gets its full timeout even if both cloud attempts hang.

## Architectural changes

- **Agentic refactor.** The original linear pipeline (Triage → Clarifying →
  Instruction → Planner → Search → Writer → PDF) was folded into a single
  orchestrator agent that drives the workflow through tool calls. Tools
  record their actual outcomes in workflow state, and the workflow checks
  those outcomes before accepting the agent's report, so the agent cannot
  claim work that never ran.
- **Generic elicitation primitive.** Clarifying questions became a generic
  one-at-a-time elicitation contract: the agent calls `elicit_user`, the
  workflow publishes a single pending elicitation, and the frontend delivers
  the response back via a workflow update. The same primitive handles any
  future human-in-the-loop step.
- **Parallel work.** `run_parallel_research` fans out 4–6 research worker
  sub-agents; `query_data_warehouse` and `generate_research_image` are
  independent and can run in the same agent turn.
- **Output-typed termination.** The orchestrator finalizes by emitting a
  structured `FinalizeReportRequest` as its final response, rather than
  calling a `finalize_report` tool. (gpt-5 occasionally serialized large
  tool arguments as a text message, leaving `research_completed=False`.)

## Demo affordances

- **Failure injection on the data warehouse activity.**
  `DEMO_DATA_WAREHOUSE_RETRY_FAILURES` stages retry-recoverable failures on
  demand. Off by default for clean recordings. See README.md → "The two
  recovery beats".
- **`scripts/crash-worker`.** SIGKILLs the worker for the worker-death beat.
- **Cached image fast-path.** `DEMO_RESEARCH_IMAGE_PATH` skips live image
  generation by pointing at a saved jpeg/png, for fast deterministic
  reruns during recording sessions.
- **Topic-specific UI labels.** The orchestrator may supply a three-card
  progress plan (planning / collecting / writing) with topic-specific
  titles and details; the UI otherwise uses fixed labels. The cards advance
  with a phase derived from the workflow's recorded tool outcomes.

## UI

- Report card with download, dynamic title pulled from the markdown H1,
  full-report page driven by URL-based workflow IDs, amber
  "Still researching…" state during long-running attempts, no flicker
  on phase transitions.

## Stack

- `temporalio` 1.27.0+, `openai-agents` 0.14.6+, `gpt-5` / `gpt-5-mini`
  for orchestrator and worker.
- PDF generation removed (it was unused after the refactor; dropping the
  `weasyprint` dependency makes the demo install cleanly on macOS without
  cairo/pango).

## Cleanup

- Legacy per-stage agent files (`triage_agent.py`, `clarifying_agent.py`,
  `instruction_agent.py`, `planner_agent.py`, `search_agent.py`,
  `pdf_generator_agent.py`, `writer_agent.py`, `research_manager.py`)
  removed once the orchestrator subsumed them.
- `serializable_model_activity` shim removed (no longer needed with the
  current `temporalio.contrib.openai_agents`).
- Dead `/api/stream` SSE endpoint removed; the UI uses inline polling.
- Three `start-worker*` shell scripts that referenced unrelated paths
  removed. `scripts/start-clean-worker` was later removed too, in favor of
  launching the worker directly with
  `uv run --env-file .env openai_agents/run_worker.py`.
