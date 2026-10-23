"""End-to-end smoke driver for the interactive research demo.

Drives InteractiveResearchWorkflow exactly the way the FastAPI backend does
(start with the query -> answer each pending elicitation -> await
result), records one JSON record per run to a JSONL file, and can then analyze
the resulting Temporal histories.

Usage:
    uv run scripts/demo_smoke.py run --results FILE [--query TEXT] [--timeout S]
    uv run scripts/demo_smoke.py analyze --results FILE

Not a production pattern; a demo robustness harness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from typing import Any

from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.openai_agents._temporal_openai_agents import (
    OpenAIPayloadConverter,
)
from temporalio.converter import DataConverter
from temporalio.envconfig import ClientConfig

from openai_agents.workflows.interactive_research_workflow import (
    InteractiveResearchWorkflow,
)
from openai_agents.workflows.research_agents.research_models import (
    ElicitationResponseInput,
    UserQueryInput,
)

TASK_QUEUE = "research-queue"

# Generic-but-useful answers for whatever clarifying questions the orchestrator
# asks; content does not gate success. Indexed by how many we've answered so the
# two replies differ a little, like a real user.
CLARIFICATION_ANSWERS = [
    "General professional audience; focus on the most important and recent, "
    "practical findings.",
    "Give a balanced overview with concrete, current examples; no strict "
    "constraints on scope or format.",
]


def _answer_for(n_answered: int) -> str:
    return CLARIFICATION_ANSWERS[min(n_answered, len(CLARIFICATION_ANSWERS) - 1)]


async def _client() -> Client:
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")
    config.setdefault("namespace", "default")
    return await Client.connect(
        **config,
        data_converter=DataConverter(payload_converter_class=OpenAIPayloadConverter),
    )


def _field(obj: Any, key: str, default: Any = None) -> Any:
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


async def _drive(handle: WorkflowHandle, deadline: float) -> tuple[int, str]:
    """Drive the whole run to a terminal state, answering elicitations as they
    appear.

    A single loop is essential: "researching" also appears in the gap between
    the first and second clarifying question (while the orchestrator is
    thinking), so we must NOT stop steering on it - we keep polling and answer
    every elicitation until the workflow reports completed.

    Returns (num_answered, last_status).
    """
    answered_ids: set[str] = set()
    last_status = "pending"
    while time.monotonic() < deadline:
        status = await handle.query(InteractiveResearchWorkflow.get_status)
        last_status = _field(status, "status", "pending")
        if last_status == "completed":
            break
        pending = _field(status, "pending_elicitation")
        if pending is not None:
            el_id = _field(pending, "id")
            if el_id not in answered_ids:
                await handle.execute_update(
                    InteractiveResearchWorkflow.submit_elicitation_response,
                    ElicitationResponseInput(
                        elicitation_id=el_id, response=_answer_for(len(answered_ids))
                    ),
                )
                answered_ids.add(el_id)
                continue  # re-check immediately for the next question
        await asyncio.sleep(2.0)
    return len(answered_ids), last_status


async def run_once(results_path: str, query: str, timeout_s: float) -> dict[str, Any]:
    client = await _client()
    wf_id = f"interactive-research-{uuid.uuid4().hex[:8]}"
    record: dict[str, Any] = {
        "workflow_id": wf_id,
        "query": query,
        "t_start": time.time(),
    }
    t0 = time.monotonic()
    deadline = t0 + timeout_s
    try:
        handle = await client.start_workflow(
            InteractiveResearchWorkflow.run,
            UserQueryInput(query=query),
            id=wf_id,
            task_queue=TASK_QUEUE,
        )
        record["run_id"] = handle.first_execution_run_id
        n_answered, _ = await _drive(handle, deadline)
        record["elicitations_answered"] = n_answered

        remaining = max(1.0, deadline - time.monotonic())
        result = await asyncio.wait_for(handle.result(), timeout=remaining)

        markdown = _field(result, "markdown_report", "") or ""
        follow_ups = _field(result, "follow_up_questions", []) or []
        image_path = _field(result, "image_file_path", None)
        record.update(
            {
                "outcome": "success",
                "markdown_len": len(markdown),
                "n_follow_ups": len(follow_ups),
                "has_image": bool(image_path),
                "image_path": image_path,
                "short_summary": (_field(result, "short_summary", "") or "")[:200],
            }
        )
    except asyncio.TimeoutError:
        record.update({"outcome": "error", "error": f"client timeout after {timeout_s}s"})
    except Exception as e:  # noqa: BLE001 - record any failure for the report
        record.update({"outcome": "error", "error": f"{type(e).__name__}: {e}"})
    finally:
        record["duration_s"] = round(time.monotonic() - t0, 1)
        record["t_end"] = time.time()

    with open(results_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(
        f"[{record.get('outcome', '?'):>14}] {record['duration_s']:>5.1f}s "
        f"elic={record.get('elicitations_answered', '-')} "
        f"md={record.get('markdown_len', '-')} "
        f"img={record.get('has_image', '-')} {wf_id} :: {query[:48]}"
    )
    return record


async def analyze(results_path: str) -> None:
    client = await _client()
    with open(results_path) as f:
        records = [json.loads(line) for line in f if line.strip()]

    print(f"\n==== TRACE ANALYSIS ({len(records)} runs) ====")
    agg = {
        "success": 0,
        "error": 0,
        "durations": [],
        "model_acts": [],
        "act_failed_total": 0,
        "warehouse_fail_total": 0,
    }
    for r in records:
        wf_id = r["workflow_id"]
        line: dict[str, Any] = {
            "workflow_id": wf_id,
            "outcome": r.get("outcome"),
            "duration_s": r.get("duration_s"),
        }
        try:
            handle = client.get_workflow_handle(wf_id)
            desc = await handle.describe()
            line["wf_status"] = desc.status.name if desc.status else "?"
            sched: dict[str, int] = {}
            act_failed = 0
            warehouse_fail = 0
            async for ev in handle.fetch_history_events():
                if ev.HasField("activity_task_scheduled_event_attributes"):
                    name = ev.activity_task_scheduled_event_attributes.activity_type.name
                    sched[name] = sched.get(name, 0) + 1
                if ev.HasField("activity_task_failed_event_attributes"):
                    act_failed += 1
                    # Heuristic: warehouse failures carry the simulated-reset msg.
                    msg = (
                        ev.activity_task_failed_event_attributes.failure.message
                        if ev.activity_task_failed_event_attributes.HasField("failure")
                        else ""
                    )
                    if "connection reset" in msg or "data warehouse" in msg:
                        warehouse_fail += 1
            model_acts = sched.get("invoke_model_activity", 0)
            line["model_activities"] = model_acts
            line["activity_scheduled"] = sched
            line["activity_failed"] = act_failed
            agg["model_acts"].append(model_acts)
            agg["act_failed_total"] += act_failed
            agg["warehouse_fail_total"] += warehouse_fail
        except Exception as e:  # noqa: BLE001
            line["trace_error"] = f"{type(e).__name__}: {e}"

        oc = r.get("outcome", "error")
        if oc in agg:
            agg[oc] += 1
        if isinstance(r.get("duration_s"), (int, float)):
            agg["durations"].append(r["duration_s"])
        print(json.dumps(line))

    ds = sorted(agg["durations"])
    n = len(ds)

    def pct(p: float) -> float:
        if not ds:
            return 0.0
        return ds[min(n - 1, int(p * n))]

    print("\n---- AGGREGATE ----")
    print(
        f"runs={len(records)} success={agg['success']} error={agg['error']}"
    )
    if ds:
        print(
            f"duration_s: min={ds[0]} p50={pct(0.5)} p90={pct(0.9)} max={ds[-1]} "
            f"mean={round(sum(ds)/n,1)}"
        )
    if agg["model_acts"]:
        ma = agg["model_acts"]
        print(
            f"model_activities per run: min={min(ma)} max={max(ma)} "
            f"mean={round(sum(ma)/len(ma),1)}"
        )
    print(
        f"activity failures across all runs: total={agg['act_failed_total']} "
        f"(warehouse-simulated={agg['warehouse_fail_total']})"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--results", required=True)
    r.add_argument("--query", required=True)
    r.add_argument("--timeout", type=float, default=360.0)
    a = sub.add_parser("analyze")
    a.add_argument("--results", required=True)
    args = ap.parse_args()

    if args.cmd == "run":
        asyncio.run(run_once(args.results, args.query, args.timeout))
    else:
        asyncio.run(analyze(args.results))


if __name__ == "__main__":
    main()
