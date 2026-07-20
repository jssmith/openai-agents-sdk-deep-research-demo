"""Configuration for predictable live-demo timing and recovery behavior."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoSettings:
    profile: str
    model_start_to_close_seconds: int
    model_schedule_to_close_seconds: int
    model_retry_delay_seconds: int
    model_max_attempts: int
    model_http_timeout_seconds: int
    model_fallback: str
    image_max_attempts: int
    # Local (Ollama) synthesis fallback used on later model-activity attempts.
    model_local: str
    model_local_base_url: str
    model_local_http_timeout_seconds: int
    model_local_attempt: int
    # Hard wall-clock budget for non-interactive work (excludes human wait).
    total_budget_seconds: int
    budget_recheck_seconds: int


def load_demo_settings() -> DemoSettings:
    profile = os.getenv("DEMO_PROFILE", "clean").strip().lower()
    if profile not in {"clean", "recovery"}:
        raise ValueError(
            f"DEMO_PROFILE must be 'clean' or 'recovery', got {profile!r}"
        )

    # Explicit environment variables always win over profile defaults. This lets
    # a presenter tighten one setting without creating another profile.
    # Timeout invariants (see .env-sample): http_timeout < start_to_close so an
    # abandoned request cannot overlap its retry; schedule_to_close <
    # total_budget so model retries end inside the wall-clock budget, leaving
    # room for the deterministic floor.
    defaults = {
        "DEMO_DATA_WAREHOUSE_RETRY_FAILURES": "2" if profile == "recovery" else "0",
        "DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS": "1",
        "DEMO_MODEL_START_TO_CLOSE_SECONDS": "100",
        "DEMO_MODEL_SCHEDULE_TO_CLOSE_SECONDS": "240",
        "DEMO_MODEL_RETRY_DELAY_SECONDS": "3",
        "DEMO_MODEL_MAX_ATTEMPTS": "3",
        "DEMO_MODEL_HTTP_TIMEOUT_SECONDS": "90",
        "ORCHESTRATOR_FALLBACK_MODEL": "gpt-5-mini",
        "DEMO_IMAGE_MAX_ATTEMPTS": "1",
        "DEMO_MODEL_LOCAL": "qwen2.5:14b-instruct",
        "DEMO_MODEL_LOCAL_BASE_URL": "http://localhost:11434/v1",
        "DEMO_MODEL_LOCAL_HTTP_TIMEOUT_SECONDS": "90",
        "DEMO_MODEL_LOCAL_ATTEMPT": "3",
        "DEMO_TOTAL_BUDGET_SECONDS": "300",
        "DEMO_BUDGET_RECHECK_SECONDS": "5",
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)

    return DemoSettings(
        profile=profile,
        model_start_to_close_seconds=int(os.environ["DEMO_MODEL_START_TO_CLOSE_SECONDS"]),
        model_schedule_to_close_seconds=int(
            os.environ["DEMO_MODEL_SCHEDULE_TO_CLOSE_SECONDS"]
        ),
        model_retry_delay_seconds=int(os.environ["DEMO_MODEL_RETRY_DELAY_SECONDS"]),
        model_max_attempts=int(os.environ["DEMO_MODEL_MAX_ATTEMPTS"]),
        model_http_timeout_seconds=int(os.environ["DEMO_MODEL_HTTP_TIMEOUT_SECONDS"]),
        model_fallback=os.environ["ORCHESTRATOR_FALLBACK_MODEL"].strip(),
        image_max_attempts=int(os.environ["DEMO_IMAGE_MAX_ATTEMPTS"]),
        model_local=os.environ["DEMO_MODEL_LOCAL"].strip(),
        model_local_base_url=os.environ["DEMO_MODEL_LOCAL_BASE_URL"].strip(),
        model_local_http_timeout_seconds=int(
            os.environ["DEMO_MODEL_LOCAL_HTTP_TIMEOUT_SECONDS"]
        ),
        model_local_attempt=int(os.environ["DEMO_MODEL_LOCAL_ATTEMPT"]),
        total_budget_seconds=int(os.environ["DEMO_TOTAL_BUDGET_SECONDS"]),
        budget_recheck_seconds=int(os.environ["DEMO_BUDGET_RECHECK_SECONDS"]),
    )
