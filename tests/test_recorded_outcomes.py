"""Unit tests for the outcomes the workflow's tools record.

Recorded outcomes, not the model's final output, decide whether a run is
complete, which phase the UI shows, and which image the report uses.
"""

from __future__ import annotations

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.orchestrator_agent import (
    FinalizeReportRequest,
)
from openai_agents.workflows.research_agents.research_models import Elicitation
from openai_agents.workflows.research_agents.research_worker_agent import (
    SearchSummary,
)


def _answered(wf: InteractiveResearchWorkflow) -> None:
    wf.completed_elicitations = [
        Elicitation(id=f"el_{i}", message="Q?", response="A") for i in range(2)
    ]


def _researched(wf: InteractiveResearchWorkflow) -> None:
    wf.research_started = True
    wf.research_findings = [SearchSummary(query="q", summary="s")]


def test_fresh_run_is_planning_and_missing_everything():
    wf = InteractiveResearchWorkflow()

    assert wf.current_activity() == "planning"
    assert wf.missing_outcomes() == [
        "two answered clarifications",
        "research findings",
        "a data warehouse result",
        "an image outcome",
    ]


def test_phase_is_collecting_until_all_evidence_is_recorded():
    wf = InteractiveResearchWorkflow()
    _answered(wf)
    _researched(wf)
    wf.record_warehouse("Simulated data warehouse: none")

    assert wf.current_activity() == "collecting"

    wf.set_image("temp_images/ground_truth.jpeg")

    assert wf.current_activity() == "writing"
    assert wf.missing_outcomes() == []


def test_unavailable_image_counts_as_an_outcome_without_a_path():
    wf = InteractiveResearchWorkflow()
    _answered(wf)
    _researched(wf)
    wf.record_warehouse("Simulated data warehouse: none")

    wf.set_image_unavailable()

    assert wf.missing_outcomes() == []
    assert wf.research_image_path is None


def test_failed_research_alone_is_not_findings():
    wf = InteractiveResearchWorkflow()
    _answered(wf)
    wf.research_started = True
    wf.research_failed_queries = ["q"]

    assert "research findings" in wf.missing_outcomes()


def test_finalize_report_request_carries_only_the_report():
    """The model never echoes evidence or a filename it doesn't own."""
    assert set(FinalizeReportRequest.model_fields) == {
        "short_summary",
        "markdown_report",
        "follow_up_questions",
    }
