"""Top-level orchestrator agent for the agentic deep-research demo.

The orchestrator runs inside the InteractiveResearchWorkflow via Runner.run and
drives the entire research process through tool calls. Workflow state is exposed
to tools through the run context, which is the workflow instance itself.

Determinism is enforced *structurally*: required Pydantic-typed tool arguments
mean the agent cannot reach finalize_report without having gathered every input
(clarification answers, search summaries, warehouse summary, image path,
written report). No post-hoc validator is needed.
"""

import os
from datetime import timedelta
from typing import Annotated, Any

from agents import Agent, RunContextWrapper, Runner, function_tool
from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from openai_agents.workflows.demo_failure_activities import (
    SearchBranchRequest,
    prepare_web_search_branch,
)
from openai_agents.workflows.enterprise_data_activities import (
    DataWarehouseRequest,
    DataWarehouseResult,
    fetch_data_warehouse_context,
)
from openai_agents.workflows.image_generation_activity import (
    ImageGenerationResult,
    generate_image,
)
from openai_agents.workflows.research_agents.research_worker_agent import (
    SearchSummary,
)
from openai_agents.workflows.research_agents.writer_agent import (
    ReportData,
    new_writer_agent,
)

# ctx.context is always an InteractiveResearchWorkflow at runtime, but we type
# it as Any here to avoid a circular import (the workflow imports this module).


SYSTEM_PROMPT = (
    "You are a research orchestrator. Given a user query, you direct the entire "
    "research process by calling tools. You must call them in the following order, "
    "and you cannot complete the task by skipping any step.\n"
    "\n"
    "1. ask_user_clarifications: ask 2-3 clarifying questions that narrow the user's "
    "intent. Wait for the answers (the tool returns them as a dict).\n"
    "\n"
    "2. run_parallel_research: decompose the clarified query into 4-6 focused subqueries "
    "and dispatch them in parallel. Each subquery must explore a distinct angle of the "
    "topic. The tool returns a list of SearchSummary objects.\n"
    "\n"
    "3. query_data_warehouse: look up proprietary internal context for the topic.\n"
    "\n"
    "4. generate_research_image: create an evocative thematic image for the report. "
    "Provide a 2-sentence visual description focused on atmosphere and metaphor. "
    "Never request text, labels, charts, or numbers in the image.\n"
    "\n"
    "5. write_report: hand the original query, the search summaries, the warehouse "
    "summary, and the image description to the writer sub-agent. The tool returns a "
    "ReportData with markdown_report, short_summary, and follow_up_questions.\n"
    "\n"
    "6. finalize_report: pass back every gathered piece (report, image_path, "
    "warehouse_summary, search_summaries). The system requires all of them - you "
    "literally cannot finalize with anything missing.\n"
    "\n"
    "Be efficient. Do not call tools out of order or repeat them. Do not narrate to the "
    "user between tool calls."
)


# ---------------------------------------------------------------------------
# Tool I/O models
# ---------------------------------------------------------------------------


class ClarificationsRequest(BaseModel):
    """Questions the orchestrator wants the user to answer."""

    questions: Annotated[list[str], Field(min_length=2, max_length=3)]


class ParallelResearchRequest(BaseModel):
    """Subqueries to fan out as parallel research worker sub-agents."""

    subqueries: Annotated[list[str], Field(min_length=4, max_length=6)]


class FinalizeReportRequest(BaseModel):
    """Terminal payload. Every field is required - the type is the validator."""

    report_data: ReportData
    image_path: Annotated[str, Field(min_length=1)]
    warehouse_summary: Annotated[str, Field(min_length=1)]
    search_summaries: Annotated[list[SearchSummary], Field(min_length=3)]


# ---------------------------------------------------------------------------
# Tools (workflow-state and sub-agent invocations)
# ---------------------------------------------------------------------------


@function_tool
async def ask_user_clarifications(
    ctx: RunContextWrapper[Any],
    request: ClarificationsRequest,
) -> dict[str, str]:
    """Ask the user 2-3 clarifying questions and wait for their answers.

    Returns a mapping of question -> answer.
    """
    wf = ctx.context
    return await wf.tool_ask_user_clarifications(request.questions)


@function_tool
async def run_parallel_research(
    ctx: RunContextWrapper[Any],
    request: ParallelResearchRequest,
) -> list[SearchSummary]:
    """Dispatch 4-6 research worker sub-agents in parallel and gather their summaries."""
    wf = ctx.context
    return await wf.tool_run_parallel_research(request.subqueries)


