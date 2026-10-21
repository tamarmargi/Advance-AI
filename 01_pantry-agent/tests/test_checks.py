"""Tests for shared/runner.py + shared/checks.py using a scripted (non-LLM) agent."""
import json

import pytest

from shared import tools
from scripts.count_loc import count_loc
from shared.runner import SCENARIO, run_many, run_scenario

GOOD_SCRIPT = {
    1: ([("remember", {"key": "household_size", "value": 4}),
         ("remember", {"key": "kashrut", "value": "strict"}),
         ("remember", {"key": "dislikes", "value": "mushrooms"})], "Saved."),
    2: ([("save_meal_plan", {"day": "sunday", "meal": "dinner", "recipe_id": "r02"}),
         ("save_meal_plan", {"day": "monday", "meal": "dinner", "recipe_id": "r07"}),
         ("save_meal_plan", {"day": "tuesday", "meal": "dinner", "recipe_id": "r11"})], "Planned."),
    3: ([("update_pantry", {"item": "eggs", "qty": 6, "unit": "unit"}),
         ("update_pantry", {"item": "rice", "qty": 1000, "unit": "g"})], "Noted."),
    4: ([("build_shopping_list", {"servings": 4}),
         ("lookup_product", {"name": "raw tahini"})], "Here is the list."),
    5: ([("get_preferences", {})], "You don't like mushrooms."),
    6: ([("forget", {"key": "dislikes", "value": "mushrooms"}),
         ("save_meal_plan", {"day": "wednesday", "meal": "dinner", "recipe_id": "r20"})], "Done."),
    7: ([("build_shopping_list", {"servings": 8})], "Updated for 8."),
}


class ScriptedAgent:
    def __init__(self, script):
        self.script, self.history = script, []

    def send(self, text):
        self.history.append(text)
        n = next(n for n, t in TURN_TEXTS.items() if t == text)
        calls, reply = self.script[n]
        records = [{"name": name, "input": args, "result": tools.TOOLS[name](**args), "is_error": False}
                   for name, args in calls]
        return {"text": reply, "tool_calls": records,
                "context_user_messages": list(self.history), "memory_loaded": False}


SCENARIO_DATA = json.loads(SCENARIO.read_text(encoding="utf-8"))
TURN_TEXTS = {t["n"]: t["user"] for s in SCENARIO_DATA["sessions"] for t in s["turns"]}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTRY_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("PANTRY_OFFLINE", "1")


def _run(tmp_path, script, factory=None):
    factory = factory or (lambda model, effort: ScriptedAgent(script))
    res = run_scenario(factory, "TEST", SCENARIO_DATA)
    return {c["id"]: c for c in res["checks"]}, res


def test_scenario_shape():
    assert [len(s["turns"]) for s in SCENARIO_DATA["sessions"]] == [4, 3]
    assert len(SCENARIO_DATA["checks"]) >= 7


def test_good_run_passes_all(tmp_path):
    checks, res = _run(tmp_path, GOOD_SCRIPT)
    assert all(c["passed"] for c in checks.values()), {k: c["detail"] for k, c in checks.items() if not c["passed"]}
    assert [s["id"] for s in res["sessions"]] == [1, 2] and "state" in res["sessions"][0]


def test_run_many_writes_runs_means_and_loc(tmp_path):
    usage = {"input_tokens": 100, "cache_read_input_tokens": 300, "cache_creation_input_tokens": 100,
             "output_tokens": 10, "cost_usd": 0.01}

    class Metered(ScriptedAgent):
        def send(self, text):
            return {**super().send(text), "usage": usage, "model_calls": 2}

    resets = []
    out = run_many(lambda m, e: Metered(GOOD_SCRIPT), "TEST", SCENARIO, tmp_path / "r.json", runs=2,
                   reset=lambda: resets.append(1), loc={"x": 1})
    saved = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert saved["mean"] == out["mean"] and len(saved["runs"]) == 2 and resets == [1, 1]
    row = saved["runs"][0]["turns"][0]
    assert set(row) == {"session", "turn", "user", "answer", "input_tokens", "cache_read_input_tokens",
                        "cache_creation_input_tokens", "output_tokens", "model_calls", "tools", "cost_usd", "seconds"}
    assert (row["session"], row["turn"], row["tools"]) == (1, 1, ["remember", "remember", "remember"])
    m = saved["mean"]
    assert m["totals"]["cache_hit_pct"] == 60.0                    # 300 / (100 + 300 + 100)
    assert m["totals"]["cost_usd"] == pytest.approx(0.07) and m["totals"]["model_calls"] == 14
    assert m["checks_passed"] == m["checks_total"] == 11 and set(m["check_pass_rate"].values()) == {1.0}
    assert len(m["per_turn"]) == 7 and saved["loc"] == {"x": 1}


def test_count_loc():
    loc = count_loc()
    assert set(loc["API"]["per_file"]) == {"__init__", "loop", "tools", "history", "memory", "context",
                                           "compaction", "caching", "tokens"}
    assert all(n > 0 for n in loc["API"]["per_file"].values())
    assert {"tools", "loop", "memory", "history", "tokens"} <= set(loc["SDK"]["replaces"])
    assert loc["SDK"]["tools_py_total"] > 0


def test_bad_run_fails_expected_checks(tmp_path):
    bad = dict(GOOD_SCRIPT)
    bad[5] = ([], "You don't like mushrooms.")                     # answered without memory
    bad[6] = ([("save_meal_plan", {"day": "wednesday", "meal": "dinner", "recipe_id": "r20"})], "Done.")
    bad[7] = ([("build_shopping_list", {"servings": 4})], "Updated.")
    checks, _ = _run(tmp_path, bad)
    failed = {k for k, c in checks.items() if not c["passed"]}
    assert failed == {"turn5_from_memory", "mushrooms_not_disliked_final", "servings_doubled"}


def test_leaked_history_fails_turn5(tmp_path):
    shared_history = []

    def factory(model, effort):  # agent that wrongly keeps session-1 history across sessions
        a = ScriptedAgent(GOOD_SCRIPT)
        a.history = shared_history
        return a

    checks, _ = _run(tmp_path, GOOD_SCRIPT, factory)
    assert not checks["turn5_from_memory"]["passed"]
