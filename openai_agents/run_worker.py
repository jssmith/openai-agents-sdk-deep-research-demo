from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from datetime import timedelta

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents import ModelActivityParameters, OpenAIAgentsPlugin
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from openai_agents.workflows.enterprise_data_activities import (
    fetch_data_warehouse_context,
)
from openai_agents.workflows.demo_failure_activities import (
    prepare_web_search_branch,
)
from openai_agents.workflows.image_generation_activity import generate_image
from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
    process_clarification,
)
from openai_agents.workflows.pdf_generation_activity import generate_pdf

# Load environment variables
load_dotenv()

# Configure logging
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("openai.agents").setLevel(logging.CRITICAL)


async def main():
    logging.basicConfig(level=logging.INFO)
    pid_file = Path(os.getenv("DEMO_WORKER_PID_FILE", ".demo-worker.pid"))
    pid_file.write_text(str(os.getpid()))
    # Default to 1 to keep the crash-recovery demo path (DEMO_SEARCH_BRANCH_*) responsive:
    # with low concurrency, fewer in-flight activities have to age out on timeout when the
    # worker SIGKILLs itself. The clean-demo path opts into higher concurrency via
    # scripts/start-clean-worker (which exports DEMO_WORKER_MAX_CONCURRENT_ACTIVITIES=4).
    max_concurrent_activities = int(
        os.getenv("DEMO_WORKER_MAX_CONCURRENT_ACTIVITIES", "1")
    )

    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")
    config.setdefault("namespace", "default")

    print(
        f"Connecting to Temporal at {config.get('target_host')} in namespace {config.get('namespace')}"
    )

    client = await Client.connect(
        **config,
        plugins=[
            OpenAIAgentsPlugin(
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(seconds=200),
                    schedule_to_close_timeout=timedelta(seconds=500),
                    retry_policy=RetryPolicy(
                        backoff_coefficient=2.0,
                        initial_interval=timedelta(seconds=1),
                        maximum_interval=timedelta(seconds=5),
                    ),
                )
            ),
        ],
    )

    print(
        "Starting worker..."
        f" max_concurrent_activities={max_concurrent_activities}"
    )
    worker = Worker(
        client,
        task_queue="research-queue",
        max_concurrent_activities=max_concurrent_activities,
        workflows=[
            InteractiveResearchWorkflow,
        ],
        activities=[
            generate_pdf,
            generate_image,
            prepare_web_search_branch,
            fetch_data_warehouse_context,
            process_clarification,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
