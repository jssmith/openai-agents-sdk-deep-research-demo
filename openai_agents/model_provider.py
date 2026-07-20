"""Attempt-aware model provider used by the live demo worker.

Maps Temporal model-activity attempts to a layered synthesis fallback:

  attempt 1        -> primary cloud model (e.g. gpt-5), Responses API
  attempt 2        -> faster cloud fallback (e.g. gpt-5-mini), Responses API
  attempt >= N     -> local model via an OpenAI-compatible server (Ollama),
                      Chat Completions API

The retry itself is still driven by Temporal; this provider only chooses which
model/endpoint serves each attempt. The layer above this (the workflow's
deterministic floor) covers the case where even the local attempt fails.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from agents import Model, ModelProvider, OpenAIProvider
from agents.model_settings import ModelSettings
from openai import AsyncOpenAI
from temporalio import activity

logger = logging.getLogger(__name__)


class _ChatSettingsScrubbingModel(Model):
    """Wrap a chat-completions Model, dropping Responses-only settings.

    The chat-completions path forwards ``reasoning_effort`` and ``verbosity``
    verbatim, but a local OpenAI-compatible endpoint (Ollama) rejects them. We
    null those two fields before delegating; everything else (temperature,
    tool_choice, max_tokens, response_format) passes through unchanged.
    """

    def __init__(self, inner: Model) -> None:
        self._inner = inner

    @staticmethod
    def _scrub(model_settings: ModelSettings | None) -> ModelSettings | None:
        if model_settings is None:
            return model_settings
        if model_settings.reasoning is None and model_settings.verbosity is None:
            return model_settings
        return dataclasses.replace(model_settings, reasoning=None, verbosity=None)

    def _scrub_args(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        # The activity calls with model_settings as a keyword; handle the
        # positional form too for safety (it is the 3rd positional parameter).
        if "model_settings" in kwargs:
            kwargs = {**kwargs, "model_settings": self._scrub(kwargs["model_settings"])}
        elif len(args) >= 3:
            args = args[:2] + (self._scrub(args[2]),) + args[3:]
        return args, kwargs

    async def get_response(self, *args: Any, **kwargs: Any):
        args, kwargs = self._scrub_args(args, kwargs)
        return await self._inner.get_response(*args, **kwargs)

    def stream_response(self, *args: Any, **kwargs: Any):
        args, kwargs = self._scrub_args(args, kwargs)
        return self._inner.stream_response(*args, **kwargs)

    def get_retry_advice(self, request: Any):
        return self._inner.get_retry_advice(request)

    async def close(self) -> None:
        await self._inner.close()


class AttemptAwareOpenAIProvider(ModelProvider):
    """Route each Temporal model-activity attempt to a fallback layer.

    Attempt two swaps in a faster cloud model; attempt ``local_attempt`` and
    beyond route to a local OpenAI-compatible endpoint (Ollama) over the
    Chat Completions API. Keeping a slow primary from consuming the whole
    live-demo budget is the point.
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
        primary_client = AsyncOpenAI(
            max_retries=0,
            timeout=http_timeout_seconds,
        )
        fallback_client = AsyncOpenAI(
            max_retries=0,
            timeout=http_timeout_seconds,
        )
        self._primary = OpenAIProvider(openai_client=primary_client)
        self._fallback = OpenAIProvider(openai_client=fallback_client)
        self._fallback_model = fallback_model

        # Local layer: its own client so the httpx read-timeout is the hard cap
        # that stops a runaway local generation from hanging the activity.
        # use_responses=False forces the Chat Completions API (Ollama does not
        # implement the Responses API).
        local_client = AsyncOpenAI(
            base_url=local_base_url,
            api_key=local_api_key,
            max_retries=0,
            timeout=local_http_timeout_seconds,
        )
        self._local = OpenAIProvider(openai_client=local_client, use_responses=False)
        self._local_model = local_model
        self._local_attempt = local_attempt

    def get_model(self, model_name: str | None) -> Model:
        try:
            attempt = activity.info().attempt
        except RuntimeError:
            attempt = 1

        if self._local_model and attempt >= self._local_attempt:
            logger.warning(
                "Using LOCAL model=%s (Ollama, chat completions) for Temporal "
                "activity attempt=%s (primary=%s)",
                self._local_model,
                attempt,
                model_name,
            )
            inner = self._local.get_model(self._local_model)
            return _ChatSettingsScrubbingModel(inner)

        if attempt > 1 and self._fallback_model and model_name != self._fallback_model:
            logger.warning(
                "Using fallback model=%s for Temporal activity attempt=%s "
                "(primary=%s)",
                self._fallback_model,
                attempt,
                model_name,
            )
            return self._fallback.get_model(self._fallback_model)

        return self._primary.get_model(model_name)

    async def aclose(self) -> None:
        await self._primary.aclose()
        await self._fallback.aclose()
        await self._local.aclose()
