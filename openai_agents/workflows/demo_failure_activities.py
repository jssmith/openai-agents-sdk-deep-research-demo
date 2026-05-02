import asyncio
import os
import signal
from dataclasses import dataclass

from temporalio import activity


@dataclass
class SearchBranchRequest:
    query_count: int
    search_index: int
    search_query: str


def _search_branch_failure_attempts() -> int:
    return int(
        os.getenv(
            "DEMO_SEARCH_BRANCH_PROCESS_FAILURES",
            os.getenv("DEMO_SEARCH_AGENT_PROCESS_CRASHES", "0"),
        )
        or "0"
    )


def _search_branch_failure_after_seconds() -> float:
    return float(
        os.getenv(
            "DEMO_SEARCH_BRANCH_FAILURE_AFTER_SECONDS",
            os.getenv("DEMO_SEARCH_AGENT_CRASH_AFTER_SECONDS", "4"),
        )
        or "4"
    )


@activity.defn(name="PrepareWebSearchBranch")
async def prepare_web_search_branch(
    request: SearchBranchRequest,
) -> str:
    """Demo-only setup activity for one parallel search branch."""
    attempt = activity.info().attempt
    activity.logger.info(
        "Preparing web search branch "
        f"(attempt={attempt}, search={request.search_index + 1}/{request.query_count}, "
        f"query={request.search_query!r})"
    )

    if attempt <= _search_branch_failure_attempts():
        delay = _search_branch_failure_after_seconds()
        activity.logger.error(
            "Simulating worker process failure "
            f"in {delay:.1f}s while preparing search branch {request.search_index + 1}"
        )
        await asyncio.sleep(delay)
        os.kill(os.getpid(), signal.SIGKILL)

    await asyncio.sleep(0.3)
    return "Search branch prepared; continuing this web search."
