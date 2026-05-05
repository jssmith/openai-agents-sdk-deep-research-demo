# Changelog

This file summarizes the diff between this fork and the upstream
[steveandroulakis/openai-agents-demos](https://github.com/steveandroulakis/openai-agents-demos)
snapshot it was forked from. Per-commit history is in `git log`.

## Architectural changes

- **Agentic refactor.** The original linear pipeline (Triage → Clarifying →
  Instruction → Planner → Search → Writer → PDF) was folded into a single
  orchestrator agent that drives the workflow through tool calls. Required
  Pydantic-typed tool arguments enforce step ordering — the runtime
  structurally prevents skipping steps. Determinism comes from the workflow
  shape, not post-hoc validators.
- **Generic elicitation primitive.** Clarifying questions became a generic
  one-at-a-time elicitation contract: the agent calls `elicit_user`, the
  workflow publishes a single pending elicitation, and the frontend delivers
  the response back via a workflow update. The same primitive handles any
  future human-in-the-loop step.
- **Parallel tool calls.** `query_data_warehouse` and
  `generate_research_image` are issued in the same agent turn so they
  execute concurrently. `run_parallel_research` fans out 4–6 research
  worker sub-agents.
- **Output-typed termination.** The orchestrator finalizes by emitting a
  structured `FinalizeReportRequest` as its final response, rather than
  calling a `finalize_report` tool. (gpt-5 occasionally serialized large
  tool arguments as a text message, leaving `research_completed=False`.)

## Demo affordances

- **Failure injection on the data warehouse activity.** Two env-var knobs
  (`DEMO_DATA_WAREHOUSE_RETRY_FAILURES`, `DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS`)
  stage retry-recoverable failures and slow attempts on demand. Off by
  default for clean recordings. See README.md → "Failure injection".
- **`scripts/crash-worker`.** SIGKILLs a named worker instance for the
  worker-death beat. Enables the "Temporal moves the in-flight activity
  to a fresh worker" recipe.
- **Cached image fast-path.** `DEMO_RESEARCH_IMAGE_PATH` skips live image
  generation by pointing at a saved jpeg/png, for fast deterministic
  reruns during recording sessions.
- **Topic-specific UI labels.** On its first elicit, the orchestrator
  commits a three-card progress plan (planning / collecting / writing)
  with topic-specific titles and details. The UI advances against the
  workflow's `current_activity` rather than a generic boilerplate
  timeline.

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
  removed; `scripts/start-clean-worker` is the documented entry point.
