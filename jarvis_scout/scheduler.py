"""A small, sequential scheduler for inexpensive single-process scans."""
from __future__ import annotations

from collections.abc import Callable
import math
import threading
import time
from typing import Any


class ScanScheduler:
    """Run immediately, then at a configured start-to-start interval.

    Slow scans never overlap and never trigger a backlog. This scheduler is
    intended for one process; Telegram deduplication handles concurrent alert
    attempts from other processes. A stopped event permits responsive shutdown.
    """

    def __init__(self, scan_callback: Callable[[], Any], interval_seconds: float = 300, *,
                 stop_event: threading.Event | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 wait: Callable[[float], bool] | None = None) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("Scan interval must be positive and finite.")
        self.scan_callback = scan_callback
        self.interval_seconds = interval_seconds
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.clock = clock
        self.wait = wait if wait is not None else self.stop_event.wait
        self.last_error: str | None = None
        self.last_result: Any = None
        self.failures = 0
        self._run_lock = threading.Lock()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self, *, max_iterations: int | None = None) -> int:
        if max_iterations is not None and (not isinstance(max_iterations, int) or max_iterations < 0):
            raise ValueError("max_iterations must be a non-negative integer.")
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("This scan scheduler is already running.")
        iterations = 0
        try:
            while not self.stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
                started = self.clock()
                try:
                    self.last_result = self.scan_callback()
                    self.last_error = None
                except Exception as error:
                    # Exception bodies may contain API URLs or secrets. Keep type only.
                    self.last_result = None
                    self.last_error = type(error).__name__
                    self.failures += 1
                iterations += 1
                if self.stop_event.is_set() or (max_iterations is not None and iterations >= max_iterations):
                    break
                elapsed = max(0, self.clock() - started)
                delay = max(0, self.interval_seconds - elapsed)
                if self.wait(delay):
                    break
            return iterations
        finally:
            self._run_lock.release()


# A short alias for integrations that prefer a generic scheduler name.
Scheduler = ScanScheduler
