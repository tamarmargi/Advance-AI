"""Scenario checks: run_checks(results) -> [{id, passed, detail}].

`results` is the dict written by shared/runner.py:
  {"variant", "scenario": {...scenario.json...},
   "sessions": [{"id", "turns": [TurnRecord], "state": {preferences, pantry, meal_plan, shopping_list}}]}
TurnRecord: {"n", "user", "text", "tool_calls": [{"name", "input", "result", "is_error"}],
             "context_user_messages": [str], "memory_loaded": bool}

Usage: python -m shared.checks results/<file>.json   (re-checks a saved run)
"""
from __future__ import annotations

import json
import sys

from shared.errors import RecipeNotFound
from shared.tools import _recipe

MUSHROOM_WORDS = ("mushroom", "פטרי")
EGG_WORDS = ("egg", "ביצ")
DAYS_S1 = ("sunday", "monday", "tuesday")
FAMILY_SIZE = 4  # from scenario turn 1; independent of what the agent stored


# ---------- helpers ----------

def _state(results, session_id: int) -> dict:
    return next(s["state"] for s in results["sessions"] if s["id"] == session_id)


def _final(results) -> dict:
    return results["sessions"][-1]["state"]


def _turns(results):
    return [t for s in results["sessions"] for t in s["turns"]]


def _has(text, words) -> bool:
    text = str(text).lower()
    return any(w in text for w in words)


def _dislikes(prefs: dict) -> list:
    return [v for k, vals in prefs.items() if "dislike" in k
            for v in (vals if isinstance(vals, list) else [vals])]


def _kind(rid):
    try:
        return _recipe(rid)["kind"]
    except RecipeNotFound:
        return None


def _day(plan: dict, day: str) -> dict:
    # tolerate "Sunday" / "ראשון"-style keys by matching case-insensitively
    hebrew = {"sunday": "ראשון", "monday": "שני", "tuesday": "שלישי", "wednesday": "רביעי"}
    for k, v in plan.items():
        if k.lower() == day or hebrew.get(day, "\0") in k:
            return v
    return {}


def _dinner(plan: dict, day: str) -> list:
    meals = _day(plan, day)
    return next((v for k, v in meals.items() if k.lower() in ("dinner", "ערב", "ארוחת ערב")), [])


def _list_map(items) -> dict:
    return {(x["name"], x["unit"]): x["qty"] for x in items}


def _recipe_ids(plan: dict) -> list:
    return [rid for meals in plan.values() for slot in meals.values() for rid in slot]


def _ingredient_keys(rids) -> set:
    keys = set()
    for rid in rids:
        try:
            keys |= {(x["name"].lower(), x["unit"].lower()) for x in _recipe(rid)["ingredients"]}
        except RecipeNotFound:
            pass
    return keys


# ---------- checks ----------

def household_size_s1(r):
    v = _state(r, 1)["preferences"].get("household_size")
    try:
        ok = int(v) == 4
    except (TypeError, ValueError):
        ok = False
    return ok, f"household_size={v!r}"


def mushrooms_disliked_s1(r):
    d = _dislikes(_state(r, 1)["preferences"])
    return any(_has(x, MUSHROOM_WORDS) for x in d), f"dislikes={d}"


def one_dairy_dinner_s1(r):
    plan = _state(r, 1)["meal_plan"]
    dinners = {d: _dinner(plan, d) for d in DAYS_S1}
    kinds = {d: {_kind(x) for x in rids} for d, rids in dinners.items()}
    planned = all(dinners.values())
    dairy_days = [d for d, k in kinds.items() if "dairy" in k]
    return planned and len(dairy_days) == 1, f"dinners={dinners}"


def mushrooms_not_disliked_final(r):
    d = _dislikes(_final(r)["preferences"])
    return not any(_has(x, MUSHROOM_WORDS) for x in d), f"dislikes={d}"


def wednesday_mushroom_dinner(r):
    rids = _dinner(_final(r)["meal_plan"], "wednesday")
    ok = any(_has(name, MUSHROOM_WORDS) for name, _ in _ingredient_keys(rids))
    return ok, f"wednesday dinner={rids}"


def no_meat_dairy_mix(r):
    bad = []
    for s in r["sessions"]:
        for day, meals in s["state"]["meal_plan"].items():
            for meal, rids in meals.items():
                if {"meat", "dairy"} <= {_kind(x) for x in rids}:
                    bad.append(f"s{s['id']}:{day}/{meal}")
    return not bad, f"mixed slots={bad}" if bad else "all slots ok"


