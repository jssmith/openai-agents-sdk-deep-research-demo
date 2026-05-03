# Temporal Interactive Deep Research Demo using OpenAI Agents SDK

This repository builds on the Temporal Interactive Deep Research Demo by @steveandroulakis, adding a web-based user interface.

For detailed information about the research agents in this repo, see [openai_agents/workflows/research_agents/README.md](openai_agents/workflows/research_agents/README.md)
Access original repo [here](https://github.com/steveandroulakis/openai-agents-demos)

## Key Features

- **Temporal Workflows**: This demo uses Temporal for reliable workflow orchestration
- **OpenAI Agents**: Powered by the OpenAI Agents SDK for natural language processing
- **Multi-Agent Systems**: The research demo showcases complex multi-agent coordination
- **Interactive Workflows**: Research demo supports real-time user interaction
- **Tool Integration**: Tools demo shows how to integrate external activities
- **PDF Generation**: Interactive research workflow generates professional PDF reports alongside markdown

## About this Demo: Multi-Agent Interactive Research Workflow

An enhanced version of the research workflow with interactive clarifying questions to refine research parameters before execution and optional PDF generation.

This example is designed to be similar to the OpenAI Cookbook: [Introduction to deep research in the OpenAI API](https://cookbook.openai.com/examples/deep_research_api/introduction_to_deep_research_api)

**Files:**

- `openai_agents/workflows/interactive_research_workflow.py` - Interactive research workflow
- `openai_agents/workflows/research_agents/` - All research agent components
- `openai_agents/workflows/pdf_generation_activity.py` - PDF generation activity
- `openai_agents/workflows/research_agents/pdf_generator_agent.py` - PDF generation agent

**Agents:**

- **Orchestrator Agent** (`research_agents/orchestrator_agent.py`): top-level
  agent that drives the entire workflow via tool calls — eliciting clarifying
  questions from the user, dispatching parallel research workers, querying the
  data warehouse, generating a thematic image, and emitting the final report
  as a structured response.
- **Research Worker Agent** (`research_agents/research_worker_agent.py`):
  small sub-agent the orchestrator fans out for each parallel subquery. Uses
  `WebSearchTool` and returns a `SearchSummary`.

(The original demo's separate Triage / Clarifying / Instruction / Planner /
Writer / PDF Generator agents were folded into the orchestrator's prompt and
tool surface during the agentic refactor; their files are kept under
`research_agents/` as legacy and aren't on the active code path.)

## Prerequisites

1. **Python 3.10+** - Required for the demos
2. Temporal Server - Must be running locally on localhost:7233 OR Connect to [Temporal Cloud](https://temporal.io)
3. **OpenAI API Key** - Set as environment variable `OPENAI_API_KEY` in .env file (note, you will need enough quota on in your [OpenAI account](https://platform.openai.com/api-keys) to run this demo)
4. **PDF Generation Dependencies** - Required for PDF output (optional)

## Install / Upgrade Temporal CLI
You'll need the latest version to run the demo.

```bash
# Install Temporal CLI
curl -sSf https://temporal.download/cli.sh | sh

# Alternately, upgrade to the latest version:
brew upgrade temporal
```

### Run Temporal Server Locally

```
# Start Temporal server
temporal server start-dev
```

### Or, Connect to Temporal Cloud

1. Uncomment the following line in your `.env` file:

```
# TEMPORAL_PROFILE=cloud
```

2. Run the following commands:

```
temporal config set --profile cloud --prop address --value "CLOUD_REMOTE_ADDRESS"
temporal config set --profile cloud --prop namespace  --value "CLOUD_NAMESPACE"
temporal config set --profile cloud --prop api_key --value "CLOUD_API_KEY"
```

See https://docs.temporal.io/develop/environment-configuration for more details.

For ease of use, all environemnt variables may be defined through the `.env` file,
at the root of the repository. See the .env-sample file for details.

## Setup

1. Clone this repository
2. Install dependencies:

   ```bash
   uv sync
   ```

   Note: If uv is not installed, please install uv by following the instructions [here](https://docs.astral.sh/uv/getting-started/installation/)

3. Set your [OpenAI API](https://platform.openai.com/api-keys) key:
   ```bash
   # Add OpenAI API key in .env file (copy .env-sample to .env and update the OPENAI_API_KEY)
   OPENAI_API_KEY=''
   ```

## Running the Demos

### 1. Start the Worker

In one terminal, start the worker that will handle all workflows:

```bash
uv run openai_agents/run_worker.py
```

Keep this running throughout your demo sessions. The worker registers all available workflows and activities.
You can run multiple copies of workers for faster workflow processing. Please ensure `OPENAI_API_KEY` is set before
you attempt to start the worker.

### 2. Run the UI

In another terminal:

```bash
uv run ui/backend/main.py
```

This will launch the Interactive Research App on http://0.0.0.0:8234

![UI Interface](ui/public/images/ui_img.png "UI Interface Img")

### 3. Use the Demo

In Google Chrome, go to chrome://flags/ search for "Split View" and enable it.

Close and re-open Chrome for it to take effect.

Open a new browser window with two tabs:

* Tab 1: Application UI — http://0.0.0.0:8234
* Tab 2: Temporal UI — http://localhost:8233/ (OSS) or https://cloud.temporal.io/namespaces/XXX/workflows (Temporal Cloud)

Right-click Tab 1, choose Add Tab to New Split View, and click the Workflows tab as the right-hand side.

Re-position the window divider so that the chat UI is taking up approximately 1/3 of the screen, leading the rest for the Temporal UI.

<img width="1498" height="807" alt="Side-by-side view of application UI and Temporal UI" src="https://github.com/user-attachments/assets/e236a56c-e0bb-4688-a4a1-5484441bfbae" />


**Output:** the final report (markdown body, summary, follow-up questions, and
a thematic image) is surfaced in the web UI's report card and on the full
report page. The workflow's structured result is fetched via the
`/api/result/{workflow_id}` endpoint; nothing is written to disk by default.

**Note:** the interactive workflow may take 1-3 minutes end-to-end depending
on web-search latency and how many of the demo's failure-injection beats are
enabled.

## Development

### Code Quality Tools

```bash
# Lint (unused imports / unused locals)
uv run ruff check --select F401,F841

# Type checking
uv run pyright .
```

## License

MIT License - see the original project for full license details.
