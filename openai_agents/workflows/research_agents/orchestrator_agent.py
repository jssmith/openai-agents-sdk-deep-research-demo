"""Top-level orchestrator agent for the agentic deep-research demo.

The orchestrator runs inside the InteractiveResearchWorkflow via Runner.run and
drives the entire research process through tool calls. Workflow state is exposed
to tools through the run context, which is the workflow instance itself.

Tools record what actually happened (answers, research findings, the warehouse
result, the image outcome) in workflow state. The final output carries only the
report the model wrote; the workflow checks the recorded outcomes before
accepting it, so a model cannot claim work that never ran.
"""

import os
from datetime import timedelta
from typing import Annotated, Any, Literal, cast

from agents import Agent, RunContextWrapper, default_tool_error_function, function_tool
from agents.exceptions import ModelBehaviorError
from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning
from openai.types.shared.reasoning_effort import ReasoningEffort
from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from openai_agents.demo_config import load_demo_settings
from openai_agents.model_provider import MEMORY_ANSWER_PREFIX
from openai_agents.workflows.enterprise_data_activities import (
    DATA_WAREHOUSE_MAX_ATTEMPTS,
    DataWarehouseRequest,
    DataWarehouseResult,
    fetch_data_warehouse_context,
)
from openai_agents.workflows.image_generation_activity import generate_image
from openai_agents.workflows.research_agents.research_models import ResearchResults

# Read once at import. Retry options are not checked on replay, so workers with
# different values don't cause nondeterminism; each worker simply schedules new
# image activities with its own policy.
IMAGE_MAX_ATTEMPTS = load_demo_settings().image_max_attempts

# ctx.context is always an InteractiveResearchWorkflow at runtime, but we type
# it as Any here to avoid a circular import (the workflow imports this module).


SYSTEM_PROMPT = (
    "You are a research orchestrator. Given a user query, you direct the research "
    "by calling tools, then write the final report.\n"
    "\n"
    "1. elicit_user: ask the user EXACTLY TWO clarifying questions, one at a time. "
    "Ask question 1, wait for the answer, then ask question 2 informed by the first "
    "answer. The workflow rejects run_parallel_research until both are answered. "
    "On an elicit_user call you may also include a progress_plan: three short, "
    "topic-specific status cards the UI shows during the run:\n"
    "   - planning: 'title' is a 4-7 word phrase about what we're about to research; "
    "'detail' is one sentence about clarifying scope.\n"
    "   - collecting: 'title' is a 4-7 word phrase about gathering evidence on the "
    "topic; 'detail' is one sentence naming the angles being researched.\n"
    "   - writing: 'title' is a 4-7 word phrase about synthesizing the report; "
    "'detail' is one sentence describing the deliverable.\n"
    "\n"
    "2. run_parallel_research: decompose the clarified query into 4-6 focused "
    "subqueries, each exploring a distinct angle, and dispatch them in parallel. "
    "It returns the findings plus any failed_queries whose research could not be "
    "completed.\n"
    "\n"
    "3. query_data_warehouse and generate_research_image: both are required and "
    "independent of each other. Issue BOTH tool calls TOGETHER in the same turn, "
    "so they run in parallel; do not wait for one before calling the other. "
    "query_data_warehouse takes a concise query string. "
    "generate_research_image takes a 2-sentence prompt focused on atmosphere and "
    "metaphor; never request text, labels, charts, or numbers in the image. If it "
    "reports the image is unavailable, continue without it.\n"
    "\n"
    "When all of these have run, finish by emitting a FinalizeReportRequest as "
    "your final response.\n"
    "\n"
    "Honesty requirements for the report:\n"
    f"- If any finding begins with '{MEMORY_ANSWER_PREFIX}', say that live web "
    "search was unavailable and those parts come from model knowledge.\n"
    "- The data warehouse is a simulation; never present its output as evidence.\n"
    "\n"
    "Report style guidance for the final response:\n"
    "- markdown_report: tight, executive-ready markdown, 150-200 words MAX in total. "
    "Use exactly this structure:\n"
    "  # <topic in 4-8 words, no trailing period>\n"
    "  <1-2 sentence intro paragraph, with no label such as 'Intro:'>\n"
    "  ## <section heading>  (2-3 sections, each a `## ` heading line followed by "
    "2-3 sentences)\n"
    "  <a one-line conclusion paragraph, with no label such as 'Conclusion:'>\n"
    "  Every section heading MUST be its own line starting with `## `. Favor "
    "substance over length. No filler, no repeated caveats, no generic background.\n"
    "- short_summary: 1-2 sentences capturing the headline findings.\n"
    "- follow_up_questions: 3 suggested topics to research further.\n"
    "\n"
    "Be efficient. Do not repeat tool calls. Do not narrate to the user between "
    "tool calls."
)


# ---------------------------------------------------------------------------
# Tool I/O models
# ---------------------------------------------------------------------------


class ProgressLabel(BaseModel):
    """A status card shown in the UI during one phase of the run."""

    title: Annotated[str, Field(min_length=1, max_length=80)]
    detail: Annotated[str, Field(min_length=1, max_length=200)]


class ProgressPlan(BaseModel):
    """Topic-specific labels for the three phases of the run.

    Optional: the UI shows fixed labels for any phase the agent doesn't name.
    """

    planning: ProgressLabel
    collecting: ProgressLabel
    writing: ProgressLabel


