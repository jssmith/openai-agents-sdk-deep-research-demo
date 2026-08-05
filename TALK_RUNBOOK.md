# Talk runbook — the two Temporal beats

A tight, presenter-facing script for the two "why Temporal" moments in this demo:

1. **Parallel execution** — the agent fans work out and Temporal runs it concurrently.
2. **User input isn't lost when you kill the workers** — durable state survives a real process death.

Keep this open on a second screen. Every command below reuses the existing `scripts/`.
Verified end-to-end on this machine (2026-07-31) with the config in the next section.

---

## 0. One-time setup

```bash
cd /Users/jssmith/t/openai-agents-sdk-deep-research-demo
uv sync
cp .env-sample .env      # then set OPENAI_API_KEY
```

**Verification config used here (reliable, cheap):** in `.env`,

```
ORCHESTRATOR_MODEL='gpt-5-mini'
RESEARCH_WORKER_MODEL='gpt-5-mini'
DEMO_RESEARCH_IMAGE_PATH='temp_images/generated_image_20260505_091349_451235.jpeg'
# DEMO_DATA_WAREHOUSE_RETRY_FAILURES left unset (or 0) for a clean run
```

- `gpt-5-mini` avoids gpt-5 access issues and keeps latency low.
- The cached `DEMO_RESEARCH_IMAGE_PATH` skips live image generation entirely.
- **For the actual recording**, if your key has access, drop `ORCHESTRATOR_MODEL` back to
  `gpt-5` and remove `DEMO_RESEARCH_IMAGE_PATH` for a live image. Nothing else changes.

## 1. Bring up the stack (3 things)

```bash
# Temporal dev server — reuse an existing one if it's already on :7233.
temporal server start-dev                 # UI at http://localhost:8233

# Worker (writes .demo-worker.pid, which crash-worker targets)
uv run --env-file .env openai_agents/run_worker.py

# Backend + browser UI
uv run --env-file .env ui/backend/main.py # UI at http://127.0.0.1:8234
```

Sanity check: `bash scripts/check-ports` (7233 / 8233 / 8234).
Open **two windows side by side**: the chat UI (8234) and the Temporal Web UI (8233).

---

## 2. Beat — Parallel execution

**Setup.** In the chat UI, ask a question with several independent facets, e.g.

> *"What are the tradeoffs between durable execution frameworks for building AI agents?"*

Answer the **two clarifying questions** (any answers; scoping them to "production" and a couple
of named systems makes the research richer).

**The moment.** Right after the second answer, switch to the **Temporal Web UI** → open the
running workflow → **Pending Activities**. You'll see a burst of concurrent activities:

```
6 pending: invoke_model_activity x6   →  x4  →  x2  →  x1   (then DataWarehouseLookup)
```

Each `invoke_model_activity` is one `ResearchWorkerAgent` sub-agent researching a different
subquery. The orchestrator issued them all at once (`asyncio.gather` in
`interactive_research_workflow.py`), so Temporal ran them **in parallel** on the worker's
activity slots — not one after another.

**Narration:** *"The agent decided to research six angles at once. It didn't write any threading
code — it just called the tools, and Temporal fanned them out as parallel activities and tracked
every one. If any single one failed, only that one retries."*

**Optional after-the-fact view:**

```bash
bash scripts/show-history <workflow-id>
```

Look for several `invoke_model_activity` rows **scheduled at the same timestamp** — that's the
fan-out on the timeline. (Note: with a cached image, `generate_research_image` returns instantly,
so the headline parallelism to point at is the research fan-out.)

---

## 3. Beat — User input isn't lost when you kill the workers

This is the durable-execution payoff. Use a **single worker** and kill it **while a clarifying
question is on screen** (the workflow is parked, waiting on the human — the clean, reliable place
to crash; see `KNOWN_ISSUES.md` for why not mid-activity).

**Steps:**

1. Start a fresh query in the chat UI, e.g.
   > *"How does durable execution improve reliability of long-running AI agent workflows?"*
2. Wait until the **first clarifying question appears** on screen. The workflow is now parked.
   (In the Temporal UI: status **Running**, and it's sitting idle — no activity in flight.)
3. **Kill the worker** — a real `kill -9`:
   ```bash
   bash scripts/crash-worker
   ```
4. Show it's really dead and the workflow is fine anyway:
   ```bash
   temporal workflow describe -w <workflow-id> --address localhost:7233 \
     | grep -E 'Status|HistoryLength'
   ```
   → still **Running**, history length **unchanged**. The question is still on screen in the chat
   UI (the browser keeps showing the last state; its status poll simply pauses).
   - *Teaching aside if you want it:* the chat UI's status endpoint is a **workflow query**, which
     needs a live worker to answer — so it goes quiet while the worker is dead, yet the workflow
     itself is untouched. The truth lives in Temporal, not the process.
5. **Restart the worker:**
   ```bash
   uv run --env-file .env openai_agents/run_worker.py
   ```
6. **Now type the answer** to the question that's still on screen and submit it. The run picks up
   exactly where it left off and continues to the final report.

**What the audience sees / what to say:** *"I killed the worker — the whole process is gone. My
question, my half-finished conversation, all of it. I bring a new worker up, answer the question
that's still sitting there, and it just continues. Nothing re-ran, nothing was lost — because the
state was never in the worker. It's durable in Temporal."*

**Proof it resumed cleanly (optional):** history **appends** rather than rewrites — it went from
15 events at the crash to 26 after the answer, same run, and `show-history` shows the earliest
events unchanged with the answer landing at the end (post-restart).

---

## 4. Helpers & teardown

- `bash scripts/crash-worker` — SIGKILL the worker (targets `.demo-worker.pid`, which the
  worker writes on startup). Pass a name (e.g. `crash-worker 2`) to target `.demo-worker-2.pid`.
- `uv run --env-file .env openai_agents/run_worker.py` — (re)start a worker. For a second
  instance, prefix `DEMO_WORKER_PID_FILE=.demo-worker-2.pid`.
- `bash scripts/show-history <workflow-id>` — TSV activity/failure/completion timeline.
- `bash scripts/demo-status <workflow-id>` — hit the backend status/result endpoints.
- `bash scripts/stop-demo-workers` — graceful shutdown of workers tracked by `.demo-worker*.pid`.

**Reset between takes:** `bash scripts/stop-demo-workers` (or `crash-worker`), then
`uv run --env-file .env openai_agents/run_worker.py` for a fresh worker. Each research run is
its own workflow id, so old runs don't interfere.

## 5. Timing & gotchas

- A clean run is ~1–2 min on `gpt-5-mini`; the crash/restart adds only the seconds it takes the
  new worker to start polling.
- Kill the worker **while a clarification is pending**, not during the parallel research burst.
- If `/api/status` looks stuck, check a worker is actually up (`check-ports`, or the pid file) —
  the query needs one.
- No retry/failure-injection beat is used here. If you ever want it, the `DEMO_DATA_WAREHOUSE_*`
  knobs in `.env` drive the activity-retry story (see `README.md → Failure injection`).
