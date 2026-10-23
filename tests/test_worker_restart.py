"""The demo's durability beat: replace the worker while question two is pending.

The first worker serves the run up to the second clarification and is then shut
down. A new worker, with an empty cache, rebuilds the workflow from history,
receives the second answer, and completes the same conversation without
repeating any model call that had already completed.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid

import pytest
from temporalio.client import Client
from temporalio.contrib.openai_agents.testing import AgentEnvironment
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from openai_agents.workflows import enterprise_data_activities
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
from support.chaos_model import ChaosModelProvider
from support.research_flow import (
    ANSWERS,
    FAKE_IMAGE_PATH,
    QUESTIONS,
    SUBQUERIES,
    TASK_QUEUE,
    ScriptedResearch,
    fake_generate_image,
    model_params,
)


async def _wait_for_question(handle, index: int):
    for _ in range(200):
        status = await handle.query(InteractiveResearchWorkflow.get_status)
        if status.pending_elicitation is not None and len(
            status.completed_elicitations
        ) == index:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow did not publish question {index + 1}")


async def _answer(handle, status, answer: str) -> None:
    await handle.execute_update(
        InteractiveResearchWorkflow.submit_elicitation_response,
        ElicitationResponseInput(
            elicitation_id=status.pending_elicitation.id, response=answer
        ),
    )


@pytest.mark.asyncio
async def test_worker_replaced_at_second_question_continues_conversation(
    monkeypatch,
):
    monkeypatch.setattr(enterprise_data_activities, "ATTEMPT_SECONDS", 0)
    monkeypatch.delenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", raising=False)
    script = ScriptedResearch()

    # A real dev server, not the time-skipping test server, whose handling of an
    # update on a cache-less worker differs from the real server's. Uses the
    # installed temporal CLI if there is one; otherwise the SDK downloads it.
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as wf_env:
        async with AgentEnvironment(
            model_provider=ChaosModelProvider(response_fn=script),
            model_params=model_params(),
        ) as agent_env:
            client: Client = agent_env.applied_on_client(wf_env.client)

            def new_worker() -> Worker:
                # No workflow cache: tasks never stick to the departed worker,
                # and every workflow task is rebuilt from history.
                return Worker(
                    client,
                    task_queue=TASK_QUEUE,
                    workflows=[InteractiveResearchWorkflow],
                    activities=[fake_generate_image, fetch_data_warehouse_context],
                    max_cached_workflows=0,
                )

            async with new_worker():
                handle = await client.start_workflow(
                    InteractiveResearchWorkflow.run,
                    UserQueryInput(query="housing prices"),
                    id=f"worker-restart-{uuid.uuid4().hex[:8]}",
                    task_queue=TASK_QUEUE,
                )
                await _answer(handle, await _wait_for_question(handle, 0), ANSWERS[0])
                await _wait_for_question(handle, 1)
            # The first worker is gone; the run waits durably at question two.
            assert script.orchestrator_steps == [0, 1]

            async with new_worker():
                status = await _wait_for_question(handle, 1)
                assert status.pending_elicitation.message == QUESTIONS[1]
                assert [e.response for e in status.completed_elicitations] == [
                    ANSWERS[0]
                ]
                await _answer(handle, status, ANSWERS[1])
                result = await handle.result()

    assert result.image_file_path == FAKE_IMAGE_PATH
    # Questions one and two were not re-asked after the restart.
    assert script.orchestrator_steps == [0, 1, 2, 3, 5]
    assert sorted(script.worker_queries) == sorted(SUBQUERIES)
