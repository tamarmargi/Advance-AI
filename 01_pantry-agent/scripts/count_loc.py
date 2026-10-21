"""Lines of code per harness part.

API: non-blank, non-comment, non-docstring lines per file in API/harness/.
SDK: lines marked "# replaces: <name>" grouped by name, plus the SDK/tools.py total.

Usage: python scripts/count_loc.py
"""
from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = re.compile(r"#\s*replaces:\s*([A-Za-z_]+)")


def code_lines(path: Path) -> int:
    """Count lines holding real code (blank lines, comments and docstrings excluded)."""
    src = path.read_text(encoding="utf-8")
    doc_lines = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                doc_lines.update(range(first.lineno, first.end_lineno + 1))
    lines = set()
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENCODING, tokenize.ENDMARKER}
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type not in skip:
            lines.update(range(tok.start[0], tok.end[0] + 1))
    return len(lines - doc_lines)


def api_loc() -> dict:
    files = {p.stem: code_lines(p) for p in sorted((ROOT / "API" / "harness").glob("*.py"))}
    return {"per_file": files, "total": sum(files.values())}


def sdk_loc() -> dict:
    groups: dict[str, int] = {}
    for p in sorted((ROOT / "SDK").glob("*.py")):
        for line in p.read_text(encoding="utf-8").splitlines():
            m = MARK.search(line)
            if m:  # "tools.py (dispatch)" -> "tools"
                groups[m.group(1)] = groups.get(m.group(1), 0) + 1
    return {"replaces": dict(sorted(groups.items())), "marked_total": sum(groups.values()),
            "tools_py_total": code_lines(ROOT / "SDK" / "tools.py"),
            "agent_py_total": code_lines(ROOT / "SDK" / "agent.py")}


def count_loc() -> dict:
    return {"API": api_loc(), "SDK": sdk_loc()}


if __name__ == "__main__":
    print(json.dumps(count_loc(), indent=1))
