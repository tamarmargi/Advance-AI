"""Tool registry, schemas and dispatch for the harness.

The API `tools` list is built from shared/tool_specs.py; execution goes to shared/tools.py.
"""
from __future__ import annotations

import copy
import json

from API.harness import log
from shared.errors import PantryError
from shared.tool_specs import TOOL_SPECS
from shared.tools import TOOLS

MAX_ERROR_CHARS = 300


def build_tools() -> list[dict]:
    """API `tools` param: one {name, description, input_schema} per shared spec, in a fixed order."""
    tools = [copy.deepcopy({k: s[k] for k in ("name", "description", "input_schema")}) for s in TOOL_SPECS]
    log(__file__, f"built {len(tools)} tool definitions: {', '.join(t['name'] for t in tools)}")
    return tools


def dispatch(name: str, tool_input: dict) -> tuple[str, bool]:
    """Run one tool call. Never raises: failures come back as (message, True) so the model can recover."""
    args = json.dumps(tool_input, ensure_ascii=False)
    log(__file__, f"call {name}({args})")
    fn = TOOLS.get(name)
    if fn is None:
        content, is_error = f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}.", True
    else:
        try:
            content, is_error = fn(**tool_input), False
        except PantryError as e:
            content, is_error = f"{type(e).__name__}: {e}", True
        except TypeError as e:  # wrong / missing arguments
            content, is_error = f"Bad arguments for {name}: {e}", True
        except Exception as e:
            content, is_error = f"{type(e).__name__} in {name}: {e}", True
    if is_error:
        content = content[:MAX_ERROR_CHARS]
    log(__file__, f"{'ERROR' if is_error else 'ok'} {name} -> {content}")
    return content, is_error
