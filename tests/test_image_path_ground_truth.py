"""Regression tests for the image-path ground-truth contract.

The generated image path has a single authoritative source: set_image, called
by generate_research_image with the path the image activity actually produced.
Finalization must surface that path and must never let a model-echoed filename
overwrite it (which used to surface a stale or broken image). FinalizeReportRequest
therefore has no image_path field at all.
"""

from __future__ import annotations

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.orchestrator_agent import (
    FinalizeReportRequest,
)
from openai_agents.workflows.research_agents.research_models import ReportData


def _report() -> ReportData:
    return ReportData(
        short_summary="A summary.",
        markdown_report="# Report\n\n" + ("Detail. " * 40),
        follow_up_questions=["One?", "Two?"],
    )


def test_complete_research_preserves_ground_truth_image_path():
    """complete_research must not disturb the path recorded by set_image."""
    wf = InteractiveResearchWorkflow()
    wf.set_image("temp_images/ground_truth.jpeg", "atmospheric prompt")

    wf.complete_research(_report())

    assert wf.research_image_path == "temp_images/ground_truth.jpeg"
    assert wf.research_completed is True


def test_complete_research_leaves_path_unset_when_no_image_generated():
    """With no image generated, the path stays None (UI renders no image)."""
    wf = InteractiveResearchWorkflow()

    wf.complete_research(_report())

    assert wf.research_image_path is None


def test_finalize_report_request_has_no_image_path_field():
    """The model is never asked to echo a filename it doesn't own."""
    assert "image_path" not in FinalizeReportRequest.model_fields
