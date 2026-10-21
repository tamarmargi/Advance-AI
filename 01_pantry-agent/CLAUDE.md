# CLAUDE.md — project instructions

## Assignment goal
University assignment: build **one** pantry agent **twice** and compare them.
1. `API/` — on the raw Anthropic Messages API with a hand-written harness.
2. `SDK/` — on the Claude Agent SDK.
Both versions must behave the same on `scenario.json`; findings go in `REPORT.md`, run outputs in `results/`.

## Folder rules
- `shared/` — **all domain code** and data. Domain logic lives **only** here; never duplicate it in `API/` or `SDK/`. It is plain Python: never import `anthropic` or `claude_agent_sdk` in `shared/`.
  - `tools.py` — tool functions (compact string results, ≤500 chars) + `TOOLS` name→function map + `reset_state()`.
  - `tool_specs.py` — `TOOL_SPECS` (name, description, input_schema): the single source both harnesses build tool definitions from.
  - `errors.py` — `PantryError` and subclasses (`RecipeNotFound`, `KashrutConflict`, `InvalidInput`); harnesses turn these into tool errors for the model.
  - `runner.py` — `run_scenario(make_agent, variant)`: runs `scenario.json`, a NEW agent per session, snapshots state after each session, runs checks, writes `results/<variant>_<ts>.json`. Agents must implement `send(text) -> {text, tool_calls:[{name,input,result,is_error}], context_user_messages, memory_loaded}` (see its docstring).
  - `checks.py` — `run_checks(results)`; `python -m shared.checks results/<file>.json` re-checks a saved run.
  - `data/recipes/` (r01–r20), `data/products_stub.json` (offline `lookup_product`), `data/runtime/` (state, git-ignored; override with `PANTRY_DATA_DIR`). `PANTRY_OFFLINE=1` forces the stub.
- `API/` — `main.py` (entry), `agent.py` (agent definition), `harness/` (one concern per file: `loop`, `tools`, `history`, `memory`, `context`, `compaction`, `caching`, `tokens`).
  - Harness invariants (claude-opus-5-5 runs preserved thinking — edited history is rejected): `system` and `tools` are frozen per session (memory is a snapshot taken at `Agent.__init__`); history is append-only; compaction is "simple compaction" at turn start only (summary + new turn, nothing older replayed) and never edits `history.jsonl`; `cache_control` is added only to the copy sent, max 4 breakpoints. Effort goes in `output_config={"effort": ...}`. Only `client.messages.create` — no tool runner, no beta helpers.
- `SDK/` — `main.py`, `agent.py`, `tools.py` (thin SDK wrappers around `shared/tools.py`).
- `tests/` — pytest tests.
- Do not add new top-level folders without asking.

## Logging rule (API harness)
Every action the API harness takes must print a line prefixed with its file name:
```
[<filename>] <message>
```
e.g. `[loop.py] turn 3: calling model`, `[compaction.py] compacted 12 messages`.

## Traceability rule (SDK)
Every SDK line that replaces functionality from a harness file must carry a comment:
```
# replaces: <name>
```
where `<name>` is the harness file it replaces, e.g. `# replaces: loop`, `# replaces: compaction`.

## Secrets
Never write an API key into any file. The API version reads `ANTHROPIC_API_KEY` from the environment; the SDK version runs with it **unset** so it uses the Claude Code login.
