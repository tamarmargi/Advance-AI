"""Run one agent on its own thread so Streamlit reruns (which may switch threads) never touch it directly.

The SDK Agent owns an asyncio loop and a Claude Code subprocess; keeping every call on the thread that
created it makes it safe to use from Streamlit. The API Agent goes through the same path for symmetry.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable


class AgentWorker:
    def __init__(self, factory: Callable[[], Any]):
        self._jobs: queue.Queue = queue.Queue()
        self._ready: Future = Future()
        threading.Thread(target=self._run, args=(factory,), daemon=True).start()
        self._ready.result()  # re-raises a construction error (e.g. missing API key) in the caller

    def _run(self, factory):
        try:
            agent = factory()
        except BaseException as e:
            self._ready.set_exception(e)
            return
        self._ready.set_result(None)
        while True:
            fn, fut = self._jobs.get()
            if fn is None:
                break
            try:
                fut.set_result(fn(agent))
            except BaseException as e:
                fut.set_exception(e)

    def call(self, fn: Callable[[Any], Any]) -> Any:
        """Run fn(agent) on the agent thread and wait for the result."""
        fut: Future = Future()
        self._jobs.put((fn, fut))
        return fut.result()

    def close(self) -> None:
        try:
            self.call(lambda a: a.close() if hasattr(a, "close") else None)
        finally:
            self._jobs.put((None, None))
