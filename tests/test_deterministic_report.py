"""Unit tests for the deterministic report floor.

Every case must produce output that satisfies both ReportData and the stricter
FinalizeReportRequest constraints (markdown >= 300 chars, 2..5 follow-ups, all
fields non-empty), proving the floor can never dead-end the workflow.
"""

from __future__ import annotations

import pytest

from openai_agents.workflows.research_agents.deterministic_report import (
    build_deterministic_report,
)
from openai_agents.workflows.research_agents.orchestrator_agent import (
    FinalizeReportRequest,
)
from openai_agents.workflows.research_agents.research_models import (
    Elicitation,
    ReportData,
)
from openai_agents.workflows.research_agents.research_worker_agent import SearchSummary


def _assert_valid(report: ReportData) -> None:
    # ReportData itself has no length constraints, but the stricter
    # FinalizeReportRequest does; if we can build one from the same user-visible
    # fields, the floor output is guaranteed acceptable everywhere.
    assert isinstance(report, ReportData)
    assert len(report.markdown_report) >= 300
    assert 2 <= len(report.follow_up_questions) <= 5
    assert report.short_summary.strip()

    FinalizeReportRequest(
        short_summary=report.short_summary,
        markdown_report=report.markdown_report,
        follow_up_questions=report.follow_up_questions,
        image_path="research_image.png",
        warehouse_summary="warehouse context",
        search_summaries=[
            SearchSummary(query=f"q{i}", summary=f"s{i}") for i in range(3)
        ],
    )


def test_empty_inputs_still_valid():
    report = build_deterministic_report(
        original_query="",
        completed_elicitations=[],
        search_summaries=[],
        warehouse_summary=None,
        image_description=None,
    )
    _assert_valid(report)


def test_zero_summaries_but_query_present():
    report = build_deterministic_report(
        original_query="impact of AI on drug discovery",
        completed_elicitations=[],
        search_summaries=[],
        warehouse_summary=None,
        image_description=None,
    )
    _assert_valid(report)
    assert "drug discovery" in report.markdown_report


def test_partial_inputs_appear_in_report():
    report = build_deterministic_report(
        original_query="renewable energy storage",
        completed_elicitations=[
            Elicitation(id="1", message="What region?", response="Europe"),
            Elicitation(id="2", message="What timeframe?", response="2030"),
        ],
        search_summaries=[
            SearchSummary(query="grid-scale batteries", summary="Costs fell 40%."),
        ],
        warehouse_summary="Internal: 3 pilot sites online.",
        image_description="A bar chart of storage capacity by year.",
    )
    _assert_valid(report)
    md = report.markdown_report
    assert "Europe" in md
    assert "grid-scale batteries" in md
    assert "Costs fell 40%." in md
    assert "3 pilot sites online" in md
    assert "bar chart" in md


def test_full_inputs_valid():
    report = build_deterministic_report(
        original_query="state of quantum computing",
        completed_elicitations=[
            Elicitation(id="1", message="Hardware or software?", response="Hardware"),
        ],
        search_summaries=[
            SearchSummary(query=f"subquery {i}", summary=f"Finding number {i} here.")
            for i in range(5)
        ],
        warehouse_summary="Internal telemetry across 12 QPUs.",
        image_description="A qubit coherence timeline.",
    )
    _assert_valid(report)
    # short_summary is derived from the first non-empty evidence summary.
    assert report.short_summary.startswith("Finding number 0")


def test_follow_ups_bounded_and_deduped():
    # Many summaries would produce many candidate questions; must cap at 5.
    report = build_deterministic_report(
        original_query="topic",
        completed_elicitations=[],
        search_summaries=[
            SearchSummary(query=f"q{i}", summary=f"s{i}") for i in range(10)
        ],
        warehouse_summary=None,
        image_description=None,
    )
    _assert_valid(report)
    assert len(report.follow_up_questions) == len(set(report.follow_up_questions))


def test_never_raises_on_whitespace_only():
    report = build_deterministic_report(
        original_query="   ",
        completed_elicitations=[
            Elicitation(id="1", message="q?", response="   "),  # blank response
        ],
        search_summaries=[],
        warehouse_summary="   ",
        image_description="   ",
    )
    _assert_valid(report)


@pytest.mark.parametrize("n_summaries", [0, 1, 2, 3, 4, 5, 6])
def test_various_summary_counts(n_summaries):
    report = build_deterministic_report(
        original_query="scaling laws",
        completed_elicitations=[],
        search_summaries=[
            SearchSummary(query=f"q{i}", summary=f"summary {i}")
            for i in range(n_summaries)
        ],
        warehouse_summary="ctx",
        image_description=None,
    )
    _assert_valid(report)
