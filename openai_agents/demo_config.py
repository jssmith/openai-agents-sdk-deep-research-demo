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
    # Optional hard wall-clock cap on the whole run (0 = disabled, the default;
    # a single timer, not a polling loop, so the Temporal history stays clean).
    total_budget_seconds: int


def load_demo_settings() -> DemoSettings:
    profile = os.getenv("DEMO_PROFILE", "clean").strip().lower()
    if profile not in {"clean", "recovery"}:
        raise ValueError(
            f"DEMO_PROFILE must be 'clean' or 'recovery', got {profile!r}"
        )

    # Explicit environment variables always win over profile defaults. This lets
    # a presenter tighten one setting without creating another profile.
    # Timeout invariant (see .env-sample): http_timeout < start_to_close so an
    # abandoned request cannot overlap its retry. The optional total-budget cap
    # is off by default (0); when enabled, keep it > schedule_to_close so model
    # retries can finish inside the budget before the floor takes over.
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
        "DEMO_TOTAL_BUDGET_SECONDS": "0",
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
    )
