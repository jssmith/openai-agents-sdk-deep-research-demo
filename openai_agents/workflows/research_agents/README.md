# Research agents

The agentic deep-research demo runs a single orchestrator agent that drives
the entire research pipeline through tool calls. There is no chain of
handoffs and no per-stage agent: the orchestrator talks to the user, fans
out research workers, queries the data warehouse, generates a thematic
image, and emits the final report itself as a structured response.

## Files

- **`orchestrator_agent.py`** — the top-level agent. Runs inside
  `InteractiveResearchWorkflow` via `Runner.run`. Its tools are:
  - `elicit_user(message, progress_plan?)` — ask the user one question and
    wait for the answer; the workflow blocks until a response is delivered
    via the FE-BE elicitation contract. Required exactly twice.
  - `run_parallel_research(subqueries)` — fan 4–6 `ResearchWorkerAgent`
    sub-agents out in parallel, gather `SearchSummary` outputs.
  - `query_data_warehouse(query)` — call the
    `fetch_data_warehouse_context` activity. Issued in parallel with
    `generate_research_image` in the same agent turn.
  - `generate_research_image(image_prompt)` — call the `generate_image`
    activity to produce a thematic image.
  - Terminal action: emit a `FinalizeReportRequest` Pydantic model as the
    structured final response. The runtime parses this into the report.

  Determinism is enforced *structurally* through required Pydantic-typed
  tool arguments — there is no validator step. The workflow rejects
  out-of-order tool calls (e.g. `run_parallel_research` before two
  elicitations have completed).

- **`research_worker_agent.py`** — the small sub-agent the orchestrator
  fans out for each parallel subquery. Uses `WebSearchTool` and returns a
  `SearchSummary`.

- **`research_models.py`** — Pydantic models shared across the workflow
  and the BFF: `UserQueryInput`, `ElicitationResponseInput`, `Elicitation`,
  `ReportData`, `ResearchInteractionDict`.

## Execution shape

```
elicit_user (progress_plan committed)        ← Q1
elicit_user                                  ← Q2 (informed by A1)
run_parallel_research(subqueries[4..6])      ← parallel sub-agents
query_data_warehouse  ║  generate_research_image   ← same turn, parallel
                      ║
emit FinalizeReportRequest                   ← terminal structured output
```

## Models

- **Orchestrator**: `gpt-5` by default, configurable via `ORCHESTRATOR_MODEL`,
  `ORCHESTRATOR_REASONING_EFFORT`, `ORCHESTRATOR_VERBOSITY`.
- **Research worker**: `gpt-5-mini` by default, configurable via
  `RESEARCH_WORKER_MODEL`. Uses `WebSearchTool` for evidence gathering.
- **Image generation**: `gpt-image-1` (called via the
  `generate_image` activity, not from an agent).
