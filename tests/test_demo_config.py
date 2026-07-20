from __future__ import annotations

import os

from openai_agents.demo_config import load_demo_settings


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
        "DEMO_BUDGET_RECHECK_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_clean_profile_defaults(monkeypatch):
    _clear_demo_env(monkeypatch)

    settings = load_demo_settings()

    assert settings.profile == "clean"
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
    assert settings.total_budget_seconds == 300
    assert settings.budget_recheck_seconds == 5


def test_timeout_invariants_hold_by_default(monkeypatch):
    _clear_demo_env(monkeypatch)

    settings = load_demo_settings()

    # An abandoned HTTP request must not overlap its retry.
    assert settings.model_http_timeout_seconds < settings.model_start_to_close_seconds
    # Model retries must finish inside the wall-clock budget, leaving room for
    # the deterministic floor.
    assert settings.model_schedule_to_close_seconds < settings.total_budget_seconds


def test_recovery_profile_injects_two_warehouse_failures(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_PROFILE", "recovery")

    settings = load_demo_settings()

    assert settings.profile == "recovery"
    assert settings.model_max_attempts == 3
    assert os.environ["DEMO_DATA_WAREHOUSE_RETRY_FAILURES"] == "2"


def test_explicit_values_override_profile_defaults(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_PROFILE", "recovery")
    monkeypatch.setenv("DEMO_MODEL_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "0")

    settings = load_demo_settings()

    assert settings.model_max_attempts == 1
    assert os.environ["DEMO_DATA_WAREHOUSE_RETRY_FAILURES"] == "0"


def test_explicit_local_and_budget_overrides(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_MODEL_LOCAL", "llama3.1:8b")
    monkeypatch.setenv("DEMO_MODEL_LOCAL_ATTEMPT", "2")
    monkeypatch.setenv("DEMO_TOTAL_BUDGET_SECONDS", "120")
    monkeypatch.setenv("DEMO_BUDGET_RECHECK_SECONDS", "10")

    settings = load_demo_settings()

    assert settings.model_local == "llama3.1:8b"
    assert settings.model_local_attempt == 2
    assert settings.total_budget_seconds == 120
    assert settings.budget_recheck_seconds == 10


def test_local_layer_can_be_disabled(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_MODEL_LOCAL", "")

    settings = load_demo_settings()

    assert settings.model_local == ""
