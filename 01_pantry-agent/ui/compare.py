"""Comparison tab: results/api.json vs results/sdk.json, per-turn charts and lines of code."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from scripts.count_loc import count_loc
from shared.runner import ROOT

FILES = {"API": ROOT / "results" / "api.json", "SDK": ROOT / "results" / "sdk.json"}
METRICS = {"input_tokens": "קלט", "cache_read_input_tokens": "קריאה מהמטמון",
           "cache_creation_input_tokens": "כתיבה למטמון", "output_tokens": "פלט",
           "cache_hit_pct": "פגיעות מטמון %", "model_calls": "קריאות למודל", "cost_usd": "עלות ($)",
           "seconds": "זמן (שניות)"}
TURN_METRICS = ["input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens",
                "model_calls", "cost_usd", "seconds"]


def _load() -> dict:
    return {v: json.loads(p.read_text(encoding="utf-8")) for v, p in FILES.items() if p.exists()}


def render_compare() -> None:
    data = _load()
    missing = [v for v in FILES if v not in data]
    if missing:
        st.info("חסר קובץ תוצאות: " + ", ".join(str(FILES[v].relative_to(ROOT)) for v in missing)
                + ". מריצים: python API/main.py / python SDK/main.py עם --runs 3")
    if data:
        st.markdown("#### ממוצע לריצת תרחיש")
        table = {METRICS[k]: {v: d["mean"]["totals"].get(k) for v, d in data.items()} for k in METRICS}
        table["בדיקות שעברו"] = {v: f"{d['mean']['checks_passed']:g} / {d['mean']['checks_total']}"
                                 for v, d in data.items()}
        table["מספר ריצות"] = {v: d["mean"]["runs"] for v, d in data.items()}
        st.table(pd.DataFrame(table).T.astype(str))

        st.markdown("#### לפי תור (ממוצע ריצות)")
        metric = st.selectbox("מדד", TURN_METRICS, format_func=lambda k: METRICS[k])
        frame = pd.DataFrame({v: {f"ס{t['session']}·ת{t['turn']}": t[metric] for t in d["mean"]["per_turn"]}
                              for v, d in data.items()})
        st.bar_chart(frame, stack=False)

    st.markdown("#### שורות קוד לכל חלק ב-harness")
    loc = next((d["loc"] for d in data.values() if d.get("loc")), None) or count_loc()
    parts = [p for p in loc["API"]["per_file"] if p != "__init__"]
    st.table(pd.DataFrame({
        "חלק": parts,
        "API (שורות קוד)": [loc["API"]["per_file"][p] for p in parts],
        "SDK (שורות # replaces)": [loc["SDK"]["replaces"].get(p, 0) for p in parts],
    }).set_index("חלק"))
    st.caption(f"סה\"כ: API {loc['API']['total']} · SDK {loc['SDK']['marked_total']} "
               f"(SDK/tools.py: {loc['SDK']['tools_py_total']}, SDK/agent.py: {loc['SDK']['agent_py_total']})")