@function_tool
async def query_data_warehouse(
    ctx: RunContextWrapper[Any],
    query: str,
) -> str:
    """Look up proprietary internal context from the enterprise data warehouse."""
    request = DataWarehouseRequest(query=query)
    result: DataWarehouseResult = await workflow.execute_activity(
        fetch_data_warehouse_context,
        request,
        start_to_close_timeout=timedelta(seconds=30),
        # Generous schedule_to_close so the retry survives a coincident worker
        # outage during the failure-recovery demo path.
        schedule_to_close_timeout=timedelta(seconds=300),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=4),
            backoff_coefficient=1.0,
            maximum_interval=timedelta(seconds=4),
            maximum_attempts=2,
        ),
    )
    return (
        f"Proprietary data warehouse context ({result.source}, "
        f"units={result.units_consumed}, "
        f"estimated_cost=${result.estimated_cost_usd:.2f}): {result.summary}"
    )


@function_tool
async def generate_research_image(
    ctx: RunContextWrapper[Any],
    image_prompt: str,
) -> str:
    """Generate an evocative thematic image. Returns the saved image_path on success.

    The prompt should be 2 sentences focused on atmosphere/metaphor and must end with
    "The image contains no text, numbers, or labels."
    """
    wf = ctx.context
    result: ImageGenerationResult = await workflow.execute_activity(
        generate_image,
        args=[image_prompt, None],
        start_to_close_timeout=timedelta(seconds=180),
    )
    if not result.success or not result.image_file_path:
        # Surface a clear error to the agent so it can retry with a different prompt.
        raise RuntimeError(
            f"Image generation failed: {result.error_message or 'no path returned'}"
        )
    wf.set_image(result.image_file_path, image_prompt)
    return result.image_file_path


@function_tool
async def write_report(
    ctx: RunContextWrapper[Any],
    query: Annotated[str, Field(min_length=1)],
    search_summaries: Annotated[list[SearchSummary], Field(min_length=3)],
    warehouse_summary: Annotated[str, Field(min_length=1)],
    image_description: Annotated[str, Field(min_length=1)],
) -> ReportData:
    """Hand findings to the writer sub-agent and return a structured ReportData.

    All four arguments are required - the writer cannot produce a report without them.
    """
    bullets = "\n".join(
        f"- ({s.query}) {s.summary}" for s in search_summaries
    )
    findings = (
        f"Original query: {query}\n\n"
        f"Search findings:\n{bullets}\n\n"
        f"Proprietary warehouse context:\n{warehouse_summary}\n\n"
        f"Visual concept:\n{image_description}"
    )
    writer = new_writer_agent()
    result = await Runner.run(writer, findings)
    return result.final_output_as(ReportData)


@function_tool
async def finalize_report(
    ctx: RunContextWrapper[Any],
    request: FinalizeReportRequest,
) -> str:
    """Terminal tool: store the report, mark the workflow complete, and exit.

    The required structural arguments mean the agent cannot reach this step without
    having produced a report, an image_path, a warehouse_summary, and at least three
    search_summaries.
    """
    wf = ctx.context
    wf.complete_research(request.report_data, request.image_path)
    return "Research finalized."


# ---------------------------------------------------------------------------
# Helper used by the workflow (kept here to colocate failure-injection setup)
# ---------------------------------------------------------------------------


async def prepare_first_search_branch(query: str, total_branches: int) -> None:
    """Run the demo's prepare_web_search_branch activity for branch 0.

    Kept as an explicit setup step rather than a tool so the demo failure beat
    (DEMO_SEARCH_BRANCH_PROCESS_FAILURES SIGKILLs the worker on attempt 1) is
    not exposed in the orchestrator's prompt surface.
    """
    await workflow.execute_activity(
        prepare_web_search_branch,
        SearchBranchRequest(
            query_count=total_branches,
            search_index=0,
            search_query=query,
        ),
        # start_to_close must outlast DEMO_SEARCH_BRANCH_FAILURE_AFTER_SECONDS
        # (default 20s) so the activity can actually SIGKILL the worker.
        start_to_close_timeout=timedelta(seconds=45),
        schedule_to_close_timeout=timedelta(seconds=180),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=2),
            backoff_coefficient=1.0,
            maximum_interval=timedelta(seconds=2),
            maximum_attempts=2,
        ),
    )


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def new_orchestrator_agent() -> Agent:
    return Agent(
        name="ResearchOrchestrator",
        instructions=SYSTEM_PROMPT,
        model=os.getenv("ORCHESTRATOR_MODEL", "gpt-5"),
        model_settings=ModelSettings(
            reasoning=Reasoning(
                effort=os.getenv("ORCHESTRATOR_REASONING_EFFORT", "low")
            ),
            verbosity=os.getenv("ORCHESTRATOR_VERBOSITY", "low"),
        ),
        tools=[
            ask_user_clarifications,
            run_parallel_research,
            query_data_warehouse,
            generate_research_image,
            write_report,
            finalize_report,
        ],
    )
