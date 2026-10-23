"""
FastAPI backend for the research demo UI.

Serves the UI and generated images, and talks to the research workflow:
starts it, delivers answers, reports status, and returns the result.

Environment Variables:
- TEMPORAL_PROFILE: name of the env config profile to use (optional).
- TEMPORAL_ADDRESS: Temporal server address (default: localhost:7233)
- TEMPORAL_NAMESPACE: Temporal namespace (default: default)
- TEMPORAL_API_KEY: API key for Temporal Cloud (disabled by default)
- TEMPORAL_TASK_QUEUE: Task queue name (default: research-queue)
"""

import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from temporalio.client import (
    Client,
    WorkflowExecutionDescription,
    WorkflowExecutionStatus,
    WorkflowHandle,
)
from temporalio.contrib.openai_agents import OpenAIPayloadConverter
from temporalio.converter import DataConverter
from temporalio.envconfig import ClientConfig
from temporalio.service import RPCError, RPCStatusCode

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchResult,
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import (
    ElicitationResponseInput,
    UserQueryInput,
)

load_dotenv()

TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "research-queue")
UI_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = UI_DIR.parent / "temp_images"

app = FastAPI(title="Temporal Research API")

temporal_client: Optional[Client] = None
temporal_config = ClientConfig.load_client_connect_config()
temporal_config.setdefault("target_host", "localhost:7233")
temporal_config.setdefault("namespace", "default")


async def get_temporal_client() -> Client:
    global temporal_client
    if temporal_client:
        return temporal_client

    print(
        f"Connecting to Temporal at {temporal_config.get('target_host')} "
        f"in namespace {temporal_config.get('namespace')}"
    )
    temporal_client = await Client.connect(
        **temporal_config,
        data_converter=DataConverter(payload_converter_class=OpenAIPayloadConverter),
    )
    return temporal_client


async def describe(handle: WorkflowHandle) -> WorkflowExecutionDescription:
    """Describe the workflow, mapping an unknown workflow id to a 404."""
    try:
        return await handle.describe()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            raise HTTPException(status_code=404, detail="Workflow not found") from e
        raise


class StartResearchRequest(BaseModel):
    query: str


class AnswerRequest(BaseModel):
    answer: str


# ============================================
# UI and images
# ============================================
@app.get("/")
async def serve_index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/success")
async def serve_success():
    return FileResponse(UI_DIR / "success.html")


app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

IMAGES_DIR.mkdir(exist_ok=True)
app.mount("/temp_images", StaticFiles(directory=IMAGES_DIR), name="images")


# ============================================
# API
# ============================================
@app.post("/api/start-research")
async def start_research(request: StartResearchRequest):
    """Start a research workflow for the query and return its id."""
    client = await get_temporal_client()
    workflow_id = f"interactive-research-{uuid.uuid4().hex[:8]}"
    await client.start_workflow(
        InteractiveResearchWorkflow.run,
        UserQueryInput(query=request.query.strip()),
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
    )
    return {"workflow_id": workflow_id}


_FAILED_RUN_STATUSES = {
    WorkflowExecutionStatus.FAILED,
    WorkflowExecutionStatus.TERMINATED,
    WorkflowExecutionStatus.TIMED_OUT,
    WorkflowExecutionStatus.CANCELED,
}


@app.get("/api/status/{workflow_id}")
async def get_status(workflow_id: str):
    """Current conversation and progress, from the workflow's status query.

    A failed run is reported from the execution status alone, since querying
    it would need a worker to replay a run that cannot make progress.
    """
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    desc = await describe(handle)
    if desc.status in _FAILED_RUN_STATUSES:
        return {"workflow_id": workflow_id, "status": "failed"}

    status = await handle.query(InteractiveResearchWorkflow.get_status)
    return {
        "workflow_id": workflow_id,
        "status": status.status,
        "original_query": status.original_query,
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
    return {"status": "accepted"}


@app.get("/api/result/{workflow_id}")
async def get_result(workflow_id: str) -> InteractiveResearchResult:
    """The final report of a completed run. Needs no running worker."""
    client = await get_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    desc = await describe(handle)
    if desc.status != WorkflowExecutionStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Research not complete")
    return await handle.result()


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "temporal_address": temporal_config.get("target_host"),
        "temporal_namespace": temporal_config.get("namespace"),
        "task_queue": TEMPORAL_TASK_QUEUE,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8234)
