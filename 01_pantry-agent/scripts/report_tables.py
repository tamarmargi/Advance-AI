"""Markdown tables for REPORT.md, generated only from results files (no hand-typed numbers).

Usage: python scripts/report_tables.py results/api.json results/sdk.json
Prints: results table (mean ± stdev over runs), LOC table, per-turn comparison, and a list of
numbers that look suspicious.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

METRICS = [("input_tokens", "Input tokens"), ("cache_read_input_tokens", "Cache read"),
           ("cache_creation_input_tokens", "Cache write"), ("output_tokens", "Output tokens"),
           ("cache_hit_pct", "Cache hit %"), ("model_calls", "Model calls"), ("tool_calls", "Tool calls"),
           ("cost_usd", "Cost (USD)"), ("seconds", "Time (s)")]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def fmt(mean, sd, key):
    if mean is None:
        return "n/a"
    if key == "cost_usd":
        return f"{mean:.4f} ± {sd:.4f}"
    if key in ("cache_hit_pct", "seconds"):
        return f"{mean:.1f} ± {sd:.1f}"
    return f"{mean:,.0f} ± {sd:,.0f}"


def results_table(api, sdk):
    rows = ["| Metric (per scenario run) | API | SDK |", "|---|---|---|"]
    for key, label in METRICS:
        cells = [fmt(*stats([r["totals"][key] for r in d["runs"]]), key) for d in (api, sdk)]
        rows.append(f"| {label} | {cells[0]} | {cells[1]} |")
    checks = []
    for d in (api, sdk):
        passed = [sum(c["passed"] for c in r["checks"]) for r in d["runs"]]
        checks.append(f"{statistics.mean(passed):.1f} / {d['mean']['checks_total']} (runs: {passed})")
    rows.append(f"| Checks passed | {checks[0]} | {checks[1]} |")
    rows.append(f"\nRuns: API {len(api['runs'])}, SDK {len(sdk['runs'])}; values are mean ± sample stdev. "
                f"Model {api['model']} / {sdk['model']}, effort {api['effort']} / {sdk['effort']}.")
    return "\n".join(rows)


def checks_table(api, sdk):
    rows = ["| Check | API pass rate | SDK pass rate |", "|---|---|---|"]
    for cid, rate in api["mean"]["check_pass_rate"].items():
        rows.append(f"| {cid} | {rate:.0%} | {sdk['mean']['check_pass_rate'].get(cid, 0):.0%} |")
    return "\n".join(rows)


def loc_table(loc):
    api, sdk = loc["API"], loc["SDK"]
    rows = ["| Harness part | API (code lines) | SDK (`# replaces:` lines) |", "|---|---|---|"]
    for part in [p for p in api["per_file"] if p != "__init__"]:
        sdk_n = sdk["replaces"].get(part)
        rows.append(f"| {part} | {api['per_file'][part]} | {sdk_n if sdk_n is not None else '0 (Claude Code)'} |")
    rows.append(f"| \\_\\_init\\_\\_ (log helper) | {api['per_file']['__init__']} | – |")
    rows.append(f"| **Total** | **{api['total']}** | **{sdk['marked_total']}** |")
    rows.append(f"\nSDK/tools.py total code lines: {sdk['tools_py_total']}; SDK/agent.py: {sdk['agent_py_total']}.")
    return "\n".join(rows)


def per_turn_table(api, sdk):
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens", "model_calls")
    rows = ["| S | Turn | API in / read / write / out / calls | SDK in / read / write / out / calls |",
            "|---|---|---|---|"]
    for a, s in zip(api["mean"]["per_turn"], sdk["mean"]["per_turn"]):
        cell = lambda t: " / ".join(f"{t[k]:,.0f}" for k in keys)
        rows.append(f"| {a['session']} | {a['turn']} | {cell(a)} | {cell(s)} |")
    return "\n".join(rows)


def suspicious(name, d):
    out = []
    for i, run in enumerate(d["runs"], 1):
        t = run["totals"]
        if t["cache_read_input_tokens"] == 0:
            out.append(f"{name} run {i}: cache read is 0 (prefix below the model's minimum cacheable size?)")
        if t["cost_usd"] in (None, 0):
            out.append(f"{name} run {i}: cost is {t['cost_usd']}")
        for row in run["turns"]:
            if row["model_calls"] == 0 or row["output_tokens"] == 0:
                out.append(f"{name} run {i} turn {row['turn']}: model_calls={row['model_calls']} "
                           f"output_tokens={row['output_tokens']}")
            if row["model_calls"] >= 10:
                out.append(f"{name} run {i} turn {row['turn']}: hit the iteration limit ({row['model_calls']} calls)")
    for key in ("input_tokens", "cost_usd", "seconds"):
        mean, sd = stats([r["totals"][key] for r in d["runs"]])
        if mean and sd and sd / mean > 0.3:
            out.append(f"{name}: {key} varies a lot across runs (stdev {sd:,.2f} vs mean {mean:,.2f})")
    return out


def main(api_path, sdk_path):
    api, sdk = load(api_path), load(sdk_path)
    print("## Results\n\n" + results_table(api, sdk) + "\n\n" + checks_table(api, sdk))
    print("\n## Lines of code\n\n" + loc_table(api["loc"] or sdk["loc"]))
    print("\n## Per turn (mean over runs)\n\n" + per_turn_table(api, sdk))
    flags = suspicious("API", api) + suspicious("SDK", sdk)
    print("\n## Suspicious\n\n" + ("\n".join(f"- {f}" for f in flags) if flags else "- none flagged"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:3])
