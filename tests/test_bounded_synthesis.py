"""Adversarial, end-to-end validation of the never-fail / bounded-time contract.

Each test drives the real InteractiveResearchWorkflow through a time-skipping
Temporal environment with a chaos model provider, so retries and timers advance
deterministically in milliseconds. The guarantee under test: the workflow ALWAYS
returns a valid report and never fails, in bounded time, no matter how the model
layers behave.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents import ModelActivityParameters
from temporalio.contrib.openai_agents.testing import AgentEnvironment, ResponseBuilders
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from openai_agents.workflows.enterprise_data_activities import (
    fetch_data_warehouse_context,
)
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
            "image_path": "temp_images/test.jpeg",
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
):
    """Start the workflow, kick off research, and return its result."""
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
                return await handle.result()


def _assert_valid_report(result) -> None:
    assert result.short_summary.strip()
    assert len(result.markdown_report) >= 300
    assert len(result.follow_up_questions) >= 2


def _is_floor_report(result) -> bool:
    return "assembled automatically" in result.markdown_report


@pytest.mark.asyncio
async def test_all_model_attempts_fail_returns_deterministic_floor():
    """Core never-fail guarantee: total model outage still yields a valid report."""
    provider = ChaosModelProvider(fault_all=True, fault_kind="timeout")

    result = await _run(provider)

    _assert_valid_report(result)
    assert _is_floor_report(result)
    # No model ever succeeded, so this must NOT be model-written content.
    assert result.short_summary != "Model-written summary."


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
    assert not _is_floor_report(result)


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
    assert not _is_floor_report(result)


@pytest.mark.asyncio
async def test_floor_uses_partial_data_captured_before_failure(monkeypatch):
    """Warehouse lookup succeeds, then synthesis fails on all attempts.

    The deterministic floor must incorporate the real warehouse context that was
    captured into workflow state before the failure.
    """
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "0")
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS", "0")

    state = {"orch_turns": 0}

    def respond(system_instructions, tools):
        state["orch_turns"] += 1
        if state["orch_turns"] == 1:
            # First turn: call the data warehouse (populates workflow state).
            return ResponseBuilders.tool_call(
                json.dumps({"query": "quarterly revenue"}), "query_data_warehouse"
            )
        # Every subsequent (synthesis) turn fails.
        raise TimeoutError("chaos: synthesis failed after warehouse lookup")

    provider = ChaosModelProvider(response_fn=respond)

    result = await _run(provider, activities=[fetch_data_warehouse_context])

    _assert_valid_report(result)
    assert _is_floor_report(result)
    # The floor cited the real internal data captured before the failure.
    assert "Proprietary data warehouse context" in result.markdown_report


@pytest.mark.asyncio
async def test_budget_exceeded_returns_floor_without_exhausting_retries(monkeypatch):
    """A tight wall-clock budget aborts slow model work and returns the floor.

    The model faults on every attempt with a long retry backoff. The budget
    guard must fire first, cancel the orchestrator, and return the floor BEFORE
    the retries are exhausted (proving the race + cancellation, not just the
    exception path).
    """
    # Sandbox re-executes the workflow module at run time, so setting the env
    # before the worker starts propagates these into the workflow globals.
    monkeypatch.setenv("DEMO_TOTAL_BUDGET_SECONDS", "20")
    monkeypatch.setenv("DEMO_BUDGET_RECHECK_SECONDS", "5")

    provider = ChaosModelProvider(fault_all=True, fault_kind="timeout")

    # 3 attempts x 30s backoff => ~60s to exhaust; budget of 20s must win first.
    result = await _run(
        provider, model_params=_model_params(max_attempts=3, retry_seconds=30)
    )

    _assert_valid_report(result)
    assert _is_floor_report(result)
    # Guard cancelled the orchestrator before all retries ran.
    assert provider.response_call_count[0] < 3
