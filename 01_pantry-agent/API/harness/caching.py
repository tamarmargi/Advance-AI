"""Prompt caching (cache_control breakpoints).

Render order is tools -> system -> messages. Breakpoints (3 of the API's max 4):
  1. system[0]  static instructions: caches tools + instructions, identical across sessions
  2. system[-1] memory snapshot:     frozen for the session
  3. last block of the last message: moves forward every call, so each call reads the previous prefix
Only copies are marked; history and the caller's lists are never modified.
Note: cache lookups only look back ~20 blocks from a breakpoint, and prefixes under the model's minimum
cacheable size are silently not cached; check cache_read_input_tokens in tokens.py output.
"""
from __future__ import annotations

import copy

from API.harness import log

MAX_BREAKPOINTS = 4
EPHEMERAL = {"type": "ephemeral"}


def mark_system(system: list[dict]) -> list[dict]:
    marked = copy.deepcopy(system)
    for i in {0, len(marked) - 1}:
        marked[i]["cache_control"] = dict(EPHEMERAL)
    log(__file__, f"system breakpoints on block(s) {sorted({0, len(marked) - 1})}")
    return marked


def mark_messages(messages: list[dict]) -> list[dict]:
    """Copy of messages with any old cache_control removed and one breakpoint on the last block."""
    marked = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        marked.append({"role": m["role"], "content": [{k: v for k, v in b.items() if k != "cache_control"}
                                                      for b in content]})
    if marked:
        last = marked[-1]["content"][-1]
        last["cache_control"] = dict(EPHEMERAL)
        log(__file__, f"message breakpoint on messages[{len(marked) - 1}] {last['type']} block")
    return marked


def count_breakpoints(tools: list[dict], system: list[dict], messages: list[dict]) -> int:
    blocks = list(tools) + list(system) + [b for m in messages if not isinstance(m["content"], str)
                                           for b in m["content"]]
    return sum("cache_control" in b for b in blocks)


def check_limit(tools, system, messages) -> int:
    n = count_breakpoints(tools, system, messages)
    if n > MAX_BREAKPOINTS:
        raise ValueError(f"{n} cache_control breakpoints > API limit {MAX_BREAKPOINTS}")
    return n
