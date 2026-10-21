"""Entry point for the raw Messages API version of the pantry agent.

    python API/main.py --scenario scenario.json --out results/api.json [--runs N]

Reads ANTHROPIC_API_KEY from the environment.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[0] = str(ROOT)  # run as a script from anywhere: import from the repo root, not API/
sys.stdout.reconfigure(errors="replace")  # emoji / Hebrew must never crash a cp1255 Windows console

from API.agent import Agent  # noqa: E402
from scripts.count_loc import count_loc  # noqa: E402
from shared.runner import cli_args, run_many  # noqa: E402


def main() -> None:
    args = cli_args("results/api.json").parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[main.py] ANTHROPIC_API_KEY is not set. The API version needs it in the environment.")
        sys.exit(2)
    run_many(lambda model, effort: Agent(model, effort), "API", Path(args.scenario), Path(args.out),
             runs=args.runs, loc=count_loc())


if __name__ == "__main__":
    main()
