"""Deterministic, no-model report assembly — the absolute last-resort floor.

When every model attempt (primary, cloud fallback, local) is exhausted or the
workflow's wall-clock budget fires, the workflow must still return a usable
report rather than fail. This module builds one purely from the research state
already captured in workflow memory: no network, no clock, no randomness, so it
is safe to run inside the Temporal workflow sandbox and always produces output
satisfying the ReportData / FinalizeReportRequest constraints (markdown
>= 300 chars, >= 2 follow-up questions, all fields non-empty).

It is intentionally simple. The point is a guaranteed floor, not a good report;
a good report comes from the model layers above it.
"""

from __future__ import annotations

from openai_agents.workflows.research_agents.research_models import (
    Elicitation,
    ReportData,
)
from openai_agents.workflows.research_agents.research_worker_agent import SearchSummary

# Appended when the assembled body is shorter than the ReportData/
# FinalizeReportRequest minimum. Long enough that at most two repetitions
# clear the 300-char floor even when every input is empty.
_FILLER = (
    " This summary was assembled automatically from the partial results "
    "collected before full synthesis completed, so that the research run "
    "always returns a usable result even when the writing models are "
    "unavailable."
)

_MIN_MARKDOWN_LEN = 300


def _topic(original_query: str) -> str:
    topic = (original_query or "").strip()
    return topic if topic else "this topic"


def _heading(original_query: str) -> str:
    topic = _topic(original_query)
    # Keep the H1 to a clean single line.
    if len(topic) > 120:
        topic = topic[:117].rstrip() + "..."
    return topic


def _clarifications_section(completed_elicitations: list[Elicitation]) -> str:
    answered = [
        e for e in completed_elicitations if e.response and e.response.strip()
    ]
    if not answered:
        return ""
    lines = ["## What we clarified", ""]
    for e in answered:
        response = (e.response or "").strip()
        lines.append(f"- {e.message.strip()} → {response}")
    lines.append("")
    return "\n".join(lines)


def _evidence_section(search_summaries: list[SearchSummary]) -> str:
    if not search_summaries:
        return ""
    lines = ["## Evidence gathered", ""]
    for s in search_summaries:
        lines.append(f"- **{s.query.strip()}**: {s.summary.strip()}")
    lines.append("")
    return "\n".join(lines)


def _internal_data_section(warehouse_summary: str | None) -> str:
    body = (warehouse_summary or "").strip()
    if not body:
        body = "No proprietary data warehouse context was available for this run."
    return "\n".join(["## Internal data", "", body, ""])


def _build_markdown(
    *,
    original_query: str,
    completed_elicitations: list[Elicitation],
    search_summaries: list[SearchSummary],
    warehouse_summary: str | None,
    image_description: str | None,
) -> str:
    parts = [
        f"# Research summary: {_heading(original_query)}",
        "",
        "_This report was assembled automatically from the research collected "
        "so far because the writing models were unavailable._",
        "",
    ]
    for section in (
        _clarifications_section(completed_elicitations),
        _evidence_section(search_summaries),
        _internal_data_section(warehouse_summary),
    ):
        if section:
            parts.append(section)

    if image_description and image_description.strip():
        parts.append("## Illustration")
        parts.append("")
        parts.append(image_description.strip())
        parts.append("")

    markdown = "\n".join(parts).rstrip()

    # Guarantee the minimum length by construction, regardless of how sparse
    # the captured inputs were.
    while len(markdown) < _MIN_MARKDOWN_LEN:
        markdown = (markdown + _FILLER).strip()
    return markdown


def _build_short_summary(
    original_query: str, search_summaries: list[SearchSummary]
) -> str:
    for s in search_summaries:
        summary = s.summary.strip()
        if summary:
            # First sentence, capped for a headline.
            first = summary.split(". ")[0].strip()
            if first:
                return first if first.endswith(".") else first + "."
    return (
        "Automated summary assembled from partial research results for "
        f"{_topic(original_query)}."
    )


def _build_follow_up_questions(
    original_query: str, search_summaries: list[SearchSummary]
) -> list[str]:
    topic = _topic(original_query)
    candidates: list[str] = []

    for s in search_summaries:
        q = s.query.strip()
        if q:
            candidates.append(f"What are the latest developments regarding {q}?")

    # Fixed backfill so we always reach the >= 2 floor even with no evidence.
    candidates.extend(
        [
            f"What are the most important recent developments related to {topic}?",
            f"What are the key risks or open questions around {topic}?",
            f"Which additional sources or data would deepen this analysis of {topic}?",
        ]
    )

    seen: set[str] = set()
    unique: list[str] = []
    for q in candidates:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    # ReportData / FinalizeReportRequest allow 2..5 follow-ups.
    return unique[:5]


def build_deterministic_report(
    *,
    original_query: str,
    completed_elicitations: list[Elicitation],
    search_summaries: list[SearchSummary],
    warehouse_summary: str | None,
    image_description: str | None = None,
) -> ReportData:
    """Assemble a schema-valid ReportData from captured research state.

    Never raises and never performs I/O. All inputs may be empty; the result
    still satisfies markdown_report min_length=300 and >= 2 follow-up questions.
    """
    markdown = _build_markdown(
        original_query=original_query,
        completed_elicitations=completed_elicitations,
        search_summaries=search_summaries,
        warehouse_summary=warehouse_summary,
        image_description=image_description,
    )
    return ReportData(
        short_summary=_build_short_summary(original_query, search_summaries),
        markdown_report=markdown,
        follow_up_questions=_build_follow_up_questions(
            original_query, search_summaries
        ),
    )
