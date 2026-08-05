from __future__ import annotations

import pytest

from openai_agents.demo_config import load_demo_settings
from openai_agents.workflows.enterprise_data_activities import (
    DATA_WAREHOUSE_MAX_ATTEMPTS,
)


def _clear_demo_env(monkeypatch):
    for name in (
        "DEMO_PROFILE",
        "DEMO_DATA_WAREHOUSE_RETRY_FAILURES",
        "DEMO_DATA_WAREHOUSE_FIRST_ATTEMPT_SECONDS",
        "DEMO_MODEL_START_TO_CLOSE_SECONDS",
        "DEMO_MODEL_SCHEDULE_TO_CLOSE_SECONDS",
        "DEMO_MODEL_RETRY_DELAY_SECONDS",
        "DEMO_MODEL_MAX_ATTEMPTS",
        "DEMO_MODEL_HTTP_TIMEOUT_SECONDS",
        "ORCHESTRATOR_FALLBACK_MODEL",
        "DEMO_IMAGE_MAX_ATTEMPTS",
        "DEMO_MODEL_LOCAL",
        "DEMO_MODEL_LOCAL_BASE_URL",
        "DEMO_MODEL_LOCAL_HTTP_TIMEOUT_SECONDS",
        "DEMO_MODEL_LOCAL_ATTEMPT",
        "DEMO_TOTAL_BUDGET_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch):
    _clear_demo_env(monkeypatch)

    settings = load_demo_settings()

    assert settings.model_start_to_close_seconds == 100
    assert settings.model_schedule_to_close_seconds == 240
    assert settings.model_retry_delay_seconds == 3
    assert settings.model_max_attempts == 3
    assert settings.model_http_timeout_seconds == 90
    assert settings.model_fallback == "gpt-5-mini"
    assert settings.image_max_attempts == 1
    # Local fallback + budget defaults.
    assert settings.model_local == "qwen2.5:14b-instruct"
    assert settings.model_local_base_url == "http://localhost:11434/v1"
    assert settings.model_local_http_timeout_seconds == 90
    assert settings.model_local_attempt == 3
    # Wall-clock cap disabled by default (keeps the Temporal history clean).
    assert settings.total_budget_seconds == 0
    # Failure injection off by default (clean run).
    assert settings.data_warehouse_retry_failures == 0
    assert settings.data_warehouse_first_attempt_seconds == 1.0


def test_timeout_invariant_holds_by_default(monkeypatch):
    _clear_demo_env(monkeypatch)

    settings = load_demo_settings()

    # An abandoned HTTP request must not overlap its retry.
    assert settings.model_http_timeout_seconds < settings.model_start_to_close_seconds


def test_violated_timeout_invariant_fails(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_MODEL_HTTP_TIMEOUT_SECONDS", "100")
    monkeypatch.setenv("DEMO_MODEL_START_TO_CLOSE_SECONDS", "100")

    with pytest.raises(ValueError, match="must be less than"):
        load_demo_settings()


def test_warehouse_retry_failures_parsed(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "8")

    settings = load_demo_settings()

    assert settings.data_warehouse_retry_failures == 8


def test_stale_demo_profile_fails_fast(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_PROFILE", "recovery")

    # A stale DEMO_PROFILE must not silently produce a clean run.
    with pytest.raises(ValueError, match="DEMO_PROFILE was removed"):
        load_demo_settings()


def test_max_recoverable_failures_accepted(monkeypatch):
    _clear_demo_env(monkeypatch)
    # One below the retry budget is the largest value that still recovers.
    monkeypatch.setenv(
        "DEMO_DATA_WAREHOUSE_RETRY_FAILURES", str(DATA_WAREHOUSE_MAX_ATTEMPTS - 1)
    )

    settings = load_demo_settings()

    assert settings.data_warehouse_retry_failures == DATA_WAREHOUSE_MAX_ATTEMPTS - 1


@pytest.mark.parametrize(
    "value",
    ["-1", str(DATA_WAREHOUSE_MAX_ATTEMPTS), str(DATA_WAREHOUSE_MAX_ATTEMPTS + 1)],
)
def test_warehouse_retry_failures_out_of_range_fails(monkeypatch, value):
    _clear_demo_env(monkeypatch)
    # A value that would exhaust the retry budget (>= max attempts) is rejected.
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", value)

    with pytest.raises(ValueError, match="must be between 0 and"):
        load_demo_settings()


def test_warehouse_retry_failures_malformed_fails(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "lots")

    with pytest.raises(ValueError, match="must be an integer"):
        load_demo_settings()


def test_explicit_values_parsed(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_MODEL_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "0")

    settings = load_demo_settings()

    assert settings.model_max_attempts == 1
    assert settings.data_warehouse_retry_failures == 0


def test_explicit_local_and_budget_overrides(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_MODEL_LOCAL", "llama3.1:8b")
    monkeypatch.setenv("DEMO_MODEL_LOCAL_ATTEMPT", "2")
    monkeypatch.setenv("DEMO_TOTAL_BUDGET_SECONDS", "120")

    settings = load_demo_settings()

    assert settings.model_local == "llama3.1:8b"
    assert settings.model_local_attempt == 2
    assert settings.total_budget_seconds == 120


def test_local_layer_can_be_disabled(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_MODEL_LOCAL", "")

    settings = load_demo_settings()

    assert settings.model_local == ""
