"""Tests for the plain-Python domain layer in shared/ (no network, no real state)."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from shared import tools
from shared.checks import CHECKS, run_checks
from shared.errors import InvalidInput, KashrutConflict, RecipeNotFound
from shared.runner import snapshot_state
from shared.tool_specs import TOOL_SPECS


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTRY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PANTRY_OFFLINE", "1")
    tools.reset_state()
    return tmp_path


def _shopping(state):
    return json.loads((state / "shopping_list.json").read_text(encoding="utf-8"))


# ---------- data & specs ----------

def test_no_llm_dependencies():
    # fresh interpreter: other test modules import anthropic into this process
    code = ("import sys, shared.tools, shared.tool_specs, shared.errors, shared.checks, shared.runner; "
            "sys.exit(any(m in sys.modules for m in ('anthropic', 'claude_agent_sdk')))")
    assert subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parents[1]).returncode == 0


def test_recipe_data_constraints():
    recipes = tools._all_recipes()
    assert [r["id"] for r in recipes] == [f"r{i:02d}" for i in range(1, 21)]
    kinds = [r["kind"] for r in recipes]
    assert kinds.count("meat") == 6 and kinds.count("dairy") >= 4
    assert sum(any("mushroom" in x["name"] for x in r["ingredients"]) for r in recipes) >= 3
    for r in recipes:
        assert set(r) == {"id", "name", "tags", "kind", "servings", "ingredients"}
        assert all(set(x) == {"name", "qty", "unit"} for x in r["ingredients"])


def test_specs_match_tools():
    assert [s["name"] for s in TOOL_SPECS] == list(tools.TOOLS)


# ---------- search / get ----------

@pytest.mark.parametrize("n", [1, 3, 5, 10])
def test_search_respects_max_results(n):
    assert len(tools.search_recipes(max_results=n).splitlines()) == n


@pytest.mark.parametrize("kind", ["meat", "dairy", "parve"])
def test_search_respects_kind(kind):
    lines = tools.search_recipes(kind=kind, max_results=20).splitlines()
    assert lines and all(line.endswith(f"({kind})") for line in lines)


def test_search_respects_exclude_ingredients():
    lines = tools.search_recipes(exclude_ingredients=["Mushroom"], max_results=20).splitlines()
    assert len(lines) == 16
    excluded = {"r03", "r08", "r09", "r20"}
    assert not excluded & {line.split()[0] for line in lines}


def test_search_query_and_errors():
    assert [line.split()[0] for line in tools.search_recipes("soup", max_results=10).splitlines()] == \
        ["r05", "r17", "r20"]
    assert tools.search_recipes("zzz") == "No recipes found."
    with pytest.raises(InvalidInput):
        tools.search_recipes(kind="fish")


def test_get_recipe():
    assert tools.get_recipe("r03").startswith("r03 Beef and Mushroom Stew (meat, serves 6)")
    with pytest.raises(RecipeNotFound):
        tools.get_recipe("r99")


def test_outputs_stay_under_length_cap():
    assert len(tools.search_recipes(max_results=20)) <= tools.MAX_OUT
    for i in range(1, 21):
        tools.save_meal_plan(f"day{i}", "dinner", f"r{i:02d}")
        assert len(tools.get_recipe(f"r{i:02d}")) <= tools.MAX_OUT
    out = tools.build_shopping_list(4)
    assert len(out) == tools.MAX_OUT and out.endswith("…")
    for i in range(30):
        tools.remember("dislikes", f"ingredient number {i}")
    assert len(tools.get_preferences()) <= tools.MAX_OUT


# ---------- meal plan ----------

def test_kashrut_conflict_same_slot_only():
    tools.save_meal_plan("Sunday", "Dinner", "r01")      # meat
    tools.save_meal_plan("sunday", "dinner", "r12")      # parve is fine
    with pytest.raises(KashrutConflict):
        tools.save_meal_plan("sunday", "dinner", "r09")  # dairy
    tools.save_meal_plan("sunday", "lunch", "r09")       # other meal is fine
    with pytest.raises(KashrutConflict):
        tools.save_meal_plan("sunday", "lunch", "r05")   # meat into dairy slot
    plan = tools._load("meal_plan.json")
    assert plan == {"sunday": {"dinner": ["r01", "r12"], "lunch": ["r09"]}}


def test_save_meal_plan_recipe_not_found():
    with pytest.raises(RecipeNotFound):
        tools.save_meal_plan("monday", "lunch", "nope")
    assert tools._load("meal_plan.json") == {}


def test_remove_from_meal_plan():
    tools.save_meal_plan("monday", "lunch", "r11")
    assert "Removed" in tools.remove_from_meal_plan("Monday", "Lunch")
    assert tools.remove_from_meal_plan("monday", "lunch").startswith("Nothing planned")
    assert tools._load("meal_plan.json") == {}


# ---------- pantry & shopping list ----------

def test_shopping_list_scales_merges_and_subtracts(state):
    tools.save_meal_plan("sunday", "dinner", "r11")   # shakshuka: 6 eggs, 800 g tomatoes / 4 servings
    tools.save_meal_plan("monday", "lunch", "r18")    # sabich: 4 eggs / 4 servings
    tools.save_meal_plan("monday", "dinner", "r16")   # stuffed peppers: 400 g tomatoes / 6 servings
    tools.update_pantry("Eggs", 5, "unit")
    tools.update_pantry("eggs", 5, "unit")            # set, not add: still 5
    tools.build_shopping_list(8)
    items = _shopping(state)
    keys = [(x["name"], x["unit"]) for x in items]
    assert len(keys) == len(set(keys))                # duplicates merged
    q = dict(zip(keys, (x["qty"] for x in items)))
    assert q[("eggs", "unit")] == 12 + 8 - 5          # scaled x2 each, merged, minus pantry
    assert q[("crushed tomatoes", "g")] == pytest.approx(1600 + 400 * 8 / 6, abs=0.01)


def test_shopping_list_drops_items_fully_in_pantry(state):
    tools.save_meal_plan("sunday", "dinner", "r15")   # majadra: 400 g rice
    tools.update_pantry("rice", 1000, "g")
    tools.build_shopping_list(6)
    assert ("rice", "g") not in {(x["name"], x["unit"]) for x in _shopping(state)}


def test_shopping_list_errors():
    assert "empty" in tools.build_shopping_list(4)
    with pytest.raises(InvalidInput):
        tools.build_shopping_list(0)
    with pytest.raises(InvalidInput):
        tools.update_pantry("eggs", -1, "unit")


# ---------- preferences ----------

def test_remember_forget_list_keys_no_duplicates():
    tools.remember("dislikes", "mushrooms")
    tools.remember("Dislikes", "mushrooms")
    tools.remember("dislikes", "olives")
    tools.remember("household_size", 4)
    assert tools._load("preferences.json") == {"dislikes": ["mushrooms", "olives"], "household_size": 4}
    tools.forget("dislikes", "mushrooms")
    assert tools._load("preferences.json")["dislikes"] == ["olives"]
    assert "not in" in tools.forget("dislikes", "mushrooms")
    tools.forget("household_size")
    assert tools._load("preferences.json") == {"dislikes": ["olives"]}
    assert tools.forget("nothing").startswith("No preference")


# ---------- external ----------

def test_lookup_product_offline_returns_stub(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("network used while PANTRY_OFFLINE=1")
    monkeypatch.setattr(tools.requests, "get", no_network)
    assert tools.lookup_product("Raw Tahini").startswith("(offline) Raw Tahini — Har Bracha")
    assert tools.lookup_product("something odd").startswith("(offline) Unknown product")


def test_lookup_product_falls_back_on_network_error(monkeypatch):
    monkeypatch.delenv("PANTRY_OFFLINE")

    def fail(*a, **kw):
        raise tools.requests.ConnectionError("down")
    monkeypatch.setattr(tools.requests, "get", fail)
    assert tools.lookup_product("tahini").startswith("(offline)")


# ---------- reset ----------

def test_reset_state_wipes_everything(state):
    tools.remember("household_size", 4)
    tools.update_pantry("eggs", 6, "unit")
    tools.save_meal_plan("sunday", "dinner", "r01")
    tools.build_shopping_list(4)
    tools.reset_state()
    assert {p.name for p in state.iterdir()} == set(tools.STATE_FILES)
    assert snapshot_state() == {"preferences": {}, "pantry": [], "meal_plan": {}, "shopping_list": []}


# ---------- checks on hand-built results ----------

S1_USER = ["s1 turn 1", "s1 turn 2", "s1 turn 3", "s1 turn 4"]


def _turn(n, user, text="ok", calls=(), ctx=None):
    return {"n": n, "user": user, "text": text,
            "tool_calls": [{"name": c, "input": {}, "result": "", "is_error": False} for c in calls],
            "context_user_messages": ctx if ctx is not None else [user], "memory_loaded": False}


def _good_results():
    tools.reset_state()
    tools.remember("household_size", 4)
    tools.remember("dislikes", "פטריות")
    tools.save_meal_plan("sunday", "dinner", "r02")   # meat
    tools.save_meal_plan("monday", "dinner", "r07")   # dairy
    tools.save_meal_plan("tuesday", "dinner", "r11")  # parve
    tools.update_pantry("eggs", 6, "unit")
    tools.build_shopping_list(4)
    s1 = snapshot_state()
    tools.forget("dislikes", "פטריות")
    tools.save_meal_plan("wednesday", "dinner", "r20")
    tools.build_shopping_list(8)
    s2 = snapshot_state()
    return {
        "variant": "HAND",
        "sessions": [
            {"id": 1, "state": s1, "turns": [
                _turn(i + 1, u, calls=["lookup_product"] if i == 3 else ()) for i, u in enumerate(S1_USER)]},
            {"id": 2, "state": s2, "turns": [
                _turn(5, "s2 turn 5", text="אתם לא אוהבים פטריות", calls=["get_preferences"]),
                _turn(6, "s2 turn 6"), _turn(7, "s2 turn 7")]},
        ],
    }


def _failed(results):
    return {c["id"] for c in run_checks(results) if not c["passed"]}


def test_run_checks_good_results_pass():
    checks = run_checks(_good_results())
    assert [c["id"] for c in checks] == list(CHECKS)
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]


def test_run_checks_bad_results_fail():
    bad = copy.deepcopy(_good_results())
    s1, s2 = bad["sessions"][0]["state"], bad["sessions"][1]["state"]
    s1["preferences"]["household_size"] = 2
    s2["preferences"]["dislikes"] = ["mushrooms"]
    s2["meal_plan"]["sunday"]["dinner"].append("r09")          # dairy next to meat
    s2["shopping_list"].append(dict(s2["shopping_list"][0]))   # duplicate name+unit
    for t in bad["sessions"][0]["turns"]:
        t["tool_calls"] = []                                    # no lookup_product
    bad["sessions"][1]["turns"][0]["context_user_messages"] = S1_USER + ["s2 turn 5"]  # leaked history
    assert _failed(bad) == {"household_size_s1", "mushrooms_not_disliked_final", "no_meat_dairy_mix",
                            "shopping_no_duplicates", "lookup_product_called", "turn5_from_memory"}


def test_run_checks_quantity_failures():
    bad = copy.deepcopy(_good_results())
    for x in bad["sessions"][0]["state"]["shopping_list"]:
        if x["name"] == "eggs":
            x["qty"] = 7.33                                     # pantry not subtracted
    for x in bad["sessions"][1]["state"]["shopping_list"]:
        if x["name"] == "potatoes":
            x["qty"] /= 2                                       # not scaled to 8
    assert _failed(bad) == {"eggs_reduced_by_pantry", "servings_doubled"}


def test_run_checks_malformed_results_fail_not_crash():
    checks = run_checks({"sessions": [{"id": 1, "turns": [], "state": {}}]})
    assert checks and not any(c["passed"] for c in checks)
