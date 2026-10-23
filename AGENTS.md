# Agent guidance

A Temporal + OpenAI Agents SDK deep-research demo used for talks. See
`README.md` for the architecture and `TALK_RUNBOOK.md` for the presenter
script.

## Demo lifecycle

- Start the whole stack with `scripts/start-demo`. Options: `--warehouse-failures N`
  (0-9, the activity-retry beat), `--restart` (replace a running session),
  `--no-attach` (leave it in the background), `--session NAME` (default
  `research-demo`).
- Stop it with `scripts/stop-demo`, or `scripts/stop-demo NAME` for a session
  started with `--session NAME`.
- Prefer `scripts/stop-demo` over stopping individual processes so the whole
  tmux session is cleaned up together. A Temporal server that was already
  running before `start-demo` is reused and left running; one that
  `start-demo` launched is in-memory and loses its history on stop.
- `start-demo` runs the same configuration validation as the worker
  (`scripts/demo_preflight.py`) and refuses to start on bad config. Fix the
  config rather than working around the check.
- Use `--no-attach` when running non-interactively; attach later with
  `tmux attach -t research-demo`.
- Inspect a running demo with `scripts/check-ports`,
  `scripts/demo-status <workflow-id>` and `scripts/show-history <workflow-id>`.
  `scripts/demo_smoke.py` drives real runs end to end without a browser.
- `scripts/crash-worker` is a real `kill -9` of the worker; only use it when
  asked to exercise the worker-death beat.

## Configuration

- Settings come from `.env` (copy `.env-sample`), with the shell environment
  taking precedence. They are parsed and validated in
  `openai_agents/demo_config.py`; add new settings there, document them in
  `.env-sample`, and include them in the worker's startup banner.
- The banner shows the resolved settings. Trust it over numbers in the docs.

## Development

- Tests: `uv run python -m pytest tests`. `tests/test_worker_restart.py`
  starts a local Temporal dev server, so the `temporal` CLI must be on `PATH`.
- Lint: `uv run ruff check --select F401,F841`.
- Run both before committing, including for small changes.

## Design rules

- Fail visibly. A run that could not do the work must fail or disclose the
  reduced coverage, never fake success. Robustness comes from retries and the
  cloud -> cloud -> local Ollama model fallback in
  `openai_agents/model_provider.py`, not from fallback code paths.
- The workflow accepts a report only after checking the tool outcomes it
  recorded itself (`interactive_research_workflow.py`); keep evidence checks
  on recorded outcomes, not on what the model says it did.
- Keep workflow code deterministic and put I/O and LLM calls in activities.
  The demo uses no workflow versioning, so changing workflow code strands
  in-flight runs; start new runs after a change (see `KNOWN_ISSUES.md`).
- The `scripts/` helpers are presentation aids, not production patterns.
