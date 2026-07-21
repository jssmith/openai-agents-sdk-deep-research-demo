"""Chaos model provider for adversarial workflow tests.

Lets a test force per-attempt model failures (timeout/error) by reading the
Temporal activity attempt, and otherwise delegates to a caller-supplied response
function. Model calls run inside the model activity, so ``activity.info().attempt``
is the retry attempt of the current model call.
"""

from __future__ import annotations

from typing import Any, Callable

from agents import Model, ModelProvider
from agents.items import ModelResponse
from temporalio import activity

# A response function receives (system_instructions, tools) and returns a
# ModelResponse. It may raise to simulate a model failure on that specific call.
ResponseFn = Callable[[Any, Any], ModelResponse]


class _RaisingModel(Model):
    """A model whose every call raises, simulating a hard model failure."""

    def __init__(self, kind: str = "timeout") -> None:
        self._kind = kind

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        if self._kind == "timeout":
            raise TimeoutError("chaos: injected model timeout")
        raise RuntimeError("chaos: injected model error")

    def stream_response(self, *args: Any, **kwargs: Any):
        raise RuntimeError("chaos: streaming not supported in tests")

    def get_retry_advice(self, request: Any):
        return None

    async def close(self) -> None:
        return None


class _CallableModel(Model):
    """A model that defers each response to a caller-supplied function."""

    def __init__(self, fn: ResponseFn, counter: list[int]) -> None:
        self._fn = fn
        self._counter = counter

    async def get_response(
        self,
        system_instructions: Any,
        input: Any,
        model_settings: Any,
        tools: Any,
        output_schema: Any,
        handoffs: Any,
        tracing: Any,
        **kwargs: Any,
    ) -> ModelResponse:
        self._counter[0] += 1
        return self._fn(system_instructions, tools)

    def stream_response(self, *args: Any, **kwargs: Any):
        raise RuntimeError("chaos: streaming not supported in tests")

    def get_retry_advice(self, request: Any):
        return None

    async def close(self) -> None:
        return None


class ChaosModelProvider(ModelProvider):
    """Route each model-activity attempt to a fault or the response function.

    Args:
        response_fn: called for non-faulted attempts to produce a ModelResponse.
            May itself raise to simulate a failure on a specific logical turn.
        fault_all: if True, every attempt raises (simulates total model outage).
        fault_attempts: specific attempt numbers that should raise.
        fault_kind: "timeout" or "error".
    """

    def __init__(
        self,
        response_fn: ResponseFn | None = None,
        *,
        fault_all: bool = False,
        fault_attempts: set[int] | None = None,
        fault_kind: str = "timeout",
    ) -> None:
        self._response_fn = response_fn
        self._fault_all = fault_all
        self._fault_attempts = fault_attempts or set()
        self._fault_kind = fault_kind
        # Number of times the response function was actually invoked. Lets a
        # test assert the orchestrator was cancelled early (e.g. by the budget
        # guard) rather than running every retry to exhaustion.
        self.response_call_count: list[int] = [0]

    def get_model(self, model_name: str | None) -> Model:
        try:
            attempt = activity.info().attempt
        except RuntimeError:
            attempt = 1

        if self._fault_all or attempt in self._fault_attempts:
            return _RaisingModel(self._fault_kind)

        if self._response_fn is None:
            raise RuntimeError("ChaosModelProvider: no response_fn for a live attempt")
        return _CallableModel(self._response_fn, self.response_call_count)
