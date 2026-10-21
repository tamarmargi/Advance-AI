"""History compaction when the context grows too large.

"Simple compaction" (the shape that is safe with preserved thinking on claude-opus-5-5): at the START of
a turn, if the previous call's prompt exceeded the threshold, summarize the whole current context into one
text, and from then on send only that summary + the new turns. Nothing older is replayed (no earlier
thinking blocks), and it never happens mid tool round.

history.jsonl is never touched: compaction states go to <session>.compaction.jsonl (append-only), and
context.build_messages applies the latest one.
"""
from __future__ import annotations

import json
from datetime import datetime

from API.harness import log
from API.harness.history import history_path

DEFAULT_THRESHOLD = 60_000  # prompt tokens (input + cache read + cache write) of the previous call

SUMMARIZER_PROMPT = """\
Summarize this conversation between a user and a kosher pantry assistant so the assistant can continue \
without it. Keep: what the user asked for, decisions made, what was saved (preferences, meal plan, pantry, \
shopping list) and anything still open. Do not copy recipe ingredient lists or tool output verbatim; the \
tools can be called again. Write in English, under 200 words."""


def should_compact(last_prompt_tokens: int, threshold: int = DEFAULT_THRESHOLD) -> bool:
    decision = last_prompt_tokens > threshold
    log(__file__, f"last prompt {last_prompt_tokens} tokens vs threshold {threshold}: "
                  f"{'compact' if decision else 'no compaction'}")
    return decision


def render_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        content = m["content"]
        for b in ([{"type": "text", "text": content}] if isinstance(content, str) else content):
            t = b.get("type")
            if t == "text":
                lines.append(f"{m['role']}: {b['text']}")
            elif t == "tool_use":
                lines.append(f"assistant called {b['name']}({json.dumps(b['input'], ensure_ascii=False)})")
            elif t == "tool_result":
                lines.append(f"tool result: {str(b.get('content'))[:200]}")
    return "\n".join(lines)


def compact(client, model: str, context_messages: list[dict], upto: int) -> dict:
    """Summarize the current context; returns {"upto", "summary"} (history[:upto] is now covered)."""
    log(__file__, f"compacting {len(context_messages)} context message(s), covering history[:{upto}]")
    response = client.messages.create(
        model=model, max_tokens=2000, output_config={"effort": "low"}, system=SUMMARIZER_PROMPT,
        messages=[{"role": "user", "content": render_transcript(context_messages)}])
    summary = "".join(b.text for b in response.content if b.type == "text").strip()
    log(__file__, f"summary: {len(summary)} chars")
    return {"upto": upto, "summary": summary}


def compaction_path(session_id: str):
    return history_path(session_id).with_suffix(".compaction.jsonl")


def save(session_id: str, state: dict) -> None:
    path = compaction_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **state},
                           ensure_ascii=False) + "\n")
    log(__file__, f"saved compaction state to {path.name}")


def load_latest(session_id: str) -> dict | None:
    path = compaction_path(session_id)
    if not path.exists():
        return None
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"upto": lines[-1]["upto"], "summary": lines[-1]["summary"]} if lines else None
