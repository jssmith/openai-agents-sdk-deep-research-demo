"""Interactive research workflow.

Agentic version: a single orchestrator agent runs inside the workflow and drives
every stage (clarifications, parallel research sub-agents, data warehouse, image
generation, report writing, finalization) via tool calls.

User input lands through a generic elicitation primitive: the agent calls
elicit_user once per question, the workflow publishes a single pending
elicitation, and the FE-BE contract delivers the response back via an update.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from temporalio import workflow
from temporalio.exceptions import ApplicationError

# Read at module-import time so the workflow body stays deterministic.
ORCHESTRATOR_MAX_TURNS = int(os.getenv("ORCHESTRATOR_MAX_TURNS", "30"))

with workflow.unsafe.imports_passed_through():
    # Eagerly load pydantic's transitive deps so the workflow sandbox
    # captures them in its initial-import set; otherwise they're imported
    # on first model instantiation and the sandbox emits a warning.
    import annotated_types  # noqa: F401

    from agents import Runner

    from openai_agents.workflows.research_agents.orchestrator_agent import (
        FinalizeReportRequest,
        ProgressPlan,
        new_orchestrator_agent,
    )
    from openai_agents.workflows.research_agents.research_models import (
        Elicitation,
        ElicitationResponseInput,
        ReportData,
        ResearchInteractionDict,
        UserQueryInput,
    )
    from openai_agents.workflows.research_agents.research_worker_agent import (
        SearchSummary,
        new_research_worker_agent,
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
        self.original_query: str | None = None
        self.report_data: ReportData | None = None
        self.research_image_path: str | None = None
        self.research_image_description: str | None = None
        self.research_completed: bool = False
        self.workflow_ended: bool = False
        self.research_initialized: bool = False
        # Coarse phase the orchestrator is currently in. planning | collecting
        # | writing | None. Surfaced to the UI via get_status.
        self.current_activity: str | None = None
        # Topic-specific progress labels the agent commits on its first
        # elicit_user call. Falls back to hardcoded UI text if absent.
        self.progress_plan: ProgressPlan | None = None
        # Generic elicitation contract: at most one pending request at a time;
        # answered ones move into completed_elicitations.
        self.pending_elicitation: Elicitation | None = None
        self.completed_elicitations: list[Elicitation] = []
        self._elicitation_counter: int = 0

    # ------------------------------------------------------------------
    # Helpers used by orchestrator tools (called from agent context)
    # ------------------------------------------------------------------

    async def tool_elicit_user(
        self,
        message: str,
        progress_plan: ProgressPlan | None,
    ) -> str:
        """Workflow-state tool: publish ONE elicitation and wait for the answer.

        First call must include progress_plan (the agent's commitment to the
        UI's progress timeline). Subsequent calls leave it None and reuse the
        plan that was already stored.
        """
        # Guard against a model that emits multiple elicit_user tool calls in
        # one turn (the runtime supports parallel tool calls). Two concurrent
        # invocations would race on self.pending_elicitation and lose a
        # question; reject the second one explicitly.
        if self.pending_elicitation is not None:
            raise ApplicationError(
                "An elicitation is already pending; wait for it to be answered "
                "before issuing another. Issue elicit_user calls sequentially, "
                "not in parallel."
            )
        # Upper bound: the contract is exactly two elicitations, mirroring the
        # lower bound enforced in tool_run_parallel_research.
        if len(self.completed_elicitations) >= 2:
            raise ApplicationError(
                "elicit_user has already been called twice; the contract is "
                "exactly two elicitations. Proceed to run_parallel_research."
            )

        if self.progress_plan is None:
            if progress_plan is None:
                raise ApplicationError(
                    "progress_plan is required on the first elicit_user call"
                )
            self.progress_plan = progress_plan

        el_id = f"el_{self._elicitation_counter}"
        self._elicitation_counter += 1
        self.pending_elicitation = Elicitation(id=el_id, message=message)

        await workflow.wait_condition(
            lambda: self.workflow_ended
            or (
                self.pending_elicitation is not None
                and self.pending_elicitation.response is not None
            )
        )

        if self.workflow_ended:
            raise ApplicationError("Workflow ended by user while awaiting elicitation")

        answered = self.pending_elicitation
        assert answered is not None and answered.response is not None
        self.completed_elicitations.append(answered)
        self.pending_elicitation = None
        return answered.response

    async def tool_run_parallel_research(
        self, subqueries: list[str]
    ) -> list[SearchSummary]:
        """Workflow-state tool: fan out research worker sub-agents in parallel."""
        if len(self.completed_elicitations) < 2:
            raise ApplicationError(
                "Must call elicit_user twice before run_parallel_research; "
                f"only {len(self.completed_elicitations)} elicitation(s) completed so far"
            )
        if not subqueries:
            return []
        self.current_activity = "collecting"

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
    async def run(self) -> InteractiveResearchResult:
        """Long-running interactive workflow driven by the orchestrator agent.

        The workflow is started with no input and immediately blocks waiting
        for the start_research update; the FastAPI backend sends that update
        with the user's query right after start_workflow.
        """
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
    # Queries / updates / signals
    # ------------------------------------------------------------------

    @workflow.query
    def get_status(self) -> ResearchInteractionDict:
        if self.workflow_ended:
            status = "ended"
        elif self.research_completed:
            status = "completed"
        elif self.pending_elicitation is not None:
            status = "awaiting_user_input"
        elif self.original_query and not self.research_completed:
            status = "researching"
        else:
            status = "pending"

        plan_dict = (
            self.progress_plan.model_dump() if self.progress_plan is not None else None
        )
        return ResearchInteractionDict(
            original_query=self.original_query,
            status=status,
            pending_elicitation=self.pending_elicitation,
            completed_elicitations=list(self.completed_elicitations),
            research_completed=self.research_completed,
            current_activity=self.current_activity,
            progress_plan=plan_dict,
        )

    @workflow.update
    async def start_research(self, input: UserQueryInput) -> ResearchInteractionDict:
        """Start a new research session. Records the query and unblocks the run loop."""
        workflow.logger.info(f"Starting research for query: '{input.query}'")
        self.original_query = input.query
        self.research_initialized = True
        return self.get_status()

    @workflow.update
    async def submit_elicitation_response(
        self, input: ElicitationResponseInput
    ) -> ResearchInteractionDict:
        """Deliver a response to the currently pending elicitation."""
        if self.pending_elicitation is None:
            raise ValueError("No elicitation is currently pending")
        if self.pending_elicitation.id != input.elicitation_id:
            raise ValueError(
                f"Pending elicitation id is {self.pending_elicitation.id!r}, "
                f"got response for {input.elicitation_id!r}"
            )
        if not input.response.strip():
            raise ValueError("Response cannot be empty")
        # Setting .response unblocks tool_elicit_user via wait_condition.
        self.pending_elicitation.response = input.response
        return self.get_status()

    @submit_elicitation_response.validator
    def validate_submit_elicitation_response(
        self, input: ElicitationResponseInput
    ) -> None:
        if not input.response.strip():
            raise ValueError("Response cannot be empty")
        if self.pending_elicitation is None:
            raise ValueError("No elicitation is currently pending")
        if self.pending_elicitation.id != input.elicitation_id:
            raise ValueError(
                f"Stale elicitation id: pending is {self.pending_elicitation.id!r}"
            )

    @workflow.signal
    async def end_workflow_signal(self) -> None:
        self.workflow_ended = True