def shopping_no_duplicates(r):
    dups = []
    for s in r["sessions"]:
        keys = [(x["name"], x["unit"]) for x in s["state"]["shopping_list"]]
        dups += [f"s{s['id']}:{k}" for k in set(keys) if keys.count(k) > 1]
    nonempty = all(s["state"]["shopping_list"] for s in r["sessions"])
    return nonempty and not dups, f"duplicates={dups}" if dups else f"non-empty={nonempty}"


def eggs_reduced_by_pantry(r):
    st = _state(r, 1)
    servings = FAMILY_SIZE
    needed = 0.0
    for rid in _recipe_ids(st["meal_plan"]):
        try:
            rec = _recipe(rid)
        except RecipeNotFound:
            continue
        needed += sum(x["qty"] for x in rec["ingredients"] if _has(x["name"], EGG_WORDS)) * servings / rec["servings"]
    in_pantry = sum(p["qty"] for p in st["pantry"] if _has(p["name"], EGG_WORDS))
    listed = sum(q for (n, _), q in _list_map(st["shopping_list"]).items() if _has(n, EGG_WORDS))
    expected = max(0.0, needed - in_pantry)
    ok = in_pantry == 6 and abs(listed - expected) < 0.01
    return ok, f"needed={needed:g} pantry={in_pantry:g} listed={listed:g} expected={expected:g}"


def servings_doubled(r):
    s1, fin = _state(r, 1), _final(r)
    added = [x for x in _recipe_ids(fin["meal_plan"]) if x not in _recipe_ids(s1["meal_plan"])]
    touched = _ingredient_keys(added) | {(p["name"], p["unit"]) for p in fin["pantry"]}
    a, b = _list_map(s1["shopping_list"]), _list_map(fin["shopping_list"])
    shared = [k for k in a if k in b and k not in touched]
    off = [f"{k[0]}:{a[k]:g}->{b[k]:g}" for k in shared if abs(b[k] / a[k] - 2) > 0.1]
    return bool(shared) and not off, f"compared={len(shared)} off={off}"


def lookup_product_called(r):
    n = sum(c["name"] == "lookup_product" for t in _turns(r) for c in t["tool_calls"])
    return n >= 1, f"lookup_product calls={n}"


def turn5_from_memory(r):
    t5 = next((t for t in _turns(r) if t["n"] == 5), None)
    if t5 is None:
        return False, "turn 5 missing"
    s1_texts = {t["user"] for t in r["sessions"][0]["turns"]}
    ctx = t5.get("context_user_messages")
    mentions = _has(t5["text"], MUSHROOM_WORDS)
    used_memory = any(c["name"] == "get_preferences" for c in t5["tool_calls"]) or bool(t5.get("memory_loaded"))
    clean = ctx is not None and not any(s in str(m) for m in ctx for s in s1_texts)
    return mentions and used_memory and clean, (
        f"mentions={mentions} memory={used_memory} "
        f"context_clean={clean if ctx is not None else 'not reported'}")


CHECKS = {f.__name__: f for f in (
    household_size_s1, mushrooms_disliked_s1, one_dairy_dinner_s1, mushrooms_not_disliked_final,
    wednesday_mushroom_dinner, no_meat_dairy_mix, shopping_no_duplicates, eggs_reduced_by_pantry,
    servings_doubled, lookup_product_called, turn5_from_memory)}


def run_checks(results: dict) -> list[dict]:
    """Run the checks listed in results['scenario']['checks'] (all CHECKS if absent)."""
    ids = [c["id"] for c in results.get("scenario", {}).get("checks", [])] or list(CHECKS)
    out = []
    for cid in ids:
        fn = CHECKS.get(cid)
        if fn is None:
            out.append({"id": cid, "passed": False, "detail": "check not implemented"})
            continue
        try:
            passed, detail = fn(results)
        except Exception as e:  # a malformed run must fail the check, not crash the report
            passed, detail = False, f"error: {type(e).__name__}: {e}"
        out.append({"id": cid, "passed": bool(passed), "detail": detail})
    return out


def print_checks(checks: list[dict]) -> None:
    for c in checks:
        print(f"[checks.py] {'PASS' if c['passed'] else 'FAIL'} {c['id']}: {c['detail']}")
    print(f"[checks.py] {sum(c['passed'] for c in checks)}/{len(checks)} passed")


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        print_checks(run_checks(json.load(f)))
