"""Saved-state panel: preferences, pantry, meal plan (with kashrut badges) and shopping list."""
from __future__ import annotations

import html

import streamlit as st

from shared.errors import RecipeNotFound
from shared.runner import snapshot_state
from shared.tools import _recipe

DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
DAY_HE = dict(zip(DAYS, ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]))
MEAL_HE = {"breakfast": "בוקר", "lunch": "צהריים", "dinner": "ערב"}
KIND = {"meat": ("בשרי", "#c0392b"), "dairy": ("חלבי", "#2471a3"), "parve": ("פרווה", "#1e8449")}


def _badge(rid: str) -> str:
    try:
        r = _recipe(rid)
    except RecipeNotFound:
        return f"{html.escape(rid)} <span style='color:#888'>(לא נמצא)</span>"
    label, color = KIND[r["kind"]]
    return (f"{html.escape(r['name'])} <span style='background:{color};color:white;border-radius:8px;"
            f"padding:0 6px;font-size:0.8em'>{label}</span>")


def meal_plan_html(plan: dict) -> str:
    days = sorted(plan, key=lambda d: DAYS.index(d) if d in DAYS else len(DAYS))
    meals = [m for m in MEAL_HE if any(m in plan[d] for d in days)]
    meals += sorted({m for d in days for m in plan[d]} - set(meals))
    head = "".join(f"<th>{MEAL_HE.get(m, html.escape(m))}</th>" for m in meals)
    rows = []
    for d in days:
        cells = "".join("<td>" + "<br>".join(_badge(r) for r in plan[d].get(m, [])) + "</td>" for m in meals)
        rows.append(f"<tr><th>{DAY_HE.get(d, html.escape(d))}</th>{cells}</tr>")
    return (f"<table style='width:100%'><tr><th>יום</th>{head}</tr>{''.join(rows)}</table>")


def render_state(key: str) -> None:
    state = snapshot_state()

    st.markdown("#### העדפות")
    prefs = state["preferences"]
    if prefs:
        st.table([{"מפתח": k, "ערך": ", ".join(map(str, v)) if isinstance(v, list) else str(v)}
                  for k, v in prefs.items()])
    else:
        st.caption("אין העדפות שמורות.")

    st.markdown("#### מזווה")
    if state["pantry"]:
        st.table([{"פריט": p["name"], "כמות": p["qty"], "יחידה": p["unit"]} for p in state["pantry"]])
    else:
        st.caption("המזווה ריק.")

    st.markdown("#### תפריט")
    if state["meal_plan"]:
        st.markdown(meal_plan_html(state["meal_plan"]), unsafe_allow_html=True)
    else:
        st.caption("התפריט ריק.")

    st.markdown("#### רשימת קניות")
    if state["shopping_list"]:
        st.dataframe([{"פריט": x["name"], "כמות": x["qty"], "יחידה": x["unit"]} for x in state["shopping_list"]],
                     hide_index=True, width="stretch", key=f"{key}_shopping")
    else:
        st.caption("רשימת הקניות ריקה.")
