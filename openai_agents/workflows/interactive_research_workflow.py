"""Interactive research workflow.

Agentic version: a single orchestrator agent runs inside the workflow and drives
every stage (clarifications, parallel research sub-agents, data warehouse, image
generation, report writing) via tool calls.

User input lands through a generic elicitation primitive: the agent calls
elicit_user once per question, the workflow publishes a single pending
elicitation, and the FE-BE contract delivers the response back via an update.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Literal

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError

# Read at module-import time so the workflow body stays deterministic.
ORCHESTRATOR_MAX_TURNS = int(os.getenv("ORCHESTRATOR_MAX_TURNS", "30"))

with workflow.unsafe.imports_passed_through():
    # Eagerly load pydantic's transitive deps so the workflow sandbox
    # captures them in its initial-import set; otherwise they're imported
    # on first model instantiation and the sandbox emits a warning.
    import annotated_types  # noqa: F401

    from agents import Runner
    from agents.exceptions import AgentsException
    from temporalio.contrib.openai_agents import AgentsWorkflowError

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
        ResearchResults,
        UserQueryInput,
    )
    from openai_agents.workflows.research_agents.research_worker_agent import (
        SearchSummary,
        new_research_worker_agent,
    )

# Failures meaning an agent could not produce its output: every model attempt
# (primary, cloud fallback, local) exhausted (ActivityError, possibly rewrapped
# by the plugin as AgentsWorkflowError), or the agent run itself failed
# (MaxTurnsExceeded, ModelBehaviorError, ...). Anything else is a bug and fails
# the workflow task so it can be fixed and replayed.
_AGENT_FAILURES = (ActivityError, AgentsException, AgentsWorkflowError)

REQUIRED_ELICITATIONS = 2


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
        self.research_completed: bool = False
        # Optional topic-specific progress labels from the agent; the UI
        # falls back to fixed labels when absent.
        self.progress_plan: ProgressPlan | None = None
        # Generic elicitation contract: at most one pending request at a time;
        # answered ones move into completed_elicitations.
        self.pending_elicitation: Elicitation | None = None
        self.completed_elicitations: list[Elicitation] = []
        self._elicitation_counter: int = 0
        # Outcomes recorded by the tools. These, not the model's final output,
        # establish that the work happened.
        self.research_started: bool = False
        self.research_findings: list[SearchSummary] = []
        self.research_failed_queries: list[str] = []
        self.warehouse_result: str | None = None
        self.image_outcome: Literal["generated", "unavailable"] | None = None
        self.research_image_path: str | None = None

    # ------------------------------------------------------------------
    # Helpers used by orchestrator tools (called from agent context)
    # ------------------------------------------------------------------

    async def tool_elicit_user(
        self,
        message: str,
        progress_plan: ProgressPlan | None,
    ) -> str:
        """Workflow-state tool: publish ONE elicitation and wait for the answer."""
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
        if len(self.completed_elicitations) >= REQUIRED_ELICITATIONS:
            raise ApplicationError(
                "elicit_user has already been called twice; the contract is "
                "exactly two elicitations. Proceed to run_parallel_research."
            )
        if progress_plan is not None:
            self.progress_plan = progress_plan

        el_id = f"el_{self._elicitation_counter}"
        self._elicitation_counter += 1
        self.pending_elicitation = Elicitation(id=el_id, message=message)

        await workflow.wait_condition(
            lambda: self.pending_elicitation is not None
            and self.pending_elicitation.response is not None
        )

        answered = self.pending_elicitation
        assert answered is not None and answered.response is not None
        self.completed_elicitations.append(answered)
        self.pending_elicitation = None
        return answered.response

    async def tool_run_parallel_research(
        self, subqueries: list[str]
    ) -> ResearchResults:
        """Workflow-state tool: fan out research worker sub-agents in parallel.

        A worker whose model attempts are exhausted is reported back as a
        failed query; the surviving findings still go to the agent.
        """
        if len(self.completed_elicitations) < REQUIRED_ELICITATIONS:
            raise ApplicationError(
                "Must call elicit_user twice before run_parallel_research; "
                f"only {len(self.completed_elicitations)} elicitation(s) completed so far"
            )
        self.research_started = True
        worker = new_research_worker_agent()

        async def _run_one(q: str) -> SearchSummary | None:
            try:
                result = await Runner.run(worker, f"Subquery: {q}")
            except _AGENT_FAILURES:
                workflow.logger.exception(f"Research worker failed for {q!r}")
                return None
            return result.final_output_as(SearchSummary)

        outcomes = await asyncio.gather(*[_run_one(q) for q in subqueries])
        results = ResearchResults(
            findings=[o for o in outcomes if o is not None],
            failed_queries=[q for q, o in zip(subqueries, outcomes) if o is None],
        )
        self.research_findings.extend(results.findings)
        self.research_failed_queries.extend(results.failed_queries)
        return results

    def record_warehouse(self, summary: str) -> None:
        """Workflow-state tool helper: record the warehouse lookup result."""
        self.warehouse_result = summary

    def set_image(self, image_path: str) -> None:
        """Workflow-state tool helper: record the generated research image.

        This path, returned by the image activity, is the only source of the
        image shown in the UI; the model never supplies a filename.
        """
        self.image_outcome = "generated"
        self.research_image_path = image_path

    def set_image_unavailable(self) -> None:
        """Workflow-state tool helper: image attempts were exhausted."""
        self.image_outcome = "unavailable"
        self.research_image_path = None

    def missing_outcomes(self) -> list[str]:
        """Required work with no recorded outcome; empty when the run is complete."""
        missing = []
        if len(self.completed_elicitations) < REQUIRED_ELICITATIONS:
            missing.append("two answered clarifications")
        if not self.research_findings:
            missing.append("research findings")
        if self.warehouse_result is None:
            missing.append("a data warehouse result")
        if self.image_outcome is None:
            missing.append("an image outcome")
        return missing

    def current_activity(self) -> str:
        """Coarse phase derived from recorded outcomes, for the UI."""
        if not self.research_started:
            return "planning"
        if (
            self.research_findings
            and self.warehouse_result is not None
            and self.image_outcome is not None
        ):
            return "writing"
        return "collecting"

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    @workflow.run
    async def run(self, input: UserQueryInput) -> InteractiveResearchResult:
        """Run the orchestrator agent on the user's query.

        Robustness comes from time and fallbacks, never from faking success:
        each model call gets generous timeouts and falls back from the primary
        cloud model to a faster cloud model to a local model across Temporal
        retries. If every layer fails, or the agent finishes without doing the
        required work, the workflow fails visibly.
        """
        workflow.logger.info(f"Starting research for query: {input.query!r}")
        self.original_query = input.query
        try:
            run_result = await Runner.run(
                new_orchestrator_agent(),
                input.query,
                context=self,
                max_turns=ORCHESTRATOR_MAX_TURNS,
            )
        except _AGENT_FAILURES as e:
            # Non-retryable: retrying the workflow task would replay the same
            # exhausted model calls. Fail the run so the UI reports it.
            raise ApplicationError(
                f"Research could not be completed: {e}", non_retryable=True
            ) from e

        missing = self.missing_outcomes()
        if missing:
            raise ApplicationError(
                f"Agent finished without {', '.join(missing)}", non_retryable=True
            )

        final = run_result.final_output_as(FinalizeReportRequest)
        self.report_data = ReportData(
            short_summary=final.short_summary,
            markdown_report=final.markdown_report,
            follow_up_questions=final.follow_up_questions,
        )
        self.research_completed = True
        return InteractiveResearchResult(
            short_summary=final.short_summary,
            markdown_report=final.markdown_report,
            follow_up_questions=final.follow_up_questions,
            image_file_path=self.research_image_path,
        )

    # ------------------------------------------------------------------
    # Queries / updates
    # ------------------------------------------------------------------

    @workflow.query
    def get_status(self) -> ResearchInteractionDict:
        if self.research_completed:
            status = "completed"
        elif self.pending_elicitation is not None:
            status = "awaiting_user_input"
        else:
            status = "researching"

        return ResearchInteractionDict(
            original_query=self.original_query,
            status=status,
            pending_elicitation=self.pending_elicitation,
            completed_elicitations=list(self.completed_elicitations),
            research_completed=self.research_completed,
            current_activity=self.current_activity(),
            progress_plan=(
                self.progress_plan.model_dump() if self.progress_plan else None
            ),
        )

    @workflow.update
    async def submit_elicitation_response(
        self, input: ElicitationResponseInput
    ) -> ResearchInteractionDict:
        """Deliver a response to the currently pending elicitation."""
        assert self.pending_elicitation is not None
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
