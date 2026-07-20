from __future__ import annotations

from types import SimpleNamespace

from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning

from openai_agents import model_provider
from openai_agents.model_provider import (
    AttemptAwareOpenAIProvider,
    _ChatSettingsScrubbingModel,
)


class _FakeProvider:
    def __init__(self, label):
        self.label = label

    def get_model(self, model_name):
        return (self.label, model_name)

    async def aclose(self):
        return None


def _patch_providers(monkeypatch, attempt):
    """Patch out the three OpenAIProviders (primary, fallback, local) + activity."""
    providers = iter(
        [_FakeProvider("primary"), _FakeProvider("fallback"), _FakeProvider("local")]
    )
    monkeypatch.setattr(model_provider, "AsyncOpenAI", lambda **_: object())
    monkeypatch.setattr(model_provider, "OpenAIProvider", lambda **_: next(providers))
    monkeypatch.setattr(
        model_provider.activity, "info", lambda: SimpleNamespace(attempt=attempt)
    )


def test_provider_uses_primary_model_on_first_attempt(monkeypatch):
    _patch_providers(monkeypatch, attempt=1)
    provider = AttemptAwareOpenAIProvider("gpt-5-mini", 30, local_model="qwen")
    assert provider.get_model("gpt-5") == ("primary", "gpt-5")


def test_provider_uses_fallback_model_on_retry(monkeypatch):
    _patch_providers(monkeypatch, attempt=2)
    provider = AttemptAwareOpenAIProvider("gpt-5-mini", 30, local_model="qwen")
    assert provider.get_model("gpt-5") == ("fallback", "gpt-5-mini")


def test_provider_routes_to_local_on_third_attempt(monkeypatch):
    _patch_providers(monkeypatch, attempt=3)
    provider = AttemptAwareOpenAIProvider(
        "gpt-5-mini", 30, local_model="qwen2.5:14b-instruct", local_attempt=3
    )
    model = provider.get_model("gpt-5")
    # Local path returns the scrubbing wrapper around the local provider's model.
    assert isinstance(model, _ChatSettingsScrubbingModel)
    assert model._inner == ("local", "qwen2.5:14b-instruct")


def test_local_attempt_threshold_respected(monkeypatch):
    # With local_attempt=4, attempt 3 should still use the cloud fallback.
    _patch_providers(monkeypatch, attempt=3)
    provider = AttemptAwareOpenAIProvider(
        "gpt-5-mini", 30, local_model="qwen", local_attempt=4
    )
    assert provider.get_model("gpt-5") == ("fallback", "gpt-5-mini")


def test_empty_local_model_never_routes_local(monkeypatch):
    # Local disabled (local_model=""): high attempt still uses cloud fallback.
    _patch_providers(monkeypatch, attempt=9)
    provider = AttemptAwareOpenAIProvider("gpt-5-mini", 30, local_model="")
    assert provider.get_model("gpt-5") == ("fallback", "gpt-5-mini")


def test_scrub_nulls_reasoning_and_verbosity_preserves_rest():
    settings = ModelSettings(
        reasoning=Reasoning(effort="low"),
        verbosity="low",
        temperature=0.5,
        tool_choice="required",
        max_tokens=1500,
    )
    scrubbed = _ChatSettingsScrubbingModel._scrub(settings)
    assert scrubbed is not None
    assert scrubbed.reasoning is None
    assert scrubbed.verbosity is None
    assert scrubbed.temperature == 0.5
    assert scrubbed.tool_choice == "required"
    assert scrubbed.max_tokens == 1500


def test_scrub_noop_when_already_clean():
    settings = ModelSettings(temperature=0.2)
    # No reasoning/verbosity set -> same object returned (cheap fast path).
    assert _ChatSettingsScrubbingModel._scrub(settings) is settings


def test_scrub_handles_none():
    assert _ChatSettingsScrubbingModel._scrub(None) is None
