# Agent used to synthesize a final report from the individual summaries.
import os

from agents import Agent
from agents.model_settings import ModelSettings
from dotenv import load_dotenv
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel

# Load environment variables
load_dotenv()


PROMPT = (
    "You are a senior researcher tasked with writing a clear, executive-ready report for a research query. "
    "You will be provided with the original query, and some initial research done by a research "
    "assistant.\n"
    "Generate the report directly without first writing a separate outline.\n"
    "The final output should be in markdown format. Aim for 450-650 words. Include:\n"
    "- A short introduction with context\n"
    "- 3-5 sections with clear headings\n"
    "- Direct analysis and ranked takeaways where useful\n"
    "- Specific examples, data points, and evidence where available\n"
    "- A concise conclusion with implications\n"
    "Favor substance over length. Avoid filler, repeated caveats, and generic background."
)


class ReportData(BaseModel):
    short_summary: str
    """A short 2-3 sentence summary of the findings."""

    markdown_report: str
    """The final report"""

    follow_up_questions: list[str]
    """Suggested topics to research further"""


def new_writer_agent():
    return Agent(
        name="WriterAgent",
        instructions=PROMPT,
        model=os.getenv("WRITER_MODEL", "gpt-5-mini"),
        model_settings=ModelSettings(
            max_tokens=int(os.getenv("WRITER_MAX_TOKENS", "2500")),
            reasoning=Reasoning(
                effort=os.getenv("WRITER_REASONING_EFFORT", "minimal")
            ),
            verbosity=os.getenv("WRITER_VERBOSITY", "low"),
        ),
        output_type=ReportData,
    )
