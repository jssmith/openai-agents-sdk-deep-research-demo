from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel


class ReportData(BaseModel):
    """Final report payload surfaced to the UI."""

    short_summary: str
    markdown_report: str
    follow_up_questions: list[str]


class UserQueryInput(BaseModel):
    """Input for initial user research query."""

    query: str


class ElicitationResponseInput(BaseModel):
    """Input from the FE-BE contract: a response to one pending elicitation."""

    elicitation_id: str
    response: str


class Elicitation(BaseModel):
    """A request from the agent for one piece of human input.

    Each elicitation has a unique id (assigned by the workflow). The UI sees
    pending and completed elicitations through get_status; responding to a
    pending one moves it to completed.
    """

    id: str
    message: str
    response: Optional[str] = None


class ResearchInteractionDict(BaseModel):
    """Snapshot of workflow state surfaced to the UI through the get_status query."""

    original_query: str | None = None

    # Status enum: pending | awaiting_user_input | researching | completed | ended
    status: str = "pending"

    # Generic elicitation contract. The agent emits ONE elicitation at a time.
    pending_elicitation: Elicitation | None = None
    completed_elicitations: list[Elicitation] = []

    research_completed: bool = False

    # Coarse phase the orchestrator agent is currently in: planning |
    # collecting | writing | None. Used by the UI to advance its progress
    # timeline against real backend state.
    current_activity: str | None = None

    # Topic-specific progress labels the agent committed during its first
    # elicit_user call. Shape: {planning: {title, detail}, collecting: {...},
    # writing: {...}}. None until the agent has issued the plan.
    progress_plan: dict[str, Dict[str, str]] | None = None
