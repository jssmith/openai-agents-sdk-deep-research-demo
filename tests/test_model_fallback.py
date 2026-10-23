"""Adversarial validation of model-failure handling in the research workflow.

Each test drives the real InteractiveResearchWorkflow through a time-skipping
Temporal environment with a chaos model provider, so retries advance
deterministically in milliseconds. The contract under test: a later retry
attempt (served by a fallback model) can rescue the run, and when every attempt
fails the workflow fails visibly rather than reporting success.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents import ModelActivityParameters
from temporalio.contrib.openai_agents.testing import AgentEnvironment, ResponseBuilders
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import UserQueryInput
from support.chaos_model import ChaosModelProvider

TASK_QUEUE = "bounded-synthesis-test"


def _model_params(max_attempts: int = 3, retry_seconds: int = 1) -> ModelActivityParameters:
    return ModelActivityParameters(
        start_to_close_timeout=timedelta(seconds=60),
        schedule_to_close_timeout=timedelta(seconds=240),
        retry_policy=RetryPolicy(
            initial_interval=timedelta(seconds=retry_seconds),
            backoff_coefficient=1.0,
            maximum_interval=timedelta(seconds=retry_seconds),
            maximum_attempts=max_attempts,
        ),
    )


def _final_report_json(summary: str = "Model-written summary.") -> str:
    return json.dumps(
        {
            "short_summary": summary,
            "markdown_report": "# Model Report\n\n" + ("Detailed finding. " * 40),
            "follow_up_questions": ["What next?", "What else?"],
            "warehouse_summary": "Model warehouse context.",
            "search_summaries": [
                {"query": "a", "summary": "A"},
                {"query": "b", "summary": "B"},
                {"query": "c", "summary": "C"},
            ],
        }
    )


async def _run(
    provider: ChaosModelProvider,
    *,
    model_params: ModelActivityParameters | None = None,
    activities=(),
    query: str = "impact of AI on logistics",
    steer=None,
):
    """Start the workflow, kick off research, and return its result.

    ``steer``, if given, is awaited with the workflow handle after research
    starts, to interact with the run (e.g. signal it) before awaiting the result.
    """
    async with await WorkflowEnvironment.start_time_skipping() as wf_env:
        async with AgentEnvironment(
            model_provider=provider,
            model_params=model_params or _model_params(),
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
                    id=f"bounded-{uuid.uuid4().hex[:8]}",
                    task_queue=TASK_QUEUE,
                )
                await handle.execute_update(
                    InteractiveResearchWorkflow.start_research,
                    UserQueryInput(query=query),
                )
                if steer is not None:
                    await steer(handle)
                return await handle.result()


def _assert_valid_report(result) -> None:
    assert result.short_summary.strip()
    assert len(result.markdown_report) >= 300
    assert len(result.follow_up_questions) >= 2


@pytest.mark.asyncio
async def test_all_model_attempts_fail_fails_the_workflow():
    """Total model outage must fail the run, not fabricate a report."""
    provider = ChaosModelProvider(fault_all=True, fault_kind="timeout")

    with pytest.raises(WorkflowFailureError) as exc_info:
        await _run(provider)

    assert "Research could not be completed" in str(exc_info.value.cause)


@pytest.mark.asyncio
async def test_local_attempt_succeeds_after_cloud_timeouts():
    """Cloud attempts 1-2 time out; attempt 3 (local) returns a real report."""
    provider = ChaosModelProvider(
        response_fn=lambda si, tools: ResponseBuilders.output_message(
            _final_report_json("Local-model summary.")
        ),
        fault_attempts={1, 2},
        fault_kind="timeout",
    )

    result = await _run(provider)

    _assert_valid_report(result)
    assert result.short_summary == "Local-model summary."


@pytest.mark.asyncio
async def test_happy_path_returns_model_report():
    """No faults: the model's report flows straight through (regression)."""
    provider = ChaosModelProvider(
        response_fn=lambda si, tools: ResponseBuilders.output_message(
            _final_report_json("Cloud-model summary.")
        ),
    )

    result = await _run(provider)

    _assert_valid_report(result)
    assert result.short_summary == "Cloud-model summary."


_FIRST_QUESTION = {
    "request": {
        "message": "Which time horizon should we analyze?",
        "progress_plan": {
            "planning": {"title": "Define scope", "detail": "Clarify scope."},
            "collecting": {"title": "Gather evidence", "detail": "Collect."},
            "writing": {"title": "Write brief", "detail": "Synthesize."},
        },
    }
}


@pytest.mark.asyncio
async def test_end_by_user_returns_ended_result_not_failure():
    """Ending the run mid-flight must report "ended", not a failure.

    The user ends the workflow while a clarification is pending; the agent's
    next model turn then fails on every attempt. That failure is a consequence
    of the user ending the run, so the result is "ended".
    """

    def respond(system_instructions, tools):
        if not state["asked"]:
            state["asked"] = True
            return ResponseBuilders.tool_call(
                json.dumps(_FIRST_QUESTION), "elicit_user"
            )
        raise TimeoutError("chaos: model unavailable after the user ended the run")

    state = {"asked": False}

    async def end_while_pending(handle):
        for _ in range(200):
            status = await handle.query(InteractiveResearchWorkflow.get_status)
            if status.pending_elicitation is not None:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("workflow did not publish a clarification")
        await handle.signal(InteractiveResearchWorkflow.end_workflow_signal)

    result = await _run(
        ChaosModelProvider(response_fn=respond), steer=end_while_pending
    )

    assert result.short_summary == "Research ended by user"
