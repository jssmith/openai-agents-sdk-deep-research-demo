from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path

from agents import set_tracing_disabled
from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents import (
    ModelActivityParameters,
    OpenAIAgentsPlugin,
)
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from openai_agents.demo_config import load_demo_settings
from openai_agents.model_provider import AttemptAwareOpenAIProvider
from openai_agents.workflows.enterprise_data_activities import (
    DATA_WAREHOUSE_MAX_ATTEMPTS,
    fetch_data_warehouse_context,
)
from openai_agents.workflows.image_generation_activity import generate_image
from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)

# Load environment variables
load_dotenv()

TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "research-queue")

# Quiet noisy loggers. The default basicConfig level is INFO, which makes
# httpx log every outbound request — too chatty for a demo terminal.
logging.getLogger("openai").setLevel(logging.ERROR)
logging.getLogger("openai.agents").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)

# The OpenAI Agents SDK posts traces to /v1/traces/ingest by default.
# That endpoint isn't enabled for many API keys / orgs and returns 400,
# which clutters demo output without affecting workflow execution.
set_tracing_disabled(True)


async def main():
    logging.basicConfig(level=logging.INFO)
    demo_settings = load_demo_settings()
    pid_file = Path(os.getenv("DEMO_WORKER_PID_FILE", ".demo-worker.pid"))
    pid_file.write_text(str(os.getpid()))
    max_concurrent_activities = int(
        os.getenv("DEMO_WORKER_MAX_CONCURRENT_ACTIVITIES", "4")
    )

    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")
    config.setdefault("namespace", "default")

    print(
        "Connecting to Temporal at "
        f"{config.get('target_host')} in namespace {config.get('namespace')}",
        flush=True,
    )

    client = await Client.connect(
        **config,
        plugins=[
            OpenAIAgentsPlugin(
                model_provider=AttemptAwareOpenAIProvider(
                    fallback_model=demo_settings.model_fallback,
                    http_timeout_seconds=demo_settings.model_http_timeout_seconds,
                    local_model=demo_settings.model_local,
                    local_base_url=demo_settings.model_local_base_url,
                    local_api_key="ollama",
                    local_http_timeout_seconds=(
                        demo_settings.model_local_http_timeout_seconds
                    ),
                    local_attempt=demo_settings.model_local_attempt,
                ),
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(
                        seconds=demo_settings.model_start_to_close_seconds
                    ),
                    schedule_to_close_timeout=timedelta(
                        seconds=demo_settings.model_schedule_to_close_seconds
                    ),
                    retry_policy=RetryPolicy(
                        backoff_coefficient=1.0,
                        initial_interval=timedelta(
                            seconds=demo_settings.model_retry_delay_seconds
                        ),
                        maximum_interval=timedelta(
                            seconds=demo_settings.model_retry_delay_seconds
                        ),
                        maximum_attempts=demo_settings.model_max_attempts,
                    ),
                )
            ),
        ],
    )

    # Validation guarantees failures < DATA_WAREHOUSE_MAX_ATTEMPTS, so every
    # configured value recovers within the retry budget.
    failures = demo_settings.data_warehouse_retry_failures
    if failures == 0:
        warehouse_outcome = "succeeds on attempt 1 (clean)"
    else:
        warehouse_outcome = f"recovers on attempt {failures + 1}"

    # Print the *resolved* settings, never a mode name, so the banner can never
    # advertise behavior the worker isn't actually configured for.
    print(
        "Starting worker..."
        f" max_concurrent_activities={max_concurrent_activities}"
        f" warehouse_retry_failures={failures}"
        f" warehouse_outcome={warehouse_outcome!r}"
        f" warehouse_max_attempts={DATA_WAREHOUSE_MAX_ATTEMPTS}"
        f" model_timeout={demo_settings.model_start_to_close_seconds}s"
        f" model_schedule_to_close={demo_settings.model_schedule_to_close_seconds}s"
        f" model_retry_delay={demo_settings.model_retry_delay_seconds}s"
        f" model_http_timeout={demo_settings.model_http_timeout_seconds}s"
        f" model_max_attempts={demo_settings.model_max_attempts}"
        f" fallback_model={demo_settings.model_fallback}"
        f" local_model={demo_settings.model_local or '(disabled)'}"
        f" local_attempt={demo_settings.model_local_attempt}"
        f" total_budget={demo_settings.total_budget_seconds}s",
        flush=True,
    )
    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        max_concurrent_activities=max_concurrent_activities,
        workflows=[
            InteractiveResearchWorkflow,
        ],
        activities=[
            generate_image,
            fetch_data_warehouse_context,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
