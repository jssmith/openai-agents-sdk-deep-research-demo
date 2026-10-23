"""Configuration for predictable live-demo timing and recovery behavior.

Settings come entirely from environment variables. This module is a *pure
parser*: it reads the environment, applies code defaults, validates, and returns
a frozen ``DemoSettings``. It never mutates ``os.environ`` — the precedence is
simply ``shell export > .env (via --env-file) > code default``, with no
alternate paths that could silently disagree with what the worker actually does.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from openai_agents.workflows.enterprise_data_activities import (
    DATA_WAREHOUSE_MAX_ATTEMPTS,
)


@dataclass(frozen=True)
class DemoSettings:
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
    # Data-warehouse failure injection: how many leading attempts fail before
    # the lookup succeeds. 0 = clean. The loader validates this stays below
    # DATA_WAREHOUSE_MAX_ATTEMPTS, so an injected-failure run always recovers
    # (on attempt N+1) rather than exhausting the retry budget.
    data_warehouse_retry_failures: int
    data_warehouse_first_attempt_seconds: float


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _str_env(name: str, default: str) -> str:
    # An explicitly empty value is meaningful (e.g. DEMO_MODEL_LOCAL="" disables
    # the local layer), so only fall back to the default when the var is absent.
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip()


def load_demo_settings() -> DemoSettings:
    # DEMO_PROFILE was removed in favor of driving failure injection directly
    # with DEMO_DATA_WAREHOUSE_RETRY_FAILURES. Fail loudly rather than let a
    # stale profile (in a shell or a checked-in .env) silently produce a clean
    # run when the presenter asked for the recovery demo.
    if "DEMO_PROFILE" in os.environ:
        raise ValueError(
            "DEMO_PROFILE was removed; set DEMO_DATA_WAREHOUSE_RETRY_FAILURES "
            "directly (0 = clean, 8 = the ~38s recoverable-failure demo)."
        )

    # Every allowed value must recover within the retry budget: the activity's
    # RetryPolicy allows DATA_WAREHOUSE_MAX_ATTEMPTS attempts, so at most
    # DATA_WAREHOUSE_MAX_ATTEMPTS - 1 leading failures still succeed (on the
    # final attempt). Reject values that would exhaust the budget and turn the
    # recovery demo into a failed run.
    retry_failures = _int_env("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", 0)
    max_failures = DATA_WAREHOUSE_MAX_ATTEMPTS - 1
    if not 0 <= retry_failures <= max_failures:
        raise ValueError(
            "DEMO_DATA_WAREHOUSE_RETRY_FAILURES must be between 0 and "
            f"{max_failures} (it must recover within the "
            f"{DATA_WAREHOUSE_MAX_ATTEMPTS}-attempt retry budget), got "
            f"{retry_failures}"
        )

    settings = DemoSettings(
        model_start_to_close_seconds=_int_env("DEMO_MODEL_START_TO_CLOSE_SECONDS", 100),
        model_schedule_to_close_seconds=_int_env(
            "DEMO_MODEL_SCHEDULE_TO_CLOSE_SECONDS", 300
        ),
        model_retry_delay_seconds=_int_env("DEMO_MODEL_RETRY_DELAY_SECONDS", 3),
        model_max_attempts=_int_env("DEMO_MODEL_MAX_ATTEMPTS", 3),
        model_http_timeout_seconds=_int_env("DEMO_MODEL_HTTP_TIMEOUT_SECONDS", 90),
        model_fallback=_str_env("ORCHESTRATOR_FALLBACK_MODEL", "gpt-5-mini"),
        image_max_attempts=_int_env("DEMO_IMAGE_MAX_ATTEMPTS", 1),
        model_local=_str_env("DEMO_MODEL_LOCAL", "qwen2.5:14b-instruct"),
        model_local_base_url=_str_env(
            "DEMO_MODEL_LOCAL_BASE_URL", "http://localhost:11434/v1"
        ),
        model_local_http_timeout_seconds=_int_env(
            "DEMO_MODEL_LOCAL_HTTP_TIMEOUT_SECONDS", 90
        ),
        model_local_attempt=_int_env("DEMO_MODEL_LOCAL_ATTEMPT", 3),
        data_warehouse_retry_failures=retry_failures,
        data_warehouse_first_attempt_seconds=_float_env(
            "DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS", 1.0
        ),
    )

    # Timeout invariant: an abandoned HTTP request must not overlap its retry.
    if settings.model_http_timeout_seconds >= settings.model_start_to_close_seconds:
        raise ValueError(
            "DEMO_MODEL_HTTP_TIMEOUT_SECONDS "
            f"({settings.model_http_timeout_seconds}) must be less than "
            "DEMO_MODEL_START_TO_CLOSE_SECONDS "
            f"({settings.model_start_to_close_seconds}) so an abandoned request "
            "cannot overlap its retry."
        )

    if settings.model_max_attempts < 1:
        raise ValueError(
            f"DEMO_MODEL_MAX_ATTEMPTS must be >= 1, got {settings.model_max_attempts}"
        )
    if settings.image_max_attempts < 1:
        raise ValueError(
            f"DEMO_IMAGE_MAX_ATTEMPTS must be >= 1, got {settings.image_max_attempts}"
        )

    if settings.model_local:
        _validate_local_layer(settings)

    return settings


def _validate_local_layer(settings: DemoSettings) -> None:
    """Reject configurations where the local layer could never do its job."""
    # Otherwise Temporal stops retrying before the local attempt is reached
    # and the local layer is silently never used.
    if not 1 <= settings.model_local_attempt <= settings.model_max_attempts:
        raise ValueError(
            "DEMO_MODEL_LOCAL_ATTEMPT "
            f"({settings.model_local_attempt}) must be between 1 and "
            f"DEMO_MODEL_MAX_ATTEMPTS ({settings.model_max_attempts}); set "
            "DEMO_MODEL_LOCAL='' to disable the local layer."
        )
    # Same overlap invariant as the cloud client.
    if (
        settings.model_local_http_timeout_seconds
        >= settings.model_start_to_close_seconds
    ):
        raise ValueError(
            "DEMO_MODEL_LOCAL_HTTP_TIMEOUT_SECONDS "
            f"({settings.model_local_http_timeout_seconds}) must be less than "
            "DEMO_MODEL_START_TO_CLOSE_SECONDS "
            f"({settings.model_start_to_close_seconds})."
        )
    # Worst case (every earlier attempt hangs to start-to-close) the local
    # attempt must still get its full HTTP timeout before schedule-to-close
    # expires; otherwise it is starved in exactly the case it exists for.
    worst_case = (settings.model_local_attempt - 1) * (
        settings.model_start_to_close_seconds + settings.model_retry_delay_seconds
    ) + settings.model_local_http_timeout_seconds
    if worst_case > settings.model_schedule_to_close_seconds:
        raise ValueError(
            "DEMO_MODEL_SCHEDULE_TO_CLOSE_SECONDS "
            f"({settings.model_schedule_to_close_seconds}) is too short for the "
            f"local layer: if attempts 1-{settings.model_local_attempt - 1} hang "
            "to start-to-close, the local attempt needs until "
            f"{worst_case}s to use its full DEMO_MODEL_LOCAL_HTTP_TIMEOUT_SECONDS. "
            f"Set DEMO_MODEL_SCHEDULE_TO_CLOSE_SECONDS to at least {worst_case} "
            "(default 300), or DEMO_MODEL_LOCAL='' to disable the local layer."
        )
