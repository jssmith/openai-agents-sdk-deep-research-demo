from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from temporalio.client import Client
from temporalio.contrib.openai_agents import OpenAIPayloadConverter
from temporalio.contrib.openai_agents.testing import (
    AgentEnvironment,
    ResponseBuilders,
    TestModel,
)
from temporalio.converter import DataConverter
from temporalio.worker import Worker

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import (
    ElicitationResponseInput,
    UserQueryInput,
)


@pytest.mark.skipif(
    not os.getenv("TEMPORAL_TEST_ADDRESS"),
    reason="set TEMPORAL_TEST_ADDRESS to run against an existing Temporal server",
)
@pytest.mark.asyncio
async def test_worker_restart_preserves_pending_clarification():
    first_question = {
        "request": {
            "message": "Which time horizon should we analyze?",
            "progress_plan": {
                "planning": {
                    "title": "Define housing scope",
                    "detail": "Clarify scope.",
                },
                "collecting": {
                    "title": "Gather market evidence",
                    "detail": "Collect evidence.",
                },
                "writing": {
                    "title": "Write trend brief",
                    "detail": "Synthesize findings.",
                },
            },
        }
    }
    model = TestModel.returning_responses(
        [
            ResponseBuilders.tool_call(json.dumps(first_question), "elicit_user"),
            ResponseBuilders.tool_call(
                json.dumps(
                    {
                        "request": {
                            "message": "Which housing types should we compare?"
                        }
                    }
                ),
                "elicit_user",
            ),
            ResponseBuilders.output_message(
                json.dumps(
                    {
                        "short_summary": "A test summary.",
                        "markdown_report": "# Housing Trends\n\n" + ("Evidence. " * 60),
                        "follow_up_questions": ["One?", "Two?"],
                        "image_path": "temp_images/test.jpeg",
                        "warehouse_summary": "Test warehouse context.",
                        "search_summaries": [
                            {"query": "a", "summary": "A"},
                            {"query": "b", "summary": "B"},
                            {"query": "c", "summary": "C"},
                        ],
                    }
                )
            ),
        ]
    )

    async with AgentEnvironment(model=model) as agents_env:
        client = await Client.connect(
            os.environ["TEMPORAL_TEST_ADDRESS"],
            data_converter=DataConverter(
                payload_converter_class=OpenAIPayloadConverter
            ),
        )
        task_queue = "worker-restart-test"

        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[InteractiveResearchWorkflow],
            plugins=[agents_env.openai_agents_plugin],
        ):
            handle = await client.start_workflow(
                InteractiveResearchWorkflow.run,
                id=f"worker-restart-clarification-test-{uuid.uuid4().hex[:8]}",
                task_queue=task_queue,
            )
            await handle.execute_update(
                InteractiveResearchWorkflow.start_research,
                UserQueryInput(query="housing prices"),
            )

            for _ in range(100):
                status = await handle.query(InteractiveResearchWorkflow.get_status)
                if status.pending_elicitation is not None:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("workflow did not publish a clarification")

        async with Worker(
            client,
            task_queue=task_queue,
            workflows=[InteractiveResearchWorkflow],
            plugins=[agents_env.openai_agents_plugin],
        ):
            status = await handle.query(InteractiveResearchWorkflow.get_status)
            assert status.pending_elicitation is not None
            assert (
                status.pending_elicitation.message
                == first_question["request"]["message"]
            )
            await handle.execute_update(
                InteractiveResearchWorkflow.submit_elicitation_response,
                ElicitationResponseInput(
                    elicitation_id=status.pending_elicitation.id,
                    response="Last 12 months",
                ),
            )

            for _ in range(100):
                status = await handle.query(InteractiveResearchWorkflow.get_status)
                if status.pending_elicitation is not None:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("workflow did not publish its second question")

            await handle.execute_update(
                InteractiveResearchWorkflow.submit_elicitation_response,
                ElicitationResponseInput(
                    elicitation_id=status.pending_elicitation.id,
                    response="Single-family homes",
                ),
            )
            result = await handle.result()
            assert result.short_summary == "A test summary."
