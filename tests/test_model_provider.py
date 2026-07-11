from __future__ import annotations

from types import SimpleNamespace

from openai_agents import model_provider
from openai_agents.model_provider import AttemptAwareOpenAIProvider


class _FakeProvider:
    def __init__(self, label):
        self.label = label

    def get_model(self, model_name):
        return (self.label, model_name)

    async def aclose(self):
        return None


def test_provider_uses_primary_model_on_first_attempt(monkeypatch):
    providers = iter([_FakeProvider("primary"), _FakeProvider("fallback")])
    monkeypatch.setattr(model_provider, "AsyncOpenAI", lambda **_: object())
    monkeypatch.setattr(model_provider, "OpenAIProvider", lambda **_: next(providers))
    monkeypatch.setattr(
        model_provider.activity,
        "info",
        lambda: SimpleNamespace(attempt=1),
    )

    provider = AttemptAwareOpenAIProvider("gpt-5-mini", 30)

    assert provider.get_model("gpt-5") == ("primary", "gpt-5")


def test_provider_uses_fallback_model_on_retry(monkeypatch):
    providers = iter([_FakeProvider("primary"), _FakeProvider("fallback")])
    monkeypatch.setattr(model_provider, "AsyncOpenAI", lambda **_: object())
    monkeypatch.setattr(model_provider, "OpenAIProvider", lambda **_: next(providers))
    monkeypatch.setattr(
        model_provider.activity,
        "info",
        lambda: SimpleNamespace(attempt=2),
    )

    provider = AttemptAwareOpenAIProvider("gpt-5-mini", 30)

    assert provider.get_model("gpt-5") == ("fallback", "gpt-5-mini")
