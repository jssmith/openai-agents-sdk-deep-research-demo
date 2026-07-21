"""Regression test for the agents payload-converter poison-pill.

The OpenAI Responses API sometimes returns a ``web_search_call`` output item
without the ``action`` field. openai-python builds it anyway (its response
parser is lenient), but ``ResponseFunctionWebSearch.action`` is required in
every openai 2.x. Before temporalio 1.28.0 (PR #1563), the agents payload
converter serialized such a ``ModelResponse`` into workflow history but then
failed to deserialize it (strict ``validate_json``), permanently wedging the
workflow with WORKFLOW_TASK_FAILED "Failed decoding arguments".

The fixture is the exact ``invoke_model_activity`` result pulled from the
history of the wedged run ``interactive-research-8dde98a6``. This test decodes
it through the plugin's own converter: red on temporalio <1.28.0, green on
>=1.28.0.
"""

from __future__ import annotations

from pathlib import Path

from agents.items import ModelResponse
from temporalio.api.common.v1 import Payload
from temporalio.contrib.openai_agents import OpenAIPayloadConverter

FIXTURE = Path(__file__).parent / "fixtures" / "model_response_web_search_no_action.json"


def test_converter_decodes_web_search_call_without_action():
    raw = FIXTURE.read_bytes()
    payload = Payload(metadata={"encoding": b"json/plain"}, data=raw)

    # This is exactly what the workflow side does when resolving the model
    # activity result. On temporalio <1.28.0 it raises a 69-error
    # pydantic ValidationError; on >=1.28.0 the lenient fallback tolerates it.
    response = OpenAIPayloadConverter().from_payload(payload, ModelResponse)

    web_search_calls = [
        item for item in response.output if getattr(item, "type", None) == "web_search_call"
    ]
    # The fixture has two web_search_calls: one with no action, one with a full
    # ActionSearch. Both must survive the decode.
    assert len(web_search_calls) == 2
    actions = [getattr(item, "action", "<missing>") for item in web_search_calls]
    assert None in actions, "the action-less web_search_call should decode with action=None"
    assert any(a not in (None, "<missing>") for a in actions), "the valid web_search_call should keep its action"
