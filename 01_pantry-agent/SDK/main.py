"""Entry point for the Claude Agent SDK version of the pantry agent.

    python SDK/main.py --scenario scenario.json --out results/sdk.json [--runs N]
    python SDK/main.py --turn "..."          # one turn on a fresh session

Must run with ANTHROPIC_API_KEY unset so Claude Code uses its own login.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)  # run as a script from anywhere: import from the repo root, not SDK/
sys.stdout.reconfigure(errors="replace")  # emoji / Hebrew must never crash a cp1255 Windows console

from scripts.count_loc import count_loc  # noqa: E402
from SDK.agent import Agent, api_key_is_set, reset_sessions  # noqa: E402
from shared.runner import cli_args, run_many  # noqa: E402


def make_factory():
    """Session 1: new Claude Code session. Later sessions: resume the previous one."""
    previous: dict = {}

    def make_agent(model: str, effort: str) -> Agent:
        agent = Agent(model, effort, resume=previous.get("session_id"),  # replaces: history
                      cost_offset=previous.get("cumulative_cost", 0.0))
        original_close = agent.close

        def close():
            previous["session_id"] = agent.session_id
            previous["cumulative_cost"] = agent.cumulative_cost
            original_close()

        agent.close = close
        return agent

    def reset():
        previous.clear()
        reset_sessions()

    return make_agent, reset


def main() -> None:
    p = cli_args("results/sdk.json")
    p.add_argument("--turn", help="send one user message instead of the full scenario")
    p.add_argument("--allow-api-key", action="store_true", help="run even if ANTHROPIC_API_KEY is set")
    args = p.parse_args()

    if api_key_is_set():
        print("[main.py] WARNING: ANTHROPIC_API_KEY is set. The SDK version must run on the Claude Code login.")
        if not args.allow_api_key:
            print("[main.py] Unset it (PowerShell: Remove-Item Env:ANTHROPIC_API_KEY) or pass --allow-api-key.")
            sys.exit(2)
    else:
        print("[main.py] ANTHROPIC_API_KEY is not set: using the Claude Code login.")

    if args.turn:
        cfg = json.loads(Path(args.scenario).read_text(encoding="utf-8"))
        agent = Agent(cfg["model"], cfg["effort"])
        try:
            record = agent.send(args.turn)
        finally:
            agent.close()
        print(json.dumps(record, ensure_ascii=False, indent=1))
        return

    make_agent, reset = make_factory()
    run_many(make_agent, "SDK", Path(args.scenario), Path(args.out), runs=args.runs, reset=reset, loc=count_loc())


if __name__ == "__main__":
    main()
