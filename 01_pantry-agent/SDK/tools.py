"""SDK tool wrappers around shared/tools.py: one in-process MCP server ("pantry").

Names, descriptions and schemas come from shared/tool_specs.py, exactly like the API harness.
Error results use the same messages as API/harness/tools.py dispatch().
"""
from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server, tool

from shared.errors import PantryError
from shared.tool_specs import TOOL_SPECS
from shared.tools import TOOLS

SERVER_NAME = "pantry"
MCP_PREFIX = f"mcp__{SERVER_NAME}__"
MAX_ERROR_CHARS = 300


def _run(name: str, args: dict) -> tuple[str, bool]:  # replaces: tools.py (dispatch: never raises)
    try:
        return TOOLS[name](**args), False
    except PantryError as e:
        msg = f"{type(e).__name__}: {e}"
    except TypeError as e:
        msg = f"Bad arguments for {name}: {e}"
    except Exception as e:
        msg = f"{type(e).__name__} in {name}: {e}"
    return msg[:MAX_ERROR_CHARS], True


def _make(spec: dict):
    name = spec["name"]

    @tool(name, spec["description"], spec["input_schema"])  # replaces: tools.py (build_tools)
    async def handler(args: dict) -> dict:
        text, is_error = _run(name, args)
        result = {"content": [{"type": "text", "text": text}]}
        if is_error:
            result["is_error"] = True  # replaces: tools.py (is_error tool_result)
        return result

    return handler


SDK_TOOLS = [_make(spec) for spec in TOOL_SPECS]  # replaces: tools.py
SERVER = create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=SDK_TOOLS)  # replaces: tools.py
ALLOWED_TOOLS = [MCP_PREFIX + spec["name"] for spec in TOOL_SPECS]  # replaces: tools.py


def short_name(mcp_name: str) -> str:
    """'mcp__pantry__get_recipe' -> 'get_recipe' (so traces match the API version)."""
    return mcp_name.removeprefix(MCP_PREFIX)
