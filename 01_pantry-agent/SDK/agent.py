"""Pantry agent definition built on the Claude Agent SDK.

Same model, effort, system prompt and tools as API/agent.py; Claude Code supplies the harness.
Implements the shared/runner.py agent protocol: send(text) -> turn record.

No SDK equivalent, handled automatically by Claude Code:
- context.py:    Claude Code assembles system prompt, tools and conversation itself.
- compaction.py: Claude Code auto-compacts the conversation when the context window fills up.
- caching.py:    Claude Code places prompt-cache breakpoints itself (visible as cache_* in ResultMessage.usage).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    delete_session,
    get_session_messages,
    list_sessions,
)

from SDK.tools import ALLOWED_TOOLS, SERVER, SERVER_NAME, short_name
from shared.prompts import SYSTEM_PROMPT

# Agent's own project dir: holds the agent CLAUDE.md (memory instructions), separate from the repo-root
# developer CLAUDE.md.
WORKSPACE = Path(__file__).resolve().parent / "workspace"
# Claude Code also loads CLAUDE.md files from parent directories; keep the developer one out.
DEV_CLAUDE_MD = Path(__file__).resolve().parents[1] / "CLAUDE.md"
SETTINGS = json.dumps({"claudeMdExcludes": [str(DEV_CLAUDE_MD), DEV_CLAUDE_MD.as_posix()]})
USAGE_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


def log(message: str) -> None:
    print(f"[agent.py] {message}", flush=True)


def api_key_is_set() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def reset_sessions() -> None:
    """Delete every Claude Code session transcript recorded for SDK/workspace (clean start)."""
    sessions = list_sessions(directory=str(WORKSPACE), include_worktrees=False)
    for s in sessions:
        delete_session(s.session_id, directory=str(WORKSPACE))
    log(f"reset: deleted {len(sessions)} session transcript(s) for {WORKSPACE.name}/")


class Agent:
    def __init__(self, model: str, effort: str, resume: str | None = None, cost_offset: float = 0.0):
        if api_key_is_set():
            log("WARNING: ANTHROPIC_API_KEY is set; Claude Code would bill the API key instead of your login")
        self.model = model
        self.session_id = resume
        # ResultMessage.total_cost_usd is a running total for the Claude Code session (and continues after
        # resume), while ResultMessage.usage is per query; per-turn cost is the difference.
        self.cumulative_cost = cost_offset
        self.options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,                 # same custom prompt as API (not the Claude Code preset)
            tools=[],                                    # no built-in tools at all (Bash, Read, Write, WebSearch...)
            allowed_tools=ALLOWED_TOOLS,                 # replaces: tools.py (only our MCP tools, auto-approved)
            permission_mode="dontAsk",                   # anything not pre-approved is denied, never prompted
            mcp_servers={SERVER_NAME: SERVER},           # replaces: tools.py
            strict_mcp_config=True,                      # ignore any other MCP config on this machine
            setting_sources=["project"],                 # replaces: memory.py (loads SDK/workspace/CLAUDE.md)
            settings=SETTINGS,                           # exclude the repo-root developer CLAUDE.md
            cwd=str(WORKSPACE),
            model=model,
            effort=effort,
            resume=resume,                               # replaces: history.py (session 2 resumes session 1)
        )
        self._runner = asyncio.Runner()
        self._client = ClaudeSDKClient(self.options)
        self._runner.run(self._client.connect())
        log(f"connected: model={model} effort={effort} cwd={WORKSPACE.name}/ resume={resume}")
        self.memory_files = self._runner.run(self._loaded_memory_files())
        log(f"CLAUDE.md files loaded: {self.memory_files}")
        if any(Path(p).resolve() == DEV_CLAUDE_MD for p in self.memory_files):
            raise RuntimeError(f"developer {DEV_CLAUDE_MD} was loaded into the agent's context")

    async def _loaded_memory_files(self) -> list[str]:
        usage = await self._client.get_context_usage()
        return [f.get("path", "?") for f in usage.get("memoryFiles", [])]

    async def _send(self, text: str) -> dict:  # replaces: loop.py
        await self._client.query(text)
        texts, calls, by_id, result = [], [], {}, None
        async for message in self._client.receive_response():  # replaces: loop.py
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                    elif isinstance(block, ToolUseBlock):  # replaces: loop.py (collect tool calls)
                        call = {"name": short_name(block.name), "input": block.input, "result": None,
                                "is_error": False}
                        by_id[block.id] = call
                        calls.append(call)
                        log(f"tool call {call['name']}({block.input})")
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in by_id:
                        call = by_id[block.tool_use_id]
                        call["result"] = _result_text(block.content)
                        call["is_error"] = bool(block.is_error)
                        log(f"tool {'ERROR' if call['is_error'] else 'ok'} {call['name']} -> {call['result']}")
            elif isinstance(message, ResultMessage):
                result = message
        if result is None:
            raise RuntimeError("receive_response ended without a ResultMessage")
        self.session_id = result.session_id
        usage = {f: (result.usage or {}).get(f, 0) or 0 for f in USAGE_FIELDS}  # replaces: tokens.py
        total = result.total_cost_usd
        usage["cost_usd"] = None if total is None else round(total - self.cumulative_cost, 6)  # replaces: tokens.py
        usage["cumulative_cost_usd"] = total
        usage["calls"] = result.num_turns
        if total is not None:
            self.cumulative_cost = total
        log(f"result: subtype={result.subtype} turns={result.num_turns} session={result.session_id} "
            + " ".join(f"{f}={usage[f]}" for f in USAGE_FIELDS)
            + f" cost=${usage['cost_usd']} (session total ${total})")
        return {
            "text": (result.result or "\n".join(texts)).strip(),
            "tool_calls": calls,
            "context_user_messages": self._context_user_messages(),
            "memory_loaded": False,      # nothing injected; the model reads memory through get_preferences
            "model_calls": result.num_turns,
            "stop_reason": result.stop_reason,
            "usage": usage,
            "session_id": result.session_id,
            "is_error": result.is_error,
        }

    def _context_user_messages(self) -> list[str]:
        """User-typed texts in the Claude Code session transcript (includes resumed session-1 turns)."""
        try:
            msgs = get_session_messages(self.session_id, directory=str(WORKSPACE))
        except Exception as e:  # transcript unreadable -> say so instead of guessing
            log(f"could not read session transcript: {e}")
            return []
        out = []
        for m in msgs:
            if m.type != "user":
                continue
            content = m.message.get("content")
            if isinstance(content, str):
                out.append(content)
            elif isinstance(content, list):
                out += [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return out

    def send(self, text: str) -> dict:
        log(f"user turn ({len(text)} chars)")
        return self._runner.run(self._send(text))

    def close(self) -> None:
        self._runner.run(self._client.disconnect())
        self._runner.close()
        log("disconnected")


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""
