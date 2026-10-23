from __future__ import annotations

from types import SimpleNamespace

import pytest
from agents import FunctionTool, WebSearchTool
from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning

from openai_agents import model_provider
from openai_agents.model_provider import (
    MEMORY_ANSWER_PREFIX,
    AttemptAwareOpenAIProvider,
    _LocalModel,
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
    assert isinstance(model, _LocalModel)
    assert model._local == ("local", "qwen2.5:14b-instruct")


class _RecordingModel:
    def __init__(self):
        self.calls = []

    async def get_response(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "response"


def _function_tool() -> FunctionTool:
    async def invoke(ctx, args):
        return ""

    return FunctionTool(
        name="elicit_user",
        description="",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=invoke,
    )


async def _call(model: _LocalModel, tools, settings: ModelSettings):
    return await model.get_response(
        "system", "input", settings, tools, None, [], None, previous_response_id=None
    )


@pytest.mark.asyncio
async def test_local_model_passes_function_tools_through_with_scrubbed_settings():
    local = _RecordingModel()
    settings = ModelSettings(reasoning=Reasoning(effort="low"), temperature=0.5)
    tools = [_function_tool()]

    await _call(_LocalModel(local, "qwen"), tools, settings)

    (args, kwargs), = local.calls
    assert args[0] == "system"
    assert args[2].reasoning is None
    assert args[2].temperature == 0.5
    assert args[3] == tools
    assert kwargs == {"previous_response_id": None}


@pytest.mark.asyncio
async def test_hosted_tools_dropped_and_answer_labeled_as_from_memory():
    """Ollama can't run web search: answer from memory, labeled as such."""
    local = _RecordingModel()
    settings = ModelSettings(reasoning=Reasoning(effort="low"), tool_choice="required")

    await _call(_LocalModel(local, "qwen"), [WebSearchTool()], settings)

    (args, _), = local.calls
    system, _, sent_settings, sent_tools = args[:4]
    assert system.startswith("system\n\n")
    assert MEMORY_ANSWER_PREFIX in system
    assert sent_tools == []
    # "required" with no tools left would be rejected.
    assert sent_settings.tool_choice is None
    assert sent_settings.reasoning is None


@pytest.mark.asyncio
async def test_function_tools_kept_when_hosted_tools_dropped():
    local = _RecordingModel()
    settings = ModelSettings(tool_choice="required")
    fn = _function_tool()

    await _call(_LocalModel(local, "qwen"), [WebSearchTool(), fn], settings)

    (args, _), = local.calls
    assert args[3] == [fn]
    assert args[2].tool_choice == "required"


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
    scrubbed = _LocalModel._scrub(settings)
    assert scrubbed is not None
    assert scrubbed.reasoning is None
    assert scrubbed.verbosity is None
    assert scrubbed.temperature == 0.5
    assert scrubbed.tool_choice == "required"
    assert scrubbed.max_tokens == 1500


def test_scrub_noop_when_already_clean():
    settings = ModelSettings(temperature=0.2)
    # No reasoning/verbosity set -> same object returned (cheap fast path).
    assert _LocalModel._scrub(settings) is settings


def test_scrub_handles_none():
    assert _LocalModel._scrub(None) is None
