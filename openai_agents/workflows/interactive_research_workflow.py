"""Interactive research workflow.

Agentic version: a single orchestrator agent runs inside the workflow and drives
every stage (clarifications, parallel research sub-agents, data warehouse, image
generation, report writing, finalization) via tool calls. Workflow state is
mutated through tools so the UI's polling contract (status field, clarification
question index, completed report) is preserved unchanged.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.exceptions import ApplicationError

# Read at module-import time so the workflow body stays deterministic.
ORCHESTRATOR_MAX_TURNS = int(os.getenv("ORCHESTRATOR_MAX_TURNS", "30"))

with workflow.unsafe.imports_passed_through():
    from agents import Runner

    from openai_agents.workflows.research_agents.orchestrator_agent import (
        FinalizeReportRequest,
        new_orchestrator_agent,
        prepare_first_search_branch,
    )
    from openai_agents.workflows.research_agents.research_models import (
        ClarificationInput,
        ResearchInteractionDict,
        SingleClarificationInput,
        UserQueryInput,
    )
    from openai_agents.workflows.research_agents.research_worker_agent import (
        SearchSummary,
        new_research_worker_agent,
    )
    from openai_agents.workflows.research_agents.writer_agent import ReportData


@dataclass
class ProcessClarificationInput:
    """Input for clarification processing activity."""

    answer: str
    current_question_index: int
    current_question: str | None
    total_questions: int


@dataclass
class ProcessClarificationResult:
    """Result from clarification processing activity."""

    question_key: str
    answer: str
    new_index: int


@activity.defn
async def process_clarification(
    input: ProcessClarificationInput,
) -> ProcessClarificationResult:
    """Process a single clarification answer."""
    activity.logger.info(
        f"Processing clarification answer {input.current_question_index + 1}/{input.total_questions}: "
        f"'{input.answer}' for question: '{input.current_question}'"
    )

    # Simulate cloud provider outages for the second-to-last question
    demo_retry_failures = int(os.getenv("DEMO_CLARIFICATION_RETRY_FAILURES", "0") or "0")
    if demo_retry_failures and (input.current_question_index + 2) == input.total_questions:
        attempt = activity.info().attempt
        if attempt <= demo_retry_failures:
            await asyncio.sleep(10)
            raise ApplicationError("Simulated failure -- try again soon :)")

    question_key = f"question_{input.current_question_index}"
    return ProcessClarificationResult(
        question_key=question_key,
        answer=input.answer,
        new_index=input.current_question_index + 1,
    )


@dataclass
class InteractiveResearchResult:
    """Result returned to the workflow caller."""

    short_summary: str
    markdown_report: str
    follow_up_questions: list[str]
    image_file_path: str | None = None


@workflow.defn
class InteractiveResearchWorkflow:
    def __init__(self) -> None:
        # State observed by the get_status query and the UI.
        self.original_query: str | None = None
        self.clarification_questions: list[str] = []
        self.clarification_responses: dict[str, str] = {}
        self.current_question_index: int = 0
        self.report_data: ReportData | None = None
        self.research_image_path: str | None = None
        self.research_image_description: str | None = None
        self.research_completed: bool = False
        self.workflow_ended: bool = False
        self.research_initialized: bool = False
        # Coarse phase the orchestrator is currently in. Mutated by tool bodies;
        # surfaced to the UI through get_status so the progress timeline tracks
        # real backend state.
        self.current_activity: str | None = None

    # ------------------------------------------------------------------
    # Helpers used by orchestrator tools (called from agent context)
    # ------------------------------------------------------------------

    async def tool_ask_user_clarifications(
        self, questions: list[str]
    ) -> dict[str, str]:
        """Workflow-state tool: publish clarifying questions and wait for answers.

        Drives the existing UI flow: as soon as `clarification_questions` is non-empty
        and `clarification_responses` is empty, the get_status query reports
        "awaiting_clarifications" and the UI shows the first question. Each answer
        comes back through the existing provide_single_clarification update.
        """
        self.clarification_questions = list(questions)
        self.clarification_responses = {}
        self.current_question_index = 0

        await workflow.wait_condition(
            lambda: self.workflow_ended
            or len(self.clarification_responses) >= len(self.clarification_questions)
        )

        if self.workflow_ended:
            raise ApplicationError("Workflow ended by user while awaiting clarifications")

        return {
            self.clarification_questions[i]: self.clarification_responses[
                f"question_{i}"
            ]
            for i in range(len(self.clarification_questions))
        }

    async def tool_run_parallel_research(
        self, subqueries: list[str]
    ) -> list[SearchSummary]:
        """Workflow-state tool: fan out research worker sub-agents in parallel.

        Runs the demo's prepare_web_search_branch activity for branch 0 first
        (preserves the SIGKILL failure-recovery beat), then dispatches one
        research_worker_agent per subquery via asyncio.gather.
        """
        if not subqueries:
            return []
        self.current_activity = "collecting"
        await prepare_first_search_branch(subqueries[0], len(subqueries))

        worker = new_research_worker_agent()

        async def _run_one(q: str) -> SearchSummary | None:
            try:
                input_str = f"Subquery: {q}"
                result = await Runner.run(worker, input_str)
                return result.final_output_as(SearchSummary)
            except Exception as e:
                workflow.logger.warning(f"Research worker failed for {q!r}: {e}")
                return None

        results = await asyncio.gather(
            *[_run_one(sq) for sq in subqueries], return_exceptions=False
        )
        return [r for r in results if r is not None]

    def set_image(self, image_path: str, description: str) -> None:
        """Workflow-state tool helper: record the generated research image."""
        self.research_image_path = image_path
        self.research_image_description = description

    def complete_research(self, report: ReportData, image_path: str) -> None:
        """Workflow-state tool helper: mark the workflow complete with the report."""
        self.current_activity = "writing"
        self.report_data = report
        # Trust whatever the agent passed for the image_path. set_image was
        # already called by generate_research_image, but the agent may have
        # carried a different path through finalize_report (e.g. via cache).
        self.research_image_path = image_path
        self.research_completed = True

    # ------------------------------------------------------------------
    # Result construction and main run loop
    # ------------------------------------------------------------------

    def _build_result(
        self,
        summary: str,
        report: str,
        questions: list[str] | None = None,
        image_path: str | None = None,
    ) -> InteractiveResearchResult:
        return InteractiveResearchResult(
            short_summary=summary,
            markdown_report=report,
            follow_up_questions=questions or [],
            image_file_path=image_path,
        )

    @workflow.run
    async def run(
        self, initial_query: str | None = None, use_clarifications: bool = True
    ) -> InteractiveResearchResult:
        """Long-running interactive workflow driven by the orchestrator agent."""
        # Wait for the start_research update (or an end signal).
        await workflow.wait_condition(
            lambda: self.workflow_ended or self.research_initialized
        )

        if self.workflow_ended:
            return self._build_result(
                "Research ended by user", "Research workflow ended by user"
            )

        assert self.original_query is not None, (
            "research_initialized was set without an original_query"
        )

        orchestrator = new_orchestrator_agent()
        self.current_activity = "planning"
        try:
            run_result = await Runner.run(
                orchestrator,
                self.original_query,
                context=self,
                max_turns=ORCHESTRATOR_MAX_TURNS,
            )
        except Exception as e:
            workflow.logger.exception(f"Orchestrator agent failed: {e}")
            raise

        if self.workflow_ended and not self.research_completed:
            return self._build_result(
                "Research ended by user", "Research workflow ended by user"
            )

        # The orchestrator's terminal action is emitting a structured
        # FinalizeReportRequest as its final response. Validate and apply.
        final = run_result.final_output_as(FinalizeReportRequest)
        report = ReportData(
            short_summary=final.short_summary,
            markdown_report=final.markdown_report,
            follow_up_questions=final.follow_up_questions,
        )
        self.complete_research(report, final.image_path)

        return self._build_result(
            report.short_summary,
            report.markdown_report,
            report.follow_up_questions,
            self.research_image_path,
        )

    # ------------------------------------------------------------------
    # Queries / updates / signals (UI contract - shape preserved)
    # ------------------------------------------------------------------

    def _get_current_question(self) -> str | None:
        if self.current_question_index >= len(self.clarification_questions):
            return None
        return self.clarification_questions[self.current_question_index]

    def _has_more_questions(self) -> bool:
        return self.current_question_index < len(self.clarification_questions)

    @workflow.query
    def get_status(self) -> ResearchInteractionDict:
        current_question = self._get_current_question()

        if self.workflow_ended:
            status = "ended"
        elif self.research_completed:
            status = "completed"
        elif self.clarification_questions and len(self.clarification_responses) < len(
            self.clarification_questions
        ):
            if len(self.clarification_responses) == 0:
                status = "awaiting_clarifications"
            else:
                status = "collecting_answers"
        elif self.original_query and not self.research_completed:
            status = "researching"
        else:
            status = "pending"

        return ResearchInteractionDict(
            original_query=self.original_query,
            clarification_questions=self.clarification_questions,
            clarification_responses=self.clarification_responses,
            current_question_index=self.current_question_index,
            current_question=current_question,
            status=status,
            research_completed=self.research_completed,
            current_activity=self.current_activity,
        )

    @workflow.update
    async def start_research(self, input: UserQueryInput) -> ResearchInteractionDict:
        """Start a new research session.

        Just records the query and unblocks the main run loop. The orchestrator
        agent will drive everything from there - including deciding to ask
        clarifying questions on its first turn.
        """
        workflow.logger.info(f"Starting research for query: '{input.query}'")
        self.original_query = input.query
        self.research_initialized = True
        return self.get_status()

    @workflow.update
    async def provide_single_clarification(
        self, input: SingleClarificationInput
    ) -> ResearchInteractionDict:
        """Provide a single clarification response."""
        current_question = self._get_current_question()

        result = await workflow.execute_activity(
            process_clarification,
            ProcessClarificationInput(
                answer=input.answer,
                current_question_index=self.current_question_index,
                current_question=current_question,
                total_questions=len(self.clarification_questions),
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

        self.clarification_responses[result.question_key] = result.answer
        self.current_question_index = result.new_index
        return self.get_status()

    @workflow.update
    async def provide_clarifications(
        self, input: ClarificationInput
    ) -> ResearchInteractionDict:
        """Provide all clarification responses at once (legacy compatibility)."""
        workflow.logger.info(
            f"Received {len(input.responses)} clarification responses: {input.responses}"
        )
        self.clarification_responses = input.responses
        self.current_question_index = len(self.clarification_questions)
        return self.get_status()

    @provide_single_clarification.validator
    def validate_single_clarification(self, input: SingleClarificationInput) -> None:
        if not input.answer.strip():
            raise ValueError("Answer cannot be empty")
        if not self.original_query:
            raise ValueError("No active research interaction")
        if not self.clarification_questions or len(self.clarification_responses) >= len(
            self.clarification_questions
        ):
            raise ValueError("Not collecting clarifications")

    @provide_clarifications.validator
    def validate_provide_clarifications(self, input: ClarificationInput) -> None:
        if not input.responses:
            raise ValueError("Clarification responses cannot be empty")
        if not self.original_query:
            raise ValueError("No active research interaction")
        if not self.clarification_questions:
            raise ValueError("Not awaiting clarifications")

    @workflow.signal
    async def end_workflow_signal(self) -> None:
        self.workflow_ended = True
