"""Offline tests for the raw-API harness (API/harness + API/agent.py).

FakeClient mimics client.messages.create and returns scripted anthropic Message objects
(.content blocks, .stop_reason, .usage). No API key, no network.
"""
import copy
import json

import pytest
from anthropic.types import Message

from API.agent import Agent
from API.harness import caching, compaction, context, history, loop, tokens
from API.harness.tools import build_tools, dispatch
from shared import tools as shared_tools
from shared.tool_specs import TOOL_SPECS

MODEL = "claude-opus-5-5"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PANTRY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PANTRY_OFFLINE", "1")
    shared_tools.reset_state()


# ---------- fakes ----------

def usage(inp=100, out=20, read=0, write=0):
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write}


def msg(stop_reason, *blocks, u=None):
    return Message.model_validate({
        "id": "msg_x", "type": "message", "role": "assistant", "model": MODEL,
        "stop_reason": stop_reason, "stop_sequence": None, "content": list(blocks), "usage": u or usage()})


def tool_use(id_, name, **inp):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def text(t):
    return {"type": "text", "text": t}


def thinking():
    return {"type": "thinking", "thinking": "", "signature": "sig123"}


class FakeClient:
    """client.messages.create(**kwargs) -> next scripted response; records deep copies of every request."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        if not self.responses:
            raise AssertionError("FakeClient ran out of scripted responses")
        return self.responses.pop(0)


def run(responses, **kw):
    client = FakeClient(responses)
    req = {**loop.base_request(MODEL, "medium"), "tools": build_tools(),
           "messages": [{"role": "user", "content": [text("hi")]}]}
    return client, loop.run_loop(client, req, **kw)


def lines(session_id):
    p = history.history_path(session_id)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


# ---------- tools ----------

def test_build_tools_from_specs():
    tools = build_tools()
    assert [t["name"] for t in tools] == [s["name"] for s in TOOL_SPECS]
    assert set(tools[0]) == {"name", "description", "input_schema"}
    tools[0]["input_schema"]["x"] = 1
    assert "x" not in TOOL_SPECS[0]["input_schema"]  # copies, not the shared specs


@pytest.mark.parametrize("name,inp,needle", [
    ("get_recipe", {"recipe_id": "r99"}, "RecipeNotFound"),
    ("save_meal_plan", {"day": "sunday", "meal": "dinner"}, "Bad arguments"),
    ("nope", {}, "Unknown tool"),
    ("build_shopping_list", {"servings": "many"}, "ValueError"),
])
def test_dispatch_never_raises(name, inp, needle):
    out, is_error = dispatch(name, inp)
    assert is_error and needle in out and len(out) <= 300


# ---------- loop ----------

def test_stops_on_end_turn_and_parallel_results_in_one_message(capsys):
    client, res = run([
        msg("tool_use", thinking(), text("checking"), tool_use("a", "get_recipe", recipe_id="r01"),
            tool_use("b", "search_recipes", query="soup")),
        msg("end_turn", text("done")),
    ])
    assert res.text == "done" and res.stop_reason == "end_turn"
    assert res.model_calls == len(client.requests) == 2
    sent = client.requests[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == thinking()                 # thinking block replayed unchanged
    results = sent[2]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"]
    assert [b["tool_use_id"] for b in results] == ["a", "b"]
    assert not any(b.get("is_error") for b in results)
    out = capsys.readouterr().out
    for tag in ("[loop.py]", "[tools.py]", "[tokens.py]"):
        assert tag in out


def test_raising_tool_returns_is_error_and_loop_continues(monkeypatch):
    def boom(**kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setitem(shared_tools.TOOLS, "get_preferences", boom)
    client, res = run([
        msg("tool_use", tool_use("a", "get_preferences"), tool_use("b", "get_recipe", recipe_id="r02")),
        msg("end_turn", text("recovered")),
    ])
    results = client.requests[1]["messages"][2]["content"]
    assert results[0]["is_error"] is True and "disk on fire" in results[0]["content"]
    assert "is_error" not in results[1]
    assert res.text == "recovered" and res.model_calls == 2
    assert [t["is_error"] for t in res.tools_called] == [True, False]


def test_effort_and_model_on_every_call():
    client, _ = run([msg("tool_use", tool_use("a", "get_preferences")), msg("end_turn", text("ok"))])
    assert all(r["model"] == MODEL and r["output_config"] == {"effort": "medium"} for r in client.requests)


def test_max_iterations():
    client, res = run([msg("tool_use", tool_use(f"t{i}", "get_preferences")) for i in range(12)])
    assert res.model_calls == loop.MAX_ITERATIONS == len(client.requests)
    assert res.text.startswith("(stopped") and res.new_messages[-1]["content"][0]["type"] == "tool_result"


def test_refusal():
    _, res = run([msg("refusal")])
    assert res.stop_reason == "refusal" and "declined" in res.text


# ---------- history ----------

def test_history_only_grows_across_turns():
    client = FakeClient([
        msg("tool_use", tool_use("a", "remember", key="household_size", value=4)), msg("end_turn", text("1")),
        msg("end_turn", text("2")),
        msg("tool_use", tool_use("b", "get_preferences"), tool_use("c", "get_recipe", recipe_id="r01")),
        msg("end_turn", text("3")),
    ])
    agent = Agent(MODEL, "medium", client=client, session_id="grow")
    snapshots = []
    for t in ("first", "שני", "third"):
        agent.send(t)
        snapshots.append(lines("grow"))
    for before, after in zip(snapshots, snapshots[1:]):
        assert len(after) > len(before) and after[:len(before)] == before   # earlier lines unchanged
    assert [json.loads(x)["message"]["role"] for x in snapshots[-1]] == \
        ["user", "assistant", "user", "assistant", "user", "assistant", "user", "assistant", "user", "assistant"]
    assert history.load("grow") == agent.messages
    assert "cache_control" not in "".join(snapshots[-1])           # breakpoints never persisted


# ---------- context ----------

def _tool_round_history():
    return [
        {"role": "user", "content": [text("plan dinner")]},
        {"role": "assistant", "content": [tool_use("a", "get_recipe", recipe_id="r03")]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a",
                                      "content": shared_tools.get_recipe("r03")}]},
        {"role": "assistant", "content": [text("planned")]},
        {"role": "user", "content": [text("thanks")]},
    ]


def test_context_never_splits_tool_pairs():
    h = _tool_round_history()
    assert context.build_messages(h) == h
    for upto in range(len(h)):                     # any cut point -> starts at a real user turn
        window = context.build_messages(h, {"upto": upto, "summary": "s"})
        assert window and context.is_turn_start(window[0])
        context.check_pairs(window)
    with pytest.raises(ValueError):
        context.check_pairs(h[:2])                 # tool_use without its tool_result
    with pytest.raises(ValueError):
        context.check_pairs(h[2:])                 # starts with a tool_result


def test_context_merges_user_after_max_iteration_stop():
    h = _tool_round_history()[:3] + [{"role": "user", "content": [text("next")]}]
    window = context.build_messages(h)
    assert [m["role"] for m in window] == ["user", "assistant", "user"]
    assert [b["type"] for b in window[2]["content"]] == ["tool_result", "text"]


def test_context_never_includes_recipe_data():
    shared_tools.remember("household_size", 4)
    shared_tools.save_meal_plan("sunday", "dinner", "r03")
    system = context.build_system(Agent(MODEL, "low", client=FakeClient([])).memory_summary())
    sys_text = " ".join(b["text"] for b in system)
    for r in shared_tools._all_recipes():
        assert r["name"] not in sys_text and r["id"] not in sys_text
        assert not any(f"{i['name']} {i['qty']}" in sys_text for i in r["ingredients"])
    # nothing is injected into messages either, and after compaction old recipe results are gone
    h = _tool_round_history()
    assert context.build_messages(h) == h
    compacted = json.dumps(context.build_messages(h + [{"role": "user", "content": [text("new")]}],
                                                  {"upto": len(h), "summary": "planned a stew"}))
    assert "Beef and Mushroom Stew" not in compacted and "beef chuck" not in compacted


# ---------- compaction ----------

def test_compaction_threshold_and_history_untouched():
    client = FakeClient([
        msg("end_turn", text("a"), u=usage(inp=400, read=500)),     # prompt 900 <= 1000 -> no compaction
        msg("end_turn", text("b"), u=usage(inp=600, read=500)),     # prompt 1100 > 1000 -> compact next turn
        msg("end_turn", text("SUMMARY: family of 4")),               # the compaction call
        msg("end_turn", text("c")),
    ])
    agent = Agent(MODEL, "medium", client=client, session_id="cmp", compaction_threshold=1000)
    agent.send("turn one")
    agent.send("turn two")
    assert len(client.requests) == 2                                 # below threshold: no summary call
    before = history.history_path("cmp").read_bytes()
    agent._maybe_compact()
    assert history.history_path("cmp").read_bytes() == before        # compaction never edits history.jsonl
    assert client.requests[2]["system"] == compaction.SUMMARIZER_PROMPT
    assert compaction.load_latest("cmp") == {"upto": 4, "summary": "SUMMARY: family of 4"}

    agent.compaction_threshold = 10**9                               # don't compact again
    agent.send("turn three")
    sent = client.requests[3]["messages"]
    assert len(sent) == 1                                            # summary + new turn only
    blocks = sent[0]["content"]
    assert blocks[0]["text"].startswith(context.SUMMARY_PREFIX) and blocks[1]["text"] == "turn three"
    assert "turn one" not in json.dumps(sent)
    assert len(history.load("cmp")) == 6 and history.history_path("cmp").read_bytes().startswith(before)


def test_should_compact_boundary():
    assert not compaction.should_compact(1000, 1000) and compaction.should_compact(1001, 1000)


# ---------- caching ----------

def test_cache_control_on_expected_blocks_only():
    client = FakeClient([
        msg("tool_use", tool_use("a", "get_preferences"), tool_use("b", "get_recipe", recipe_id="r01")),
        msg("end_turn", text("ok")),
        msg("end_turn", text("ok2")),
    ])
    agent = Agent(MODEL, "medium", client=client, session_id="cache")
    agent.send("one")
    agent.send("two")
    for req in client.requests:
        assert not any("cache_control" in t for t in req["tools"])
        assert all(b["cache_control"] == {"type": "ephemeral"} for b in req["system"]) and len(req["system"]) == 2
        marked = [(i, j) for i, m in enumerate(req["messages"]) for j, b in enumerate(m["content"])
                  if "cache_control" in b]
        last = len(req["messages"]) - 1
        assert marked == [(last, len(req["messages"][last]["content"]) - 1)]
        assert caching.count_breakpoints(req["tools"], req["system"], req["messages"]) == 3 <= caching.MAX_BREAKPOINTS
    # call 2 of turn 1 marks the tool_result message, not the user text any more
    assert client.requests[1]["messages"][-1]["content"][-1]["type"] == "tool_result"
    assert "cache_control" not in client.requests[1]["messages"][0]["content"][0]


def test_breakpoint_limit_enforced():
    too_many = [{"role": "user", "content": [dict(text(str(i)), cache_control={"type": "ephemeral"})
                                             for i in range(3)]}]
    with pytest.raises(ValueError):
        caching.check_limit([], caching.mark_system([text("a"), text("b")]), too_many)
    # mark_messages strips stale breakpoints so the limit holds
    assert caching.count_breakpoints([], [], caching.mark_messages(too_many)) == 1


# ---------- tokens ----------

@pytest.mark.parametrize("u,expected", [
    (usage(inp=1_000_000, out=0), 4.00),
    (usage(inp=0, out=1_000_000), 20.00),
    (usage(inp=0, out=0, read=1_000_000), 0.20),
    (usage(inp=0, out=0, write=1_000_000), 5.00),
    (usage(inp=2_000, out=500, read=10_000, write=3_000), (2_000 * 4 + 500 * 20 + 10_000 * 0.2 + 3_000 * 5) / 1e6),
])
def test_cost_known_numbers(u, expected):
    rec = tokens.record_call(msg("end_turn", u=u).usage, MODEL)
    assert rec["cost_usd"] == pytest.approx(expected)


def test_aggregate_and_unknown_model():
    a = tokens.record_call(msg("end_turn", u=usage(inp=1000, out=100)).usage, MODEL)
    b = tokens.record_call(msg("end_turn", u=usage(inp=0, out=0, read=1000)).usage, MODEL)
    total = tokens.aggregate([a, b], MODEL)
    assert total["calls"] == 2 and total["input_tokens"] == 1000 and total["cache_read_input_tokens"] == 1000
    assert total["cost_usd"] == pytest.approx(0.004 + 0.002 + 0.0002)
    partial = Message.model_validate({**msg("end_turn").model_dump(), "usage": {"input_tokens": 5, "output_tokens": 1}})
    assert tokens.record_call(partial.usage, "other-model")["cost_usd"] is None


# ---------- memory across sessions ----------

def test_fresh_agent_sees_session1_preferences_via_memory():
    s1 = FakeClient([
        msg("tool_use", tool_use("a", "remember", key="household_size", value=4),
            tool_use("b", "remember", key="dislikes", value="פטריות")),
        msg("end_turn", text("שמרתי")),
    ])
    Agent(MODEL, "medium", client=s1, session_id="s1").send("אנחנו 4 ולא אוהבים פטריות")

    s2 = FakeClient([msg("end_turn", text("לא אוהבים פטריות"))])
    agent2 = Agent(MODEL, "medium", client=s2, session_id="s2")
    summary = agent2.memory_summary()
    assert '"household_size":4' in summary and "פטריות" in summary
    rec = agent2.send("מה אני לא אוהב?")
    req = s2.requests[0]
    assert summary in req["system"][1]["text"]                       # memory reached the model
    assert context.user_texts(req["messages"]) == ["מה אני לא אוהב?"]  # no session-1 messages
    assert rec["memory_loaded"] is True and rec["context_user_messages"] == ["מה אני לא אוהב?"]


def test_system_frozen_within_session():
    client = FakeClient([
        msg("tool_use", tool_use("a", "remember", key="household_size", value=8)), msg("end_turn", text("ok")),
        msg("end_turn", text("ok")),
    ])
    agent = Agent(MODEL, "medium", client=client, session_id="frozen")
    agent.send("we are 8")
    agent.send("again")
    assert all(r["system"] == client.requests[0]["system"] for r in client.requests)
    assert all(r["tools"] == client.requests[0]["tools"] for r in client.requests)
