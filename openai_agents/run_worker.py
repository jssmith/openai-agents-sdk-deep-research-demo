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
    max_concurrent_activities = int(
        os.getenv("DEMO_WORKER_MAX_CONCURRENT_ACTIVITIES", "4")
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
                    # 30s is enough for typical gpt-5 reasoning turns and
                    # surfaces hung LLM calls quickly in the demo.
                    start_to_close_timeout=timedelta(seconds=30),
                    # schedule_to_close caps total time across retries; sized
                    # for a few retries of a hung call before giving up.
                    schedule_to_close_timeout=timedelta(seconds=180),
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
            fetch_data_warehouse_context,
            process_clarification,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
