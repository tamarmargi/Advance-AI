"""Agent loop: call the model, run tools, repeat until done.

Uses client.messages.create only (no tool runner, no beta helpers).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from API.harness import log, tokens
from API.harness.tools import dispatch

MAX_ITERATIONS = 10
MAX_TOKENS = 16000  # non-streaming default; keeps a single response under the SDK's HTTP timeout
SCENARIO = Path(__file__).resolve().parents[2] / "scenario.json"


def model_settings(path: Path = SCENARIO) -> tuple[str, str]:
    """(model, effort) from scenario.json."""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    log(__file__, f"model={cfg['model']} effort={cfg['effort']} (from {Path(path).name})")
    return cfg["model"], cfg["effort"]


def base_request(model: str, effort: str, max_tokens: int = MAX_TOKENS) -> dict:
    """Fixed part of every messages.create call.

    Effort goes in output_config (anthropic 1.16.0: OutputConfigParam.effort, values
    low|medium|high|xhigh|max); it is not a top-level parameter. Thinking is left at the
    model default (adaptive on claude-opus-5-5, which cannot disable it).
    """
    return {"model": model, "max_tokens": max_tokens, "output_config": {"effort": effort}}


@dataclass
class LoopResult:
    text: str                                   # final assistant text
    new_messages: list[dict]                    # assistant / tool_result messages added this turn
    model_calls: int
    tools_called: list[dict] = field(default_factory=list)  # {name, input, result, is_error}
    usage: list[dict] = field(default_factory=list)         # one tokens.record_call dict per model call
    stop_reason: str | None = None


def _to_param(block) -> dict:
    """SDK response block -> plain dict we can resend and store as JSON (thinking blocks kept unchanged)."""
    return block.model_dump(mode="json", exclude_none=True)


def _text(content: list[dict]) -> str:
    return "".join(b["text"] for b in content if b["type"] == "text").strip()


def run_loop(client, request_kwargs: dict, *, max_iterations: int = MAX_ITERATIONS,
             on_message: Callable[[dict], None] | None = None,
             prepare_messages: Callable[[list[dict]], list[dict]] | None = None) -> LoopResult:
    """Run one user turn to completion.

    request_kwargs: everything for client.messages.create (model, max_tokens, system, tools,
    output_config, messages...). request_kwargs["messages"] must already end with the user message;
    it is not mutated. on_message is called with every new message as soon as it exists
    (e.g. history.append), so a crash mid-turn still leaves a consistent append-only log.
    prepare_messages transforms a copy of the messages right before each call (e.g. caching.mark_messages).
    """
    messages = list(request_kwargs["messages"])
    model = request_kwargs["model"]
    result = LoopResult(text="", new_messages=[], model_calls=0)

    def add(message: dict) -> None:
        messages.append(message)
        result.new_messages.append(message)
        if on_message:
            on_message(message)

    while True:
        result.model_calls += 1
        log(__file__, f"call {result.model_calls}: messages.create with {len(messages)} messages")
        to_send = prepare_messages(messages) if prepare_messages else messages
        response = client.messages.create(**{**request_kwargs, "messages": to_send})
        result.usage.append(tokens.record_call(response.usage, model))
        result.stop_reason = response.stop_reason
        content = [_to_param(b) for b in response.content]
        add({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b["type"] == "tool_use"]
        log(__file__, f"call {result.model_calls}: stop_reason={response.stop_reason}, "
                      f"{len(tool_uses)} tool_use block(s)")

        if response.stop_reason == "tool_use":
            # ALL tool_use blocks of this response -> ALL tool_result blocks in ONE user message.
            results = []
            for b in tool_uses:
                out, is_error = dispatch(b["name"], b["input"])
                result.tools_called.append({"name": b["name"], "input": b["input"],
                                            "result": out, "is_error": is_error})
                block = {"type": "tool_result", "tool_use_id": b["id"], "content": out}
                if is_error:
                    block["is_error"] = True
                results.append(block)
            add({"role": "user", "content": results})
            log(__file__, f"sent {len(results)} tool_result block(s) in one user message")
            if result.model_calls >= max_iterations:
                log(__file__, f"stopping: reached max {max_iterations} iterations with tools still pending")
                result.text = "(stopped: too many tool iterations)"
                break
            continue

        if response.stop_reason == "pause_turn":  # server paused a long turn; resend to let it continue
            if result.model_calls >= max_iterations:
                log(__file__, f"stopping: reached max {max_iterations} iterations on pause_turn")
                break
            continue

        result.text = _text(content)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            log(__file__, f"model refused: {getattr(details, 'category', None)}")
            result.text = result.text or "(the model declined this request)"
        elif response.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            log(__file__, f"warning: response cut off ({response.stop_reason})")
        break

    log(__file__, f"turn done: {result.model_calls} model call(s), "
                  f"{len(result.tools_called)} tool call(s), final text {len(result.text)} chars")
    return result
