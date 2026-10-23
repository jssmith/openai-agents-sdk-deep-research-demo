import asyncio
import os
from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError

# The lookup's retry policy (see orchestrator_agent.py) allows this many
# attempts. DEMO_DATA_WAREHOUSE_RETRY_FAILURES is validated in demo_config.py
# to stay below this, so an injected-failure run always recovers on its final
# attempt rather than exhausting the budget.
DATA_WAREHOUSE_MAX_ATTEMPTS = 10

# Each attempt takes this long, so a running attempt is visible in the
# Temporal UI before it fails or succeeds.
ATTEMPT_SECONDS = 1.0


@dataclass
class DataWarehouseRequest:
    query: str


@dataclass
class DataWarehouseResult:
    source: str
    summary: str


@activity.defn(name="DataWarehouseLookup")
async def fetch_data_warehouse_context(
    request: DataWarehouseRequest,
) -> DataWarehouseResult:
    """Simulate a remote data warehouse lookup over a flaky connection.

    The first DEMO_DATA_WAREHOUSE_RETRY_FAILURES attempts fail; Temporal
    retries the activity until it succeeds. There is no real dataset, and the
    result says so.
    """
    attempt = activity.info().attempt
    failures = int(os.getenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "0") or "0")
    activity.logger.info(
        f"Running data warehouse lookup (attempt={attempt}/"
        f"{DATA_WAREHOUSE_MAX_ATTEMPTS}, configured_failures={failures})"
    )

    await asyncio.sleep(ATTEMPT_SECONDS)

    if attempt <= failures:
        raise ApplicationError(
            f"Simulated remote connection reset on attempt {attempt} of "
            f"{failures} configured failures; Temporal retries the lookup "
            "without re-running completed clarifications or research."
        )

    return DataWarehouseResult(
        source="Simulated data warehouse",
        summary=(
            f"Simulated internal data for {request.query!r}: this demo "
            "environment has no proprietary dataset, so there are no internal "
            "records to add."
        ),
    )
