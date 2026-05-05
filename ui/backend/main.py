"""
FastAPI Backend for Temporal Research UI
=========================================
Production-ready backend connecting to Temporal workflows.

Environment Variables:
- TEMPORAL_PROFILE: name of the env config profile to use (optional).
- TEMPORAL_ADDRESS: Temporal server address (default: 127.0.0.1:7233)
- TEMPORAL_NAMESPACE: Temporal namespace (default: default)
- TEMPORAL_API_KEY: API key for Temporal Cloud (disabled by default)
- TEMPORAL_TASK_QUEUE: Task queue name (default: research-queue)
"""

import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.contrib.openai_agents._temporal_openai_agents import (
    OpenAIPayloadConverter,
)
from temporalio.converter import DataConverter
from temporalio.envconfig import ClientConfig

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchResult,
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import (
    ElicitationResponseInput,
    UserQueryInput,
)

# Load environment variables
load_dotenv()

TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "research-queue")


# ============================================
# FastAPI App Setup
# ============================================
app = FastAPI(
    title="Temporal Research API",
    description="Backend API for the Temporal Research Demo UI",
    version="1.0.0",
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# Temporal Client Setup
# ============================================


temporal_client: Optional[Client] = None

temporal_config = ClientConfig.load_client_connect_config()
temporal_config.setdefault("target_host", "localhost:7233")
temporal_config.setdefault("namespace", "default")


async def get_temporal_client() -> Client:
    global temporal_client
    if temporal_client:
        return temporal_client

    print(
        f"Connecting to Temporal at {temporal_config.get('target_host')} in namespace {temporal_config.get('namespace')}"
    )

    temporal_client = await Client.connect(
        **temporal_config,
        data_converter=DataConverter(payload_converter_class=OpenAIPayloadConverter),
    )
    return temporal_client


# ============================================
# Request/Response Models
# ============================================
class StartResearchRequest(BaseModel):
    query: str


class AnswerRequest(BaseModel):
    answer: str


class WorkflowStatusResponse(BaseModel):
    workflow_id: str
    status: str  # "pending", "awaiting_clarifications", "researching", "complete"
    original_query: Optional[str] = None
    current_question: Optional[str] = None
    current_question_index: int = 0
    total_questions: int = 0
    clarification_responses: Dict[str, str] = {}


class ResearchResultResponse(BaseModel):
    workflow_id: str
    markdown_report: str
    short_summary: str
    follow_up_questions: List[str]


# ============================================
# Static File Serving
# ============================================
@app.get("/")
async def serve_index():
    """Serve the main chat interface"""
    index_path = Path(__file__).parent.parent / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    raise HTTPException(status_code=404, detail="Index page not found")


@app.get("/success")
async def serve_success():
    """Serve the success/results page"""
    success_path = Path(__file__).parent.parent / "success.html"
    if success_path.exists():
        return HTMLResponse(content=success_path.read_text())
    raise HTTPException(status_code=404, detail="Success page not found")


# Serve static assets (JS, CSS, fonts, images)
static_path = Path(__file__).parent.parent
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

images_path = Path(__file__).resolve().parent.parent.parent / "temp_images"
images_path.mkdir(exist_ok=True)
app.mount("/temp_images", StaticFiles(directory=str(images_path)), name="images")

# ============================================
# API Endpoints
# ============================================


@app.post("/api/start-research")
async def start_research(request: StartResearchRequest):
    """
    Start a new research workflow.

    Returns:
        workflow_id: Unique identifier for tracking the workflow
        status: Initial status ("started")
    """
    client = await get_temporal_client()
    workflow_id = f"interactive-research-{uuid.uuid4().hex[:8]}"

    handle = await client.start_workflow(
        InteractiveResearchWorkflow.run,
        args=[None, False],
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
    )

    status = await handle.execute_update(
        InteractiveResearchWorkflow.start_research,
        UserQueryInput(query=request.query.strip()),
    )

    return {
        "workflow_id": workflow_id,
        "status": "started",
    }


@app.get("/api/status/{workflow_id}")
async def get_status(workflow_id: str):
    """
    Get current workflow status.

    Returns:
        workflow_id, status, original_query, current_activity, progress_plan,
        pending_elicitation (or null), completed_elicitations.
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    status = await handle.query(InteractiveResearchWorkflow.get_status)

    return {
        "workflow_id": workflow_id,
        "status": status.status,
        "original_query": status.original_query,
        "research_completed": status.research_completed,
        "current_activity": status.current_activity,
        "progress_plan": status.progress_plan,
        "pending_elicitation": (
            status.pending_elicitation.model_dump()
            if status.pending_elicitation is not None
            else None
        ),
        "completed_elicitations": [e.model_dump() for e in status.completed_elicitations],
    }


@app.post("/api/elicitation/{workflow_id}/{elicitation_id}")
async def submit_elicitation_response(
    workflow_id: str,
    elicitation_id: str,
    request: AnswerRequest,
):
    """Deliver a response to the workflow's pending elicitation."""
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    await handle.execute_update(
        InteractiveResearchWorkflow.submit_elicitation_response,
        ElicitationResponseInput(
            elicitation_id=elicitation_id,
            response=request.answer.strip(),
        ),
    )

    status = await handle.query(InteractiveResearchWorkflow.get_status)
    return {
        "status": "accepted",
        "workflow_status": status.status,
    }


@app.get("/api/result/{workflow_id}")
async def get_result(workflow_id: str):
    """
    Get final research result.

    Returns:
        workflow_id: Workflow identifier
        markdown_report: Full markdown research report
        short_summary: Brief summary of findings
        follow_up_questions: Suggested follow-up questions
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)

    # Check if workflow is complete
    desc = await handle.describe()
    if not desc.status or desc.status.name != "COMPLETED":
        raise HTTPException(status_code=400, detail="Research not complete yet")

    result: InteractiveResearchResult = await handle.result()

    # return {
    #     "workflow_id": workflow_id,
    #     "markdown_report": result.markdown_report,
    #     "short_summary": result.short_summary,
    #     "follow_up_questions": result.follow_up_questions or [],
    # }

    return result


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        # "temporal_profile": TEMPORAL_PROFILE,
        "temporal_address": temporal_config.get("target_host"),
        "temporal_namespace": temporal_config.get("namespace"),
        "task_queue": TEMPORAL_TASK_QUEUE,
    }


# ============================================
# Main Entry Point
# ============================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8234)
