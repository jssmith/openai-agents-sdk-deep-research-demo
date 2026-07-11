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
    ):
        monkeypatch.delenv(name, raising=False)


def test_clean_profile_defaults(monkeypatch):
    _clear_demo_env(monkeypatch)

    settings = load_demo_settings()

    assert settings.profile == "clean"
    assert settings.model_start_to_close_seconds == 35
    assert settings.model_schedule_to_close_seconds == 75
    assert settings.model_retry_delay_seconds == 3
    assert settings.model_max_attempts == 2
    assert settings.model_http_timeout_seconds == 30
    assert settings.model_fallback == "gpt-5-mini"
    assert settings.image_max_attempts == 1


def test_recovery_profile_injects_two_warehouse_failures(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_PROFILE", "recovery")

    settings = load_demo_settings()

    assert settings.profile == "recovery"
    assert settings.model_max_attempts == 2
    assert os.environ["DEMO_DATA_WAREHOUSE_RETRY_FAILURES"] == "2"


def test_explicit_values_override_profile_defaults(monkeypatch):
    _clear_demo_env(monkeypatch)
    monkeypatch.setenv("DEMO_PROFILE", "recovery")
    monkeypatch.setenv("DEMO_MODEL_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DEMO_DATA_WAREHOUSE_RETRY_FAILURES", "0")

    settings = load_demo_settings()

    assert settings.model_max_attempts == 1
    assert os.environ["DEMO_DATA_WAREHOUSE_RETRY_FAILURES"] == "0"
