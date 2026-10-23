# Agent guidance

Read `README.md` for the architecture, setup, configuration and helper
scripts, and `TALK_RUNBOOK.md` for the presenter script. This file covers how
to work in the repo.

## Demo lifecycle

- Start the demo with `scripts/start-demo` and stop it with
  `scripts/stop-demo`. Prefer `stop-demo` over stopping individual processes
  so the whole tmux session is cleaned up together.
- When running non-interactively, pass `--no-attach`. A session started with
  `--session NAME` is stopped with `scripts/stop-demo NAME`.
- `stop-demo` does not stop a Temporal server that was running before
  `start-demo`; stop that yourself only if asked.
- `start-demo` refuses to start on invalid configuration. Fix the
  configuration; don't work around the check.
- `scripts/crash-worker` is a real `kill -9` of the worker. Use it only when
  asked to exercise the worker-death beat.

## Configuration

- Add new settings to `openai_agents/demo_config.py`, where they are parsed
  and validated, document them in `.env-sample`, and include them in the
  worker's startup banner (`openai_agents/run_worker.py`).

## Development

- Tests: `uv run python -m pytest tests`. `tests/test_worker_restart.py`
  starts a local Temporal dev server, so the `temporal` CLI must be on `PATH`.
- Lint: `uv run ruff check --select F401,F841`.
- Run both before committing, including for small changes.

## Design rules

- Fail visibly. A run that could not do the work must fail or disclose the
  reduced coverage, never fake success. Robustness comes from retries and the
  existing model fallback, not from new fallback code paths.
- Check what the workflow recorded from tool outcomes, not what the model
  says it did.
- Keep workflow code deterministic and put I/O and LLM calls in activities.
  After changing workflow code, start new runs; in-flight runs won't carry
  over (see `KNOWN_ISSUES.md`).
