from __future__ import annotations

from dataclasses import replace

import httpx
import openai
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from openai_agents.workflows import (
    enterprise_data_activities,
    image_generation_activity,
)
from openai_agents.workflows.enterprise_data_activities import (
    DataWarehouseRequest,
    fetch_data_warehouse_context,
)
from openai_agents.workflows.image_generation_activity import generate_image


@pytest.mark.asyncio
async def test_warehouse_failure_injection_recovers_on_third_attempt(monkeypatch):
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "2")
    monkeypatch.setattr(enterprise_data_activities, "ATTEMPT_SECONDS", 0)
    env = ActivityEnvironment()
    request = DataWarehouseRequest(query="housing prices")

    env.info = replace(env.info, attempt=1)
    with pytest.raises(ApplicationError, match="Simulated remote connection reset"):
        await env.run(fetch_data_warehouse_context, request)

    env.info = replace(env.info, attempt=2)
    with pytest.raises(ApplicationError, match="Simulated remote connection reset"):
        await env.run(fetch_data_warehouse_context, request)

    env.info = replace(env.info, attempt=3)
    result = await env.run(fetch_data_warehouse_context, request)

    assert result.source == "Simulated data warehouse"
    assert "housing prices" in result.summary


def _image_api_raising(status_code: int):
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    error = openai.APIStatusError(
        f"status {status_code}",
        response=httpx.Response(status_code, request=request),
        body=None,
    )

    class _Images:
        async def generate(self, **kwargs):
            raise error

    class _Client:
        images = _Images()

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    return _Client


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_rejected_image_request_is_not_retried(monkeypatch, status_code):
    monkeypatch.delenv("DEMO_RESEARCH_IMAGE_PATH", raising=False)
    monkeypatch.setattr(
        image_generation_activity, "AsyncOpenAI", _image_api_raising(status_code)
    )

    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(generate_image, "a harbor")

    assert exc_info.value.non_retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503])
async def test_transient_image_failure_is_retried(monkeypatch, status_code):
    monkeypatch.delenv("DEMO_RESEARCH_IMAGE_PATH", raising=False)
    monkeypatch.setattr(
        image_generation_activity, "AsyncOpenAI", _image_api_raising(status_code)
    )

    # Propagates as-is, so Temporal's retry policy applies.
    with pytest.raises(openai.APIStatusError):
        await ActivityEnvironment().run(generate_image, "a harbor")
