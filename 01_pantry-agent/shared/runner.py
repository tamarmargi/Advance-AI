"""Scenario runner shared by both versions: runs sessions, snapshots state, runs checks, writes results.

An agent version plugs in through a factory:
    make_agent(model: str, effort: str) -> agent
    agent.send(user_text: str) -> {"text": str,
                                   "tool_calls": [{"name", "input", "result", "is_error"}],
                                   "context_user_messages": [str],  # user texts in the model context this turn
                                   "memory_loaded": bool,           # preferences injected from disk
                                   "model_calls": int,
                                   "usage": {input_tokens, cache_read_input_tokens,
                                             cache_creation_input_tokens, output_tokens, cost_usd}}
    agent.close()  (optional)
Each session gets a NEW agent instance; only files on disk carry over.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from shared import tools
from shared.checks import print_checks, run_checks

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT / "scenario.json"
TOKEN_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
ANSWER_CHARS = 300


def log(message: str) -> None:
    print(f"[runner.py] {message}", flush=True)


def snapshot_state() -> dict:
    return {name.removesuffix(".json"): tools._load(name) for name in tools.STATE_FILES}


def cache_hit_pct(t: dict) -> float:
    denom = t["input_tokens"] + t["cache_read_input_tokens"] + t["cache_creation_input_tokens"]
    return round(100 * t["cache_read_input_tokens"] / denom, 1) if denom else 0.0


def turn_row(session_id: int, rec: dict) -> dict:
    u = rec.get("usage") or {}
    return {
        "session": session_id, "turn": rec["n"], "user": rec["user"],
        "answer": rec.get("text", "")[:ANSWER_CHARS] + ("…" if len(rec.get("text", "")) > ANSWER_CHARS else ""),
        **{f: u.get(f, 0) or 0 for f in TOKEN_FIELDS},
        "model_calls": rec.get("model_calls", 0),
        "tools": [c["name"] for c in rec.get("tool_calls", [])],
        "cost_usd": u.get("cost_usd"),
        "seconds": rec.get("seconds"),
    }


def totals(rows: list[dict]) -> dict:
    t = {f: sum(r[f] for r in rows) for f in TOKEN_FIELDS}
    t["model_calls"] = sum(r["model_calls"] for r in rows)
    t["tool_calls"] = sum(len(r["tools"]) for r in rows)
    costs = [r["cost_usd"] for r in rows]
    t["cost_usd"] = round(sum(costs), 6) if all(c is not None for c in costs) else None
    t["seconds"] = round(sum(r["seconds"] or 0 for r in rows), 2)
    t["cache_hit_pct"] = cache_hit_pct(t)
    return t


def run_scenario(make_agent: Callable, variant: str, scenario: dict, reset: Callable[[], None] | None = None) -> dict:
    """One full scenario run -> {"sessions": raw records + state, "turns": rows, "totals", "checks"}."""
    tools.reset_state()
    if reset:
        reset()
    result = {"variant": variant, "started": datetime.now().isoformat(timespec="seconds"),
              "scenario": scenario, "sessions": []}
    for session in scenario["sessions"]:
        log(f"session {session['id']}: new agent instance")
        agent = make_agent(scenario["model"], scenario["effort"])
        turns = []
        try:
            for turn in session["turns"]:
                log(f"turn {turn['n']} user: {turn['user']}")
                t0 = time.perf_counter()
                rec = agent.send(turn["user"])
                rec.update(n=turn["n"], user=turn["user"], seconds=round(time.perf_counter() - t0, 2))
                rec.setdefault("tool_calls", [])
                turns.append(rec)
                log(f"turn {turn['n']} assistant: {rec.get('text', '')}")
        finally:
            if hasattr(agent, "close"):
                agent.close()
        result["sessions"].append({"id": session["id"], "turns": turns, "state": snapshot_state()})
        log(f"session {session['id']}: state snapshot saved")

    result["turns"] = [turn_row(s["id"], t) for s in result["sessions"] for t in s["turns"]]
    result["totals"] = totals(result["turns"])
    result["checks"] = run_checks(result)
    print_checks(result["checks"])
    return result


def _mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 4) if values else None


def summarize(runs: list[dict]) -> dict:
    keys = list(runs[0]["totals"])
    mean_totals = {k: _mean([r["totals"][k] for r in runs]) for k in keys}
    per_turn = []
    for i, row in enumerate(runs[0]["turns"]):
        rows = [r["turns"][i] for r in runs]
        per_turn.append({"session": row["session"], "turn": row["turn"],
                         **{k: _mean([x[k] for x in rows]) for k in (*TOKEN_FIELDS, "model_calls", "cost_usd", "seconds")},
                         "tool_calls": _mean([len(x["tools"]) for x in rows])})
    ids = [c["id"] for c in runs[0]["checks"]]
    pass_rate = {cid: sum(next(c["passed"] for c in r["checks"] if c["id"] == cid) for r in runs) / len(runs)
                 for cid in ids}
    return {"runs": len(runs), "totals": mean_totals, "per_turn": per_turn, "check_pass_rate": pass_rate,
            "checks_passed": _mean([sum(c["passed"] for c in r["checks"]) for r in runs]),
            "checks_total": len(ids)}


def cli_args(default_out: str):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default=str(SCENARIO))
    p.add_argument("--out", default=default_out)
    p.add_argument("--runs", type=int, default=1)
    return p


def run_many(make_agent: Callable, variant: str, scenario_path: Path, out_path: Path, runs: int = 1,
             reset: Callable[[], None] | None = None, loc: dict | None = None) -> dict:
    scenario = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    all_runs = []
    for i in range(1, runs + 1):
        log(f"===== {variant} run {i}/{runs} =====")
        all_runs.append(run_scenario(make_agent, variant, scenario, reset))
    out = {"variant": variant, "model": scenario["model"], "effort": scenario["effort"],
           "created": datetime.now().isoformat(timespec="seconds"),
           "mean": summarize(all_runs), "loc": loc, "runs": all_runs}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    m = out["mean"]["totals"]
    log(f"{variant}: {runs} run(s), mean checks {out['mean']['checks_passed']}/{out['mean']['checks_total']}, "
        f"mean cost {m['cost_usd']}, cache hit {m['cache_hit_pct']}%, written to {out_path}")
    return out
