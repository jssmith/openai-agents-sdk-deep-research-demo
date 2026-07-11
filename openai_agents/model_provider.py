"""Attempt-aware model provider used by the live demo worker."""

from __future__ import annotations

import logging

from agents import Model, ModelProvider, OpenAIProvider
from openai import AsyncOpenAI
from temporalio import activity

logger = logging.getLogger(__name__)


class AttemptAwareOpenAIProvider(ModelProvider):
    """Use a faster model when Temporal retries a model activity.

    The retry is still controlled by Temporal. This provider only changes the
    model selected for attempt two, which keeps a slow primary request from
    consuming the entire live-demo budget.
    """

    def __init__(
        self,
        fallback_model: str,
        http_timeout_seconds: int,
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

    def get_model(self, model_name: str | None) -> Model:
        try:
            attempt = activity.info().attempt
        except RuntimeError:
            attempt = 1

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
