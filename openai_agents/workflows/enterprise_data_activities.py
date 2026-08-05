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


@dataclass
class DataWarehouseRequest:
    query: str


@dataclass
class DataWarehouseResult:
    source: str
    summary: str
    units_consumed: int
    estimated_cost_usd: float


def _demo_retry_failures() -> int:
    return int(os.getenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "0") or "0")


def _first_attempt_delay_seconds() -> float:
    return float(os.getenv("DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS", "1") or "1")


@activity.defn(name="DataWarehouseLookup")
async def fetch_data_warehouse_context(
    request: DataWarehouseRequest,
) -> DataWarehouseResult:
    """Simulate a paid remote data warehouse lookup with a flaky connection."""
    attempt = activity.info().attempt
    failures = _demo_retry_failures()
    activity.logger.info(
        "Running data warehouse lookup "
        f"(attempt={attempt}/{DATA_WAREHOUSE_MAX_ATTEMPTS}, "
        f"configured_failures={failures}, query_units=11, "
        "estimated_cost_usd=0.19)"
    )

    if attempt == 1:
        await asyncio.sleep(_first_attempt_delay_seconds())
    else:
        await asyncio.sleep(0.6)

    if attempt <= failures:
        raise ApplicationError(
            f"Simulated remote connection reset on attempt {attempt} of "
            f"{failures} configured failures; Temporal retries the lookup "
            "without re-running completed clarifications, search planning, or "
            "finished search calls."
        )

    return DataWarehouseResult(
        source="Data warehouse",
        summary=(
            "Internal market signals: the most interesting neighborhoods combine "
            "distinctive local culture, active street life, accessible transit, "
            "and durable visitor demand rather than relying on a single attraction."
        ),
        units_consumed=11,
        estimated_cost_usd=0.19,
    )