class ElicitUserRequest(BaseModel):
    """One question for the user, optionally with progress labels for the UI."""

    message: Annotated[str, Field(min_length=1, max_length=500)]
    progress_plan: ProgressPlan | None = None


class ParallelResearchRequest(BaseModel):
    """Subqueries to fan out as parallel research worker sub-agents."""

    subqueries: Annotated[list[str], Field(min_length=4, max_length=6)]


class FinalizeReportRequest(BaseModel):
    """The orchestrator's final output: the report it wrote.

    Evidence of the work (answers, findings, warehouse result, image) is not
    repeated here; the workflow checks what its tools recorded instead.
    """

    short_summary: Annotated[str, Field(min_length=1)]
    markdown_report: Annotated[str, Field(min_length=300)]
    follow_up_questions: Annotated[list[str], Field(min_length=2, max_length=5)]


# ---------------------------------------------------------------------------
# Tools (workflow-state and sub-agent invocations)
# ---------------------------------------------------------------------------


def _model_correctable_errors(ctx: RunContextWrapper[Any], error: Exception) -> str | None:
    """Tool failure policy: tell the model only about errors it can correct.

    Invalid arguments (ModelBehaviorError) and the workflow's deliberate
    contract errors (ApplicationError, e.g. "ask both questions first") go
    back to the model. Anything else is a failure the model cannot fix, so it
    propagates and fails the run visibly.
    """
    if isinstance(error, (ApplicationError, ModelBehaviorError)):
        return default_tool_error_function(ctx, error)
    return None


tool = function_tool(failure_error_function=_model_correctable_errors)


@tool
async def elicit_user(
    ctx: RunContextWrapper[Any],
    request: ElicitUserRequest,
) -> str:
    """Ask the user a single question and wait for their answer.

    Each call publishes ONE elicitation, the workflow blocks until the user
    responds, and the response is returned as a string. Call exactly twice.
    """
    wf = ctx.context
    return await wf.tool_elicit_user(request.message, request.progress_plan)


@tool
async def run_parallel_research(
    ctx: RunContextWrapper[Any],
    request: ParallelResearchRequest,
) -> ResearchResults:
    """Dispatch 4-6 research worker sub-agents in parallel and gather their findings."""
    wf = ctx.context
    return await wf.tool_run_parallel_research(request.subqueries)


@tool
async def query_data_warehouse(
    ctx: RunContextWrapper[Any],
    query: str,
) -> str:
    """Look up internal context from the (simulated) enterprise data warehouse."""
    result: DataWarehouseResult = await workflow.execute_activity(
        fetch_data_warehouse_context,
        DataWarehouseRequest(query=query),
        start_to_close_timeout=timedelta(seconds=15),
        # Caps total time across retries: up to 9 failed attempts of ~1s plus
        # 4s gaps is ~45s, plus the successful attempt.
        schedule_to_close_timeout=timedelta(seconds=120),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=4),
            backoff_coefficient=1.0,
            maximum_interval=timedelta(seconds=4),
            maximum_attempts=DATA_WAREHOUSE_MAX_ATTEMPTS,
        ),
    )
    summary = f"{result.source}: {result.summary}"
    ctx.context.record_warehouse(summary)
    return summary


@tool
async def generate_research_image(
    ctx: RunContextWrapper[Any],
    image_prompt: str,
) -> str:
    """Generate an evocative thematic image from a 2-sentence prompt.

    Returns the saved image path, or a note that the image is unavailable.
    """
    wf = ctx.context
    try:
        image_path: str = await workflow.execute_activity(
            generate_image,
            image_prompt,
            start_to_close_timeout=timedelta(seconds=180),
            retry_policy=RetryPolicy(maximum_attempts=IMAGE_MAX_ATTEMPTS),
        )
    except ActivityError:
        # Attempts exhausted: the report goes ahead with the UI's missing-image
        # placeholder rather than waiting on, or faking, an image.
        workflow.logger.exception("Image generation failed; continuing without it")
        wf.set_image_unavailable()
        return "The image is unavailable. Continue without it."
    wf.set_image(image_path)
    return image_path


# Note: there is intentionally no finalize_report tool. The orchestrator's
# terminal action is to emit a FinalizeReportRequest as its structured final
# response (output_type below). With a tool, gpt-5 sometimes serialized the
# large argument payload as a text message instead of as a function call;
# using output_type makes the structured response *be* the natural way to
# finish.


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def new_orchestrator_agent() -> Agent:
    effort = cast(
        ReasoningEffort, os.getenv("ORCHESTRATOR_REASONING_EFFORT", "minimal")
    )
    verbosity = cast(
        Literal["low", "medium", "high"],
        os.getenv("ORCHESTRATOR_VERBOSITY", "low"),
    )
    return Agent(
        name="OrchestratorAgent",
        instructions=SYSTEM_PROMPT,
        model=os.getenv("ORCHESTRATOR_MODEL", "gpt-5"),
        model_settings=ModelSettings(
            reasoning=Reasoning(effort=effort),
            verbosity=verbosity,
        ),
        tools=[
            elicit_user,
            run_parallel_research,
            query_data_warehouse,
            generate_research_image,
        ],
        output_type=FinalizeReportRequest,
    )
