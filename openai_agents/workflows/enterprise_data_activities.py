import asyncio
import os
from dataclasses import dataclass

from temporalio import activity
from temporalio.exceptions import ApplicationError


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
    activity.logger.info(
        "Running data warehouse lookup "
        f"(attempt={attempt}, query_units=11, estimated_cost_usd=0.19)"
    )

    if attempt == 1:
        await asyncio.sleep(_first_attempt_delay_seconds())
    else:
        await asyncio.sleep(0.6)

    if attempt <= _demo_retry_failures():
        raise ApplicationError(
            "Simulated remote connection reset while reading the data warehouse "
            "response; Temporal retries the lookup without re-running completed "
            "clarifications, search planning, or finished search calls."
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
