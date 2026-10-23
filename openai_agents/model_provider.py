"""Attempt-aware model provider used by the live demo worker.

Maps Temporal model-activity attempts to a layered fallback:

  attempt 1        -> primary cloud model (e.g. gpt-5), Responses API
  attempt 2        -> faster cloud fallback (e.g. gpt-5-mini), Responses API
  attempt >= N     -> local model via an OpenAI-compatible server (Ollama),
                      Chat Completions API; requests that use hosted tools
                      (e.g. web search) run without them and answer from the
                      model's own knowledge, labeled as such

The retry itself is still driven by Temporal; this provider only chooses which
model/endpoint serves each attempt. So a later layer only runs if the earlier
attempt failed retryably: the plugin marks 4xx responses other than
408/409/429 non-retryable, which skips the remaining layers entirely. If the
last attempt fails too, the activity fails and so does the workflow.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from agents import FunctionTool, Model, ModelProvider, OpenAIProvider, Tool
from agents.model_settings import ModelSettings
from openai import AsyncOpenAI
from temporalio import activity

logger = logging.getLogger(__name__)


# Required opening of any answer the local model gives without the hosted tools
# (web search) the request asked for, so it is never mistaken for search results.
MEMORY_ANSWER_PREFIX = "From model knowledge, not a web search:"

_NO_HOSTED_TOOLS_NOTE = (
    "IMPORTANT: Web search and other hosted tools are unavailable for this "
    "request. This overrides any instruction above to call them. Answer from "
    "your own knowledge, which may be out of date, and begin your summary "
    f"with exactly: {MEMORY_ANSWER_PREFIX}"
)


class _LocalModel(Model):
    """Adapt requests for the local chat-completions model (Ollama).

    Ollama's Chat Completions API only runs function tools; hosted tools such
    as WebSearchTool exist only in the Responses API. They are dropped, and
    the model is told to answer from its own knowledge and label the answer
    with MEMORY_ANSWER_PREFIX. ``reasoning`` and ``verbosity`` are nulled: the
    chat-completions path forwards them verbatim and Ollama rejects them.
    Everything else (temperature, max_tokens, response_format) passes through.
    """

    def __init__(self, local: Model, local_name: str) -> None:
        self._local = local
        self._local_name = local_name

    @staticmethod
    def _scrub(model_settings: ModelSettings | None) -> ModelSettings | None:
        if model_settings is None:
            return model_settings
        if model_settings.reasoning is None and model_settings.verbosity is None:
            return model_settings
        return dataclasses.replace(model_settings, reasoning=None, verbosity=None)

    def _adapt(
        self,
        system_instructions: str | None,
        model_settings: ModelSettings,
        tools: list[Tool],
    ) -> tuple[str | None, ModelSettings, list[Tool]]:
        model_settings = self._scrub(model_settings)
        hosted = [t.name for t in tools if not isinstance(t, FunctionTool)]
        if not hosted:
            logger.warning(
                "Using LOCAL model=%s (Ollama, chat completions)", self._local_name
            )
            return system_instructions, model_settings, tools

        logger.warning(
            "Using LOCAL model=%s without hosted tools %s; it answers from its "
            "own knowledge",
            self._local_name,
            hosted,
        )
        tools = [t for t in tools if isinstance(t, FunctionTool)]
        if not tools and model_settings.tool_choice is not None:
            # e.g. tool_choice="required" with nothing left to call.
            model_settings = dataclasses.replace(model_settings, tool_choice=None)
        system_instructions = (
            f"{system_instructions}\n\n{_NO_HOSTED_TOOLS_NOTE}"
            if system_instructions
            else _NO_HOSTED_TOOLS_NOTE
        )
        return system_instructions, model_settings, tools

    async def get_response(
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: Any,
        handoffs: Any,
        tracing: Any,
        **kwargs: Any,
    ):
        system_instructions, model_settings, tools = self._adapt(
            system_instructions, model_settings, tools
        )
        return await self._local.get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            **kwargs,
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: Any,
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: Any,
        handoffs: Any,
        tracing: Any,
        **kwargs: Any,
    ):
        system_instructions, model_settings, tools = self._adapt(
            system_instructions, model_settings, tools
        )
        return self._local.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            **kwargs,
        )

    def get_retry_advice(self, request: Any):
        return self._local.get_retry_advice(request)

    async def close(self) -> None:
        await self._local.close()


class AttemptAwareOpenAIProvider(ModelProvider):
    """Route each Temporal model-activity attempt to a fallback layer.

    Attempt two swaps in a faster cloud model; attempt ``local_attempt`` and
    beyond route to a local OpenAI-compatible endpoint (Ollama) over the
    Chat Completions API. The point is to keep a run going when a cloud
    model is down or hung, without cutting off calls that are merely slow.
    """

    def __init__(
        self,
        fallback_model: str,
        http_timeout_seconds: int,
        local_model: str = "",
        local_base_url: str = "http://localhost:11434/v1",
        local_api_key: str = "ollama",
        local_http_timeout_seconds: int = 90,
        local_attempt: int = 3,
    ) -> None:
        # One cloud client serves the primary and fallback models. Retries are
        # Temporal's, so the client makes a single request per attempt.
        self._cloud = OpenAIProvider(
            openai_client=AsyncOpenAI(max_retries=0, timeout=http_timeout_seconds)
        )
        self._fallback_model = fallback_model

        # Local layer, built only when enabled. Its own client so the httpx
        # read-timeout is the hard cap that stops a runaway local generation
        # from hanging the activity. use_responses=False forces the Chat
        # Completions API (Ollama does not implement the Responses API).
        self._local: OpenAIProvider | None = None
        self._local_model = local_model
        self._local_attempt = local_attempt
        if local_model:
            local_client = AsyncOpenAI(
                base_url=local_base_url,
                api_key=local_api_key,
                max_retries=0,
                timeout=local_http_timeout_seconds,
            )
            self._local = OpenAIProvider(
                openai_client=local_client, use_responses=False
            )

    def get_model(self, model_name: str | None) -> Model:
        try:
            attempt = activity.info().attempt
        except RuntimeError:
            attempt = 1

        if self._local is not None and attempt >= self._local_attempt:
            return _LocalModel(
                self._local.get_model(self._local_model), self._local_model
            )

        if attempt > 1:
            return self._cloud_retry_model(model_name, attempt)

        return self._cloud.get_model(model_name)

    def _cloud_retry_model(self, model_name: str | None, attempt: int) -> Model:
        """The cloud model for a retry: the faster fallback, if configured."""
        if self._fallback_model and model_name != self._fallback_model:
            logger.warning(
                "Using fallback model=%s for Temporal activity attempt=%s "
                "(primary=%s)",
                self._fallback_model,
                attempt,
                model_name,
            )
            return self._cloud.get_model(self._fallback_model)
        return self._cloud.get_model(model_name)

    async def aclose(self) -> None:
        await self._cloud.aclose()
        if self._local is not None:
            await self._local.aclose()
