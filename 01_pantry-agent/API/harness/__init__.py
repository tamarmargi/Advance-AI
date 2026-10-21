"""Hand-written agent harness for the raw Anthropic Messages API.

Every runtime action logs through log(), which prints "[<filename>] <message>".
"""
import sys
from pathlib import Path

# Hebrew / "…" must never crash a run on a non-UTF-8 Windows console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def log(source: str, message: str) -> None:
    """Print one harness log line; pass __file__ as source."""
    print(f"[{Path(source).name}] {message}", flush=True)
