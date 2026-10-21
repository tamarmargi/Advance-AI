"""Pantry agent definition built on the hand-written harness.

One Agent = one session: system prompt (with a memory snapshot) frozen at construction, history in an
append-only JSONL file, simple compaction at turn boundaries, prompt caching on every call.
Implements the shared/runner.py agent protocol: send(text) -> turn record.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from API.harness import caching, compaction, context, history, log, memory, tokens
from API.harness.loop import base_request, run_loop
from API.harness.tools import build_tools


class Agent:
    def __init__(self, model: str, effort: str, client=None, session_id: str | None = None,
                 compaction_threshold: int = compaction.DEFAULT_THRESHOLD):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self.client, self.model = client, model
        self.session_id = session_id or f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        self.history = history.History(self.session_id)
        self.messages = self.history.load()           # [] for a new session
        self.compaction = compaction.load_latest(self.session_id)
        self.compaction_threshold = compaction_threshold
        self.last_prompt_tokens = 0
        self.memory_text = memory.memory_summary()    # snapshot: system stays byte-identical all session
        self.memory_loaded = memory.has_memory()
        tools = build_tools()
        system = caching.mark_system(context.build_system(self.memory_text))
        self.request = {**base_request(model, effort), "system": system, "tools": tools}
        log(__file__, f"session {self.session_id}: new agent, {len(self.messages)} message(s) in history, "
                      f"memory_loaded={self.memory_loaded}")

    def memory_summary(self) -> str:
        return self.memory_text

    def _record(self, message: dict) -> None:
        self.history.append(message)
        self.messages.append(message)

    def _maybe_compact(self) -> None:
        if not self.messages or not compaction.should_compact(self.last_prompt_tokens, self.compaction_threshold):
            return
        current = context.build_messages(self.messages, self.compaction)
        self.compaction = compaction.compact(self.client, self.model, current, upto=len(self.messages))
        compaction.save(self.session_id, self.compaction)

    def _prepare(self, messages: list[dict]) -> list[dict]:
        marked = caching.mark_messages(messages)
        caching.check_limit(self.request["tools"], self.request["system"], marked)
        return marked

    def send(self, text: str) -> dict:
        log(__file__, f"session {self.session_id}: user turn ({len(text)} chars)")
        self._maybe_compact()  # turn boundary: the previous turn is complete
        self._record({"role": "user", "content": [{"type": "text", "text": text}]})
        ctx = context.build_messages(self.messages, self.compaction)
        result = run_loop(self.client, {**self.request, "messages": ctx},
                          on_message=self._record, prepare_messages=self._prepare)
        last = result.usage[-1]
        self.last_prompt_tokens = (last["input_tokens"] + last["cache_read_input_tokens"]
                                   + last["cache_creation_input_tokens"])
        return {
            "text": result.text,
            "tool_calls": result.tools_called,
            "context_user_messages": context.user_texts(ctx),
            "memory_loaded": self.memory_loaded,
            "model_calls": result.model_calls,
            "stop_reason": result.stop_reason,
            "usage": tokens.aggregate(result.usage, self.model),
            "session_id": self.session_id,
        }
