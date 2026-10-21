"""Persistent memory across sessions.

Long-term memory is the state files in shared/data/runtime (written by the remember/forget tools).
memory_summary() turns them into a short text that a NEW session puts in its system prompt, so the
model knows the household without any earlier messages being replayed. Recipe data is never included.
"""
from __future__ import annotations

from API.harness import log
from shared.tools import _load, get_preferences


def memory_summary() -> str:
    prefs = _load("preferences.json")
    pantry = _load("pantry.json")
    plan = _load("meal_plan.json")
    slots = [f"{day} {meal}" for day, meals in plan.items() for meal in meals]
    lines = [
        "Saved user preferences (from earlier sessions; call get_preferences for the live values): "
        + (get_preferences() if prefs else "none yet."),
        f"Pantry: {len(pantry)} item(s) recorded." if pantry else "Pantry: empty.",
        ("Meal plan has: " + ", ".join(slots) + ".") if slots else "Meal plan: empty.",
    ]
    text = "\n".join(lines)
    log(__file__, f"memory_summary: {len(prefs)} preference key(s), {len(pantry)} pantry item(s), "
                  f"{len(slots)} planned slot(s)")
    return text


def has_memory() -> bool:
    return bool(_load("preferences.json"))
