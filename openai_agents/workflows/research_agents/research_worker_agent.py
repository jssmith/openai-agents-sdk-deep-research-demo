import os

from agents import Agent, WebSearchTool
from agents.model_settings import ModelSettings
from dotenv import load_dotenv
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel, Field

load_dotenv()


INSTRUCTIONS = (
    "You are a focused research worker. You are given a single subquery and a brief "
    "reason for researching it. Use the web_search tool to gather evidence, then "
    "produce a short factual summary.\n"
    "\n"
    "The summary must be 1-2 paragraphs and under 250 words. Capture the main points "
    "directly. Skip preamble and commentary - your output is consumed by another "
    "agent that synthesizes a report.\n"
    "\n"
    "Always call web_search at least once. If the search yields nothing useful, say "
    "so explicitly in the summary rather than fabricating details."
)


class SearchSummary(BaseModel):
    """Output of a single research worker. Both fields are required and non-empty."""

    query: str = Field(min_length=1, description="The subquery that was researched.")
    summary: str = Field(
        min_length=1,
        description="A 1-2 paragraph factual summary of the search results.",
    )


def new_research_worker_agent() -> Agent:
    """Build a research worker sub-agent for a single subquery.

    The agent runs in-process inside the orchestrator's workflow via Runner.run.
    """
    return Agent(
        name="ResearchWorkerAgent",
        instructions=INSTRUCTIONS,
        model=os.getenv("RESEARCH_WORKER_MODEL", "gpt-5-mini"),
        tools=[WebSearchTool(search_context_size="low")],
        model_settings=ModelSettings(
            tool_choice="required",
            reasoning=Reasoning(effort="low"),
            max_tokens=1500,
        ),
        output_type=SearchSummary,
    )
