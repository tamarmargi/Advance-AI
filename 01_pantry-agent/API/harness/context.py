"""Context assembly: system prompt and the messages sent each turn.

Rules:
- `system` is built once per session and never changes (memory is a snapshot taken at session start).
- Messages come only from the session history (after the latest compaction), whole turns only, so a
  tool_use / tool_result pair is never split. No recipe data is ever injected; recipes arrive only
  through tool results.
"""
from __future__ import annotations

from API.harness import log
from shared.prompts import SYSTEM_PROMPT

SUMMARY_PREFIX = "Summary of our earlier conversation in this session:\n"


def build_system(memory_text: str) -> list[dict]:
    """[static instructions, memory snapshot] as two text blocks (see caching.py for breakpoints)."""
    system = [{"type": "text", "text": SYSTEM_PROMPT},
              {"type": "text", "text": "Memory from previous sessions:\n" + memory_text}]
    log(__file__, f"system built: {len(SYSTEM_PROMPT)} + {len(system[1]['text'])} chars")
    return system


def is_turn_start(message: dict) -> bool:
    """A user message that is not a tool_result reply."""
    if message["role"] != "user":
        return False
    content = message["content"]
    return isinstance(content, str) or not any(b.get("type") == "tool_result" for b in content)


def build_messages(history: list[dict], compaction: dict | None = None) -> list[dict]:
    """Messages to send: history after the latest compaction, prefixed by its summary.

    compaction = {"upto": n, "summary": str}: history[:n] is replaced by the summary, which is
    merged into the first remaining (user) message so roles still alternate.
    """
    start = compaction["upto"] if compaction else 0
    window = [dict(m) for m in history[start:]]
    while window and not is_turn_start(window[0]):  # defensive: never start mid tool round
        window.pop(0)
    window = _merge_consecutive_users(window)
    if compaction and window:
        first = window[0]
        window[0] = {"role": "user", "content": [{"type": "text", "text": SUMMARY_PREFIX + compaction["summary"]},
                                                 *_blocks(first["content"])]}
    check_pairs(window)
    log(__file__, f"context: {len(window)} message(s) from history[{start}:] "
                  f"({'with' if compaction else 'no'} compaction summary)")
    return window


def check_pairs(messages: list[dict]) -> None:
    """Raise if any tool_use lacks its tool_result in the very next message (or vice versa)."""
    for i, m in enumerate(messages):
        if m["role"] != "assistant" or isinstance(m["content"], str):
            continue
        ids = {b["id"] for b in m["content"] if b.get("type") == "tool_use"}
        if not ids:
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        got = {b["tool_use_id"] for b in _blocks(nxt["content"]) if b.get("type") == "tool_result"} if nxt else set()
        if nxt is None:
            raise ValueError(f"message {i}: tool_use without tool_result (context ends mid tool round)")
        if ids != got:
            raise ValueError(f"message {i}: tool_use ids {ids} != tool_result ids {got}")
    if messages and not is_turn_start(messages[0]):
        raise ValueError("context starts with a tool_result")


def user_texts(messages: list[dict]) -> list[str]:
    """Plain user-typed texts in a context (summary block excluded); used for the trace checks."""
    out = []
    for m in messages:
        if m["role"] == "user":
            out += [b["text"] for b in _blocks(m["content"])
                    if b.get("type") == "text" and not b["text"].startswith(SUMMARY_PREFIX)]
    return out


def _blocks(content) -> list[dict]:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _merge_consecutive_users(messages: list[dict]) -> list[dict]:
    # After a max-iterations stop the history ends with a tool_result message; the next user text is
    # appended to it (text after tool_result blocks), so roles alternate without editing a sent message.
    out = []
    for m in messages:
        if out and out[-1]["role"] == m["role"] == "user":
            out[-1] = {"role": "user", "content": _blocks(out[-1]["content"]) + _blocks(m["content"])}
        else:
            out.append(m)
    return out
