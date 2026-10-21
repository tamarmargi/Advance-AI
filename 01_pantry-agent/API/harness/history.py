"""Conversation history: one append-only JSONL file per session.

Files live in <runtime>/history/<session_id>.jsonl (runtime = shared/data/runtime or PANTRY_DATA_DIR).
The file is only ever opened in append mode: never rewritten, never truncated.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from API.harness import log
from shared.tools import runtime_dir


def history_path(session_id: str) -> Path:
    return runtime_dir() / "history" / f"{session_id}.jsonl"


def append(session_id: str, message: dict) -> None:
    """Append one API message ({role, content}) as a JSON line."""
    path = history_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "message": message},
                      ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    log(__file__, f"append {session_id}: {message['role']} ({_describe(message['content'])})")


def load(session_id: str) -> list[dict]:
    """All messages of a session, in order ([] if the session has no file yet)."""
    path = history_path(session_id)
    if not path.exists():
        log(__file__, f"load {session_id}: no history file")
        return []
    with path.open(encoding="utf-8") as f:
        messages = [json.loads(line)["message"] for line in f if line.strip()]
    log(__file__, f"load {session_id}: {len(messages)} messages")
    return messages


class History:
    """Convenience wrapper bound to one session id."""

    def __init__(self, session_id: str):
        self.session_id = session_id

    def append(self, message: dict) -> None:
        append(self.session_id, message)

    def load(self) -> list[dict]:
        return load(self.session_id)


def _describe(content) -> str:
    if isinstance(content, str):
        return "text"
    return ", ".join(b.get("type", "?") for b in content)
