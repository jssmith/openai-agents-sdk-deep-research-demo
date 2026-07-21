from __future__ import annotations

from dataclasses import replace

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from openai_agents.workflows.enterprise_data_activities import (
    DataWarehouseRequest,
    fetch_data_warehouse_context,
)


@pytest.mark.asyncio
async def test_warehouse_failure_injection_recovers_on_third_attempt(monkeypatch):
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "2")
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS", "0")
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

    assert result.source == "Data warehouse"
    assert result.units_consumed == 11
