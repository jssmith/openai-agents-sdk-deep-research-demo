# Temporal Interactive Deep Research Demo (OpenAI Agents SDK)

A demo of an agentic deep-research workflow built on
[Temporal](https://temporal.io) and the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python). A single
orchestrator agent runs inside a Temporal workflow and drives the entire
research loop — clarifying questions, parallel research sub-agents,
proprietary-data lookup, image generation, and a final markdown report — by
calling tools.

This repository builds on the original
[Temporal Interactive Deep Research demo by @steveandroulakis](https://github.com/steveandroulakis/openai-agents-demos)
and has since diverged substantially toward an agentic structure suitable for
talks and live demos. See [What's new in this fork](#whats-new-in-this-fork)
for details.

## Architecture

```
                      ┌────────────────────────────────────────┐
   user query  ──▶    │  InteractiveResearchWorkflow           │
                      │  (Temporal workflow, deterministic)    │
                      │                                        │
                      │   ┌────────────────────────────────┐   │
                      │   │  OrchestratorAgent             │   │
                      │   │  drives every step via tools   │   │
                      │   └─────┬─────────────┬──────┬─────┘   │
                      │         │             │      │         │
                      │  elicit_user   run_parallel  query_…  generate_…
                      │         │      _research    warehouse  research_image
                      │         ▼             │      │         │
                      │  pending_elicitation  ▼      ▼         ▼
                      └─────────┬───────  ResearchWorkerAgent  Activities
                                │             (web_search)    (data warehouse,
                                │                              image generation)
                                ▼
                          FastAPI BFF  ◀──── browser UI (chat + report card)
```

- **Workflow** (`openai_agents/workflows/interactive_research_workflow.py`):
  durable state — original query, pending elicitation, completed elicitations,
  current activity, progress plan, final report. Every user input is delivered
  via a Temporal workflow update; every status read is a query.
- **Orchestrator agent** (`openai_agents/workflows/research_agents/orchestrator_agent.py`):
  the only LLM-driven decision-maker. Required Pydantic-typed tool arguments
  enforce step ordering structurally; the orchestrator emits a structured
  `FinalizeReportRequest` as its terminal action.
- **Research worker** (`openai_agents/workflows/research_agents/research_worker_agent.py`):
  the orchestrator fans this small sub-agent out via `WebSearchTool` for each
  parallel subquery.
- **Activities**: `enterprise_data_activities.py` (proprietary data lookup
  with retry-recoverable failure injection) and `image_generation_activity.py`
  (gpt-image generation, with a cached fast-path for repeatable demos).
- **FastAPI backend** (`ui/backend/main.py`): thin BFF that starts workflows,
  forwards updates and queries, and serves the static frontend.
- **Frontend** (`ui/index.html`): vanilla-JS chat UI that polls the workflow
  for state and renders a report card on completion.

## What's new in this fork

Major changes since the
[original snapshot](https://github.com/steveandroulakis/openai-agents-demos):

- **Agentic refactor.** The original linear pipeline (Triage → Clarifying →
  Instruction → Planner → Search → Writer → PDF) was folded into a single
  orchestrator agent that drives the workflow through tool calls. Required
  Pydantic-typed tool arguments enforce ordering — the runtime structurally
  prevents skipping steps. Determinism comes from the workflow shape, not
  post-hoc validators.
- **Generic elicitation primitive.** Clarifying questions became a generic
  one-at-a-time elicitation contract: the agent calls `elicit_user`, the
  workflow publishes a single pending elicitation, the FE-BE contract
  delivers the response back via a workflow update. Cleaner than dedicated
  question/answer endpoints, and the same primitive handles any future
  human-in-the-loop step.
- **Parallel tool calls.** `query_data_warehouse` and
  `generate_research_image` are issued in the same agent turn so they
  execute concurrently. `run_parallel_research` fans out 4–6 research
  worker sub-agents.
- **Topic-specific UI labels.** On its first elicit, the orchestrator
  commits a three-card progress plan (planning / collecting / writing) with
  topic-specific titles and details. The UI advances against the workflow's
  `current_activity` rather than a generic boilerplate timeline.
- **Output-typed termination.** The orchestrator finalizes by emitting a
  structured `FinalizeReportRequest` as its final response, rather than
  calling a `finalize_report` tool — gpt-5 occasionally serialized large
  tool arguments as a text message, which left `research_completed=False`.
- **Demo-friendly failure injection.** `enterprise_data_activities.py`
  exposes `DEMO_DATA_WAREHOUSE_RETRY_FAILURES` and related knobs to stage a
  retry-recoverable failure on demand. `scripts/crash-worker` SIGKILLs a
  named worker instance for the failure-recovery beat. Off by default for
  clean recordings; one env-var flip turns the moment back on.
- **Cached image fast-path.** `DEMO_RESEARCH_IMAGE_PATH` skips live image
  generation by pointing at a saved jpeg/png. Enables fast, deterministic
  reruns during recording sessions.
- **UI overhaul.** Report card with download, dynamic title pulled from the
  markdown H1, full-report page driven by URL-based workflow IDs, amber
  "Still researching…" state, no flicker on phase transitions.
- **Stack bump.** `temporalio` 1.27.0+, `openai-agents` 0.14.6+,
  `gpt-5`/`gpt-5-mini` for orchestrator and worker.
- **PDF generation removed.** Was unused after the refactor; pulling the
  `weasyprint` dependency makes the demo install cleanly on macOS without
  cairo/pango.
- **Cleanup for publication.** Legacy per-stage agent files
  (`triage_agent.py`, `clarifying_agent.py`, `instruction_agent.py`,
  `planner_agent.py`, `search_agent.py`, `pdf_generator_agent.py`,
  `writer_agent.py`, `research_manager.py`) and the `serializable_model_activity`
  shim were removed once the orchestrator subsumed them. The full change
  history is in the git log.

## Prerequisites

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) for dependency
  management
- A Temporal server — either `temporal server start-dev` locally on
  `127.0.0.1:7233` (the default) or [Temporal Cloud](https://temporal.io/cloud)
- An [OpenAI API key](https://platform.openai.com/api-keys) with access to
  `gpt-5` and `gpt-image-1` (the orchestrator model and image model are
  configurable via env vars; see `openai_agents/workflows/research_agents/orchestrator_agent.py`)

## Setup

```bash
# 1. Install dependencies
uv sync

# 2. Copy and edit the env file
cp .env-sample .env
$EDITOR .env  # at minimum, set OPENAI_API_KEY

# 3. Start a local Temporal dev server (in its own terminal)
temporal server start-dev
```

To use Temporal Cloud instead, uncomment `TEMPORAL_PROFILE=cloud` in `.env`
and run:

```bash
temporal config set --profile cloud --prop address   --value "<your-cloud-address>"
temporal config set --profile cloud --prop namespace --value "<your-namespace>"
temporal config set --profile cloud --prop api_key   --value "<your-api-key>"
```

See [Temporal environment configuration](https://docs.temporal.io/develop/python/environment-configuration)
for details.

## Running the demo

Three terminals:

```bash
# Terminal 1: worker
uv run openai_agents/run_worker.py
#   or, with the demo-friendly defaults baked in:
bash scripts/start-clean-worker

# Terminal 2: BFF + static frontend
uv run ui/backend/main.py
#   serves http://127.0.0.1:8234

# Terminal 3: Temporal dev server (if not running already)
temporal server start-dev
#   Temporal UI: http://localhost:8233
```

Open <http://127.0.0.1:8234> in a browser, ask a research question, answer
the two clarifying questions, and watch the workflow run through to a final
markdown report. The Temporal UI shows the workflow history side-by-side.

![UI screenshot](ui/public/images/ui_img.png)

The full end-to-end run takes 1–3 minutes depending on web-search latency
and which failure-injection beats are enabled.

## Demo helpers

The `scripts/` directory contains presentation helpers, not production
patterns:

- `start-clean-worker [name]` — start a worker with the recording-friendly
  defaults baked in. Pass a name to run multiple instances in parallel
  behind the same task queue.
- `crash-worker [name]` — SIGKILL a named worker. Used during the
  failure-recovery beat to demonstrate that Temporal resumes the workflow
  on a fresh worker without losing state.
- `stop-demo-workers` — graceful shutdown of all worker instances tracked
  by `.demo-worker*.pid`.
- `check-ports` — quickly check who's listening on 7233 / 8233 / 8234.
- `show-history <workflow-id>` — pretty-printed activity / failure /
  completion event timeline from `temporal workflow show`.
- `demo-status <workflow-id>` — hit the BFF status and result endpoints
  for a workflow.

The `DEMO_*` environment variables in `.env-sample` control how aggressive
the failure injection is. The shipped defaults are recording-clean (no
crashes, fast warehouse). Flip them on for the failure-recovery moment.

## Development

```bash
# Lint
uv run ruff check --select F401,F841

# Type check
uv run pyright .
```

## Attribution

Original work © Steve Androulakis (see commit history for the upstream
import). Substantial enhancements for the agentic structure described in
[What's new in this fork](#whats-new-in-this-fork) by Johann Schleier-Smith.
Full per-commit attribution is in `git log`.

## License

MIT — see [LICENSE](LICENSE).
