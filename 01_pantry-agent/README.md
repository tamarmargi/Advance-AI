# pantry-agent

One pantry agent built twice, for comparison:

- `API/` — on the raw Anthropic Messages API, with a hand-written harness (`API/harness/`).
- `SDK/` — on the Claude Agent SDK.

Domain code (pantry/recipe tools and data) lives in `shared/` and is used by both.

## Setup

```
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
```

## Optional web UI

```
streamlit run ui/app.py
```

Hebrew, right-to-left chat with either agent. The sidebar picks API or SDK, starts a new session
(optionally resuming the previous SDK session) and resets the saved state. Tabs show the saved state
and an API-vs-SDK comparison from `results/api.json` and `results/sdk.json`. The same authentication
rules as below apply: set the key for API, leave it unset for SDK.

## Authentication

No API key is ever stored in this repository.

- **API version** reads `ANTHROPIC_API_KEY` from the environment:
  ```
  $env:ANTHROPIC_API_KEY = "sk-ant-..."   # PowerShell
  python API/main.py
  ```
- **SDK version** must run with `ANTHROPIC_API_KEY` **unset**, so it uses your Claude Code login:
  ```
  Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue   # PowerShell
  python SDK/main.py
  ```
