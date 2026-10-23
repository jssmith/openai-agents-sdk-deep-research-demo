# Talk runbook: the two Temporal beats

A presenter-facing script for the two "why Temporal" moments in this demo:

1. **Worker death**: kill the worker while the agent waits on the second
   clarifying question; a new worker continues the same conversation.
2. **Activity retries**: a flaky data-warehouse lookup fails 8 times and
   recovers through Temporal retries, without redoing completed work.

Keep this open on a second screen.

---

## 0. One-time setup

```bash
uv sync
cp .env-sample .env      # then set OPENAI_API_KEY
```

The local fallback model needs Ollama (`ollama pull qwen2.5:14b-instruct`), or
set `DEMO_MODEL_LOCAL=''` in `.env` to disable it. For cheap rehearsals,
`ORCHESTRATOR_MODEL='gpt-5-mini'` and a cached `DEMO_RESEARCH_IMAGE_PATH` cut
cost and latency; remove the cached image for the real talk, so the image is
generated live.

## 1. Bring up the stack

```bash
scripts/start-demo --warehouse-failures 8
```

This starts Temporal (or reuses one on :7233), Ollama if needed, the worker,
and the backend in a tmux session, and validates the configuration first. The
worker banner should show `warehouse_retry_failures=8` and `image='live'`.

Open **two windows side by side**: the chat UI (<http://127.0.0.1:8234>) and
the Temporal UI (<http://localhost:8233>).

`scripts/stop-demo` stops everything (a Temporal server that was already
running keeps running). Each research run is its own workflow, so takes don't
interfere; the reset button in the chat starts a new one.

---

## 2. Beat: the worker dies mid-conversation

**Steps:**

1. Ask a question with several facets, e.g.
   > *"What are the tradeoffs between durable execution frameworks for building AI agents?"*
2. Answer the **first** clarifying question.
3. When the **second** question is on screen, kill the worker in the `shell`
   window:
   ```bash
   scripts/crash-worker
   ```
   It is a real `kill -9`. The `worker` window shows the process exited.
4. Point at the Temporal UI: the workflow is still **Running**, with the
   question and the first answer in its history.
5. Restart the worker: press Enter in the `worker` window.
6. Type the answer to the second question and submit. The agent continues
   into research.

If you submit while the worker is still down, the UI waits ("Still
researching...") and the answer is delivered once the worker is back.

**Narration:** *"I killed the process that was running this agent. The
question, my first answer, the agent's progress: none of it lived in that
process. A new worker rebuilt the run from its Temporal history, without
re-running the model calls that had finished, and picked up exactly where we
were."*

## 3. Beat: a flaky dependency, retried

This happens on its own in the same run, after the research fan-out.

**What to show:** after the research cards appear, open the workflow in the
Temporal UI. `DataWarehouseLookup` is a pending activity whose attempt count
climbs, each failure reading *"Simulated remote connection reset..."*. After
attempt 9 (about 40s) it succeeds, and the run continues to the report.

**Narration:** *"This lookup is a stand-in for an unreliable internal system.
Temporal retries just that call with backoff. The clarifications and the six
research calls that already finished are not repeated; they're recorded in
history."*

**Also worth pointing at:** the research fan-out right before it, several
`invoke_model_activity` entries scheduled at once, one per research sub-agent.

**After the fact:**

```bash
bash scripts/show-history <workflow-id>
```

prints the activity timeline, with the failed warehouse attempts and the
attempt that succeeded.

---

## 4. Timing and gotchas

- A clean run takes about 1-2 minutes; the retry beat adds about 40s, and the
  crash adds only the seconds the new worker takes to start.
- While the worker is down, the chat's status polling pauses (its status query
  needs a worker); it resumes on its own.
- Reloading the chat page is safe at any point: the run's id is in the URL and
  the conversation is rebuilt from the workflow.
