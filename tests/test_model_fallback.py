"""Workflow tests for the research flow, its failure handling and its retries.

Each test drives the real InteractiveResearchWorkflow through a time-skipping
Temporal environment with a scripted model (see support/research_flow.py), so
retries advance deterministically in milliseconds. The contracts under test:

- a full run records every required outcome and returns the model's report;
- a later retry attempt (served by a fallback model) can rescue the run;
- partial research and an unavailable image still complete, disclosed;
- the run fails visibly when every model attempt fails, or when the agent
  finishes without doing the required work;
- a retried activity does not repeat completed work.
"""

from __future__ import annotations

import pytest
from temporalio.client import WorkflowFailureError

from openai_agents.workflows import enterprise_data_activities
from openai_agents.workflows.enterprise_data_activities import (
    fetch_data_warehouse_context,
)
from support.chaos_model import ChaosModelProvider
from support.research_flow import (
    FAKE_IMAGE_PATH,
    SUBQUERIES,
    ScriptedResearch,
    failing_generate_image,
    run_research,
)


@pytest.fixture(autouse=True)
def fast_warehouse(monkeypatch):
    monkeypatch.setattr(enterprise_data_activities, "ATTEMPT_SECONDS", 0)
    monkeypatch.delenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", raising=False)


def _assert_valid_report(result) -> None:
    assert result.short_summary.strip()
    assert len(result.markdown_report) >= 300
    assert len(result.follow_up_questions) >= 2


@pytest.mark.asyncio
async def test_full_flow_returns_model_report_with_activity_image():
    script = ScriptedResearch(summary="Cloud-model summary.")

    result = await run_research(ChaosModelProvider(response_fn=script))

    _assert_valid_report(result)
    assert result.short_summary == "Cloud-model summary."
    # The image path comes from the image activity, never from the model.
    assert result.image_file_path == FAKE_IMAGE_PATH
    # Every orchestrator step ran exactly once, and each subquery was researched once.
    assert script.orchestrator_steps == [0, 1, 2, 3, 5]
    assert sorted(script.worker_queries) == sorted(SUBQUERIES)


@pytest.mark.asyncio
async def test_local_attempt_succeeds_after_cloud_timeouts():
    """Cloud attempts 1-2 time out on every call; attempt 3 (local) completes."""
    script = ScriptedResearch(summary="Local-model summary.")
    provider = ChaosModelProvider(
        response_fn=script, fault_attempts={1, 2}, fault_kind="timeout"
    )

    result = await run_research(provider)

    _assert_valid_report(result)
    assert result.short_summary == "Local-model summary."


@pytest.mark.asyncio
async def test_all_model_attempts_fail_fails_the_workflow():
    """Total model outage must fail the run, not fabricate a report."""
    provider = ChaosModelProvider(fault_all=True, fault_kind="timeout")

    with pytest.raises(WorkflowFailureError) as exc_info:
        await run_research(provider)

    assert "Research could not be completed" in str(exc_info.value.cause)


@pytest.mark.asyncio
async def test_report_without_the_work_fails_the_workflow():
    """A model that skips straight to a report cannot complete the run."""
    script = ScriptedResearch(report_immediately=True)

    with pytest.raises(WorkflowFailureError) as exc_info:
        await run_research(ChaosModelProvider(response_fn=script))

    message = str(exc_info.value.cause)
    assert "Agent finished without" in message
    assert "research findings" in message


@pytest.mark.asyncio
async def test_unavailable_image_completes_with_placeholder():
    script = ScriptedResearch()

    result = await run_research(
        ChaosModelProvider(response_fn=script),
        activities=(failing_generate_image, fetch_data_warehouse_context),
    )

    _assert_valid_report(result)
    # No path: the UI shows its missing-image placeholder.
    assert result.image_file_path is None


@pytest.mark.asyncio
async def test_partial_research_completes_and_reports_failed_queries():
    failing = SUBQUERIES[0]
    script = ScriptedResearch(failing_queries=frozenset({failing}))

    result = await run_research(ChaosModelProvider(response_fn=script))

    _assert_valid_report(result)
    # The agent was told which angle could not be researched.
    assert script.research_tool_output is not None
    assert failing in script.research_tool_output
    assert "failed_queries" in script.research_tool_output


@pytest.mark.asyncio
async def test_warehouse_retries_without_repeating_research(monkeypatch):
    """Injected warehouse failures recover via activity retries alone."""
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "2")
    script = ScriptedResearch()

    result = await run_research(ChaosModelProvider(response_fn=script))

    _assert_valid_report(result)
    # Research and orchestrator steps ran once each despite the warehouse retries.
    assert sorted(script.worker_queries) == sorted(SUBQUERIES)
    assert script.orchestrator_steps == [0, 1, 2, 3, 5]
