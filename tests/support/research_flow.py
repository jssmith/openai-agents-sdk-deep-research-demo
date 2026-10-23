"""A scripted run of the research agents, for workflow tests.

ScriptedResearch plays both agents the way a real model would: the
orchestrator asks two questions, dispatches research, calls the warehouse and
image tools in one turn, then writes the report; each research worker returns a
summary for its subquery. It records every call it serves, so tests can check
that completed work is not repeated across retries or worker restarts.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

from agents import ModelResponse, Usage
from openai.types.responses import ResponseFunctionToolCall
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents import ModelActivityParameters
from temporalio.contrib.openai_agents.testing import AgentEnvironment, ResponseBuilders
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from openai_agents.workflows.enterprise_data_activities import (
    fetch_data_warehouse_context,
)
from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import (
    ElicitationResponseInput,
    UserQueryInput,
)
from openai_agents.workflows.research_agents.research_worker_agent import (
    INSTRUCTIONS as WORKER_INSTRUCTIONS,
)

TASK_QUEUE = "research-flow-test"
FAKE_IMAGE_PATH = "temp_images/fake.jpeg"
SUBQUERIES = ["market size", "key players", "regulation", "outlook"]
QUESTIONS = ["Which time horizon?", "Which region?"]
ANSWERS = ["Last 12 months", "Europe"]


def model_params(max_attempts: int = 3) -> ModelActivityParameters:
    return ModelActivityParameters(
        start_to_close_timeout=timedelta(seconds=60),
        schedule_to_close_timeout=timedelta(seconds=240),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=1.0,
            maximum_interval=timedelta(seconds=1),
            maximum_attempts=max_attempts,
        ),
    )


def tool_calls(*calls: tuple[str, dict]) -> ModelResponse:
    """A model response making one or more function tool calls."""
    return ModelResponse(
        output=[
            ResponseFunctionToolCall(
                arguments=json.dumps(args),
                call_id=f"call_{uuid.uuid4().hex[:8]}",
                name=name,
                type="function_call",
                id="id",
                status="completed",
            )
            for name, args in calls
        ],
        usage=Usage(),
        response_id=None,
    )


def final_report(summary: str = "Model-written summary.") -> ModelResponse:
    return ResponseBuilders.output_message(
        json.dumps(
            {
                "short_summary": summary,
                "markdown_report": "# Model Report\n\n" + ("Detailed finding. " * 40),
                "follow_up_questions": ["What next?", "What else?"],
            }
        )
    )


def _item_field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _tool_outputs(input: Any) -> list[Any]:
    if isinstance(input, str):
        return []
    return [i for i in input if _item_field(i, "type") == "function_call_output"]


class ScriptedResearch:
    """Response function for ChaosModelProvider playing both agents.

    failing_queries: subqueries whose research worker model call always raises.
    report_immediately: the orchestrator skips every tool and returns a report.
    """

    def __init__(
        self,
        *,
        failing_queries: frozenset[str] = frozenset(),
        report_immediately: bool = False,
        summary: str = "Model-written summary.",
    ) -> None:
        self.failing_queries = failing_queries
        self.report_immediately = report_immediately
        self.summary = summary
        self.orchestrator_steps: list[int] = []
        self.worker_queries: list[str] = []
        self.research_tool_output: str | None = None

    def __call__(self, system_instructions: Any, input: Any) -> ModelResponse:
        if system_instructions == WORKER_INSTRUCTIONS:
            return self._worker(input)
        return self._orchestrator(input)

    def _worker(self, input: Any) -> ModelResponse:
        text = input if isinstance(input, str) else _item_field(input[0], "content")
        query = text.removeprefix("Subquery: ")
        self.worker_queries.append(query)
        if query in self.failing_queries:
            raise RuntimeError(f"scripted: research model unavailable for {query!r}")
        return ResponseBuilders.output_message(
            json.dumps({"query": query, "summary": f"Findings on {query}."})
        )

    def _orchestrator(self, input: Any) -> ModelResponse:
        outputs = _tool_outputs(input)
        step = len(outputs)
        self.orchestrator_steps.append(step)
        if self.report_immediately:
            return final_report(self.summary)
        if step < 2:
            return tool_calls(("elicit_user", {"request": {"message": QUESTIONS[step]}}))
        if step == 2:
            return tool_calls(
                ("run_parallel_research", {"request": {"subqueries": SUBQUERIES}})
            )
        if step == 3:
            self.research_tool_output = _item_field(outputs[2], "output")
            return tool_calls(
                ("query_data_warehouse", {"query": "internal signals"}),
                ("generate_research_image", {"image_prompt": "A calm harbor."}),
            )
        return final_report(self.summary)


@activity.defn(name="generate_image")
async def fake_generate_image(prompt: str) -> str:
    return FAKE_IMAGE_PATH


@activity.defn(name="generate_image")
async def failing_generate_image(prompt: str) -> str:
    raise ApplicationError("scripted: image service down", non_retryable=True)


async def answer_questions(handle: WorkflowHandle, count: int = 2) -> None:
    """Answer the workflow's pending clarifications, in order, as the UI does.

    Returns early if the run closes first (e.g. it fails before asking).
    """
    answered = 0
    while answered < count:
        if (await handle.describe()).status != WorkflowExecutionStatus.RUNNING:
            return
        status = await handle.query(InteractiveResearchWorkflow.get_status)
        if status.pending_elicitation is not None:
            await handle.execute_update(
                InteractiveResearchWorkflow.submit_elicitation_response,
                ElicitationResponseInput(
                    elicitation_id=status.pending_elicitation.id,
                    response=ANSWERS[answered],
                ),
            )
            answered += 1
        else:
            await asyncio.sleep(0.05)


async def run_research(
    provider: Any,
    *,
    activities: tuple = (fake_generate_image, fetch_data_warehouse_context),
    params: ModelActivityParameters | None = None,
    query: str = "impact of AI on logistics",
):
    """Run the workflow to completion, answering clarifications as they come."""
    async with await WorkflowEnvironment.start_time_skipping() as wf_env:
        async with AgentEnvironment(
            model_provider=provider, model_params=params or model_params()
        ) as agent_env:
            client: Client = agent_env.applied_on_client(wf_env.client)
            async with Worker(
                client,
                task_queue=TASK_QUEUE,
                workflows=[InteractiveResearchWorkflow],
                activities=list(activities),
            ):
                handle = await client.start_workflow(
                    InteractiveResearchWorkflow.run,
                    UserQueryInput(query=query),
                    id=f"research-flow-{uuid.uuid4().hex[:8]}",
                    task_queue=TASK_QUEUE,
                )
                # Answer before awaiting the result: while the test awaits a
                # result, the time-skipping server skips past human waits.
                await answer_questions(handle)
                return await handle.result()
