"""Standalone validation of the local (Ollama) synthesis path.

Exercises the exact integration the workflow's layer-3 fallback relies on,
WITHOUT needing OpenAI or Temporal:

  * AttemptAwareOpenAIProvider routing to the local Chat Completions endpoint
  * _ChatSettingsScrubbingModel stripping reasoning/verbosity (which gpt-5 sets
    and Ollama rejects)
  * structured output (pydantic output_type) over Ollama's response_format

Run: uv run python scripts/validate_local_model.py [model_name]

Requires `ollama serve` and the model pulled (e.g. `ollama pull qwen2.5:3b`).
"""

from __future__ import annotations

import asyncio
import sys

from agents import Agent, Runner
from agents.model_settings import ModelSettings
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel, Field

from openai_agents.model_provider import AttemptAwareOpenAIProvider


class MiniReport(BaseModel):
    """A small structured output, mirroring the real FinalizeReportRequest shape."""

    short_summary: str = Field(min_length=1)
    markdown_report: str = Field(min_length=50)
    follow_up_questions: list[str] = Field(min_length=2, max_length=5)


async def main() -> int:
    model_name = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:3b"

    # local_attempt=1 forces the local path immediately (no Temporal activity
    # context here, so activity.info() raises and attempt defaults to 1).
    provider = AttemptAwareOpenAIProvider(
        fallback_model="gpt-5-mini",
        http_timeout_seconds=90,
        local_model=model_name,
        local_base_url="http://localhost:11434/v1",
        local_api_key="ollama",
        local_http_timeout_seconds=120,
        local_attempt=1,
    )
    model = provider.get_model("gpt-5")
    print(f"Routed model type: {type(model).__name__} (expect _ChatSettingsScrubbingModel)")

    # reasoning + verbosity are the gpt-5-only settings that must be scrubbed
    # before the request reaches Ollama.
    agent = Agent(
        name="LocalSynthesisProbe",
        instructions=(
            "You are a research writer. Produce a concise structured report for "
            "the user's topic. markdown_report must be at least a short paragraph."
        ),
        model=model,
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="minimal"),
            verbosity="low",
        ),
        output_type=MiniReport,
    )

    result = await Runner.run(
        agent,
        "Topic: the benefits of durable execution for AI agent workflows.",
    )
    report = result.final_output_as(MiniReport)

    print("\n=== structured output received ===")
    print("short_summary:", report.short_summary[:120])
    print("markdown_report length:", len(report.markdown_report))
    print("follow_up_questions:", report.follow_up_questions)

    assert report.short_summary.strip()
    assert len(report.markdown_report) >= 50
    assert 2 <= len(report.follow_up_questions) <= 5
    print("\nPASS: local model produced valid structured output via the SDK "
          "chat-completions path with reasoning/verbosity scrubbed.")
    await provider.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
