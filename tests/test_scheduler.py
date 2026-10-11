from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from jarvis_scout.scheduler import ScanScheduler


def test_interval_excludes_scan_duration_and_no_backlog():
    now = [0.0]
    waits = []
    starts = []

    def scan():
        starts.append(now[0])
        now[0] += 3 if len(starts) == 1 else 12

    def wait(delay):
        waits.append(delay)
        now[0] += delay
        return False

    scheduler = ScanScheduler(scan, interval_seconds=10, clock=lambda: now[0], wait=wait)
    assert scheduler.run(max_iterations=3) == 3
    assert starts == [0, 10, 22]
    assert waits == [7, 0]


def test_failure_is_handled_without_logging_exception_contents():
    calls = []

    def scan():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("token-secret")
        return "recovered"

    scheduler = ScanScheduler(scan, wait=lambda _: False)
    assert scheduler.run(max_iterations=2) == 2
    assert scheduler.failures == 1
    assert scheduler.last_error is None
    assert scheduler.last_result == "recovered"


def test_last_error_contains_only_error_type():
    def scan():
        raise ValueError("token-secret")

    scheduler = ScanScheduler(scan)
    assert scheduler.run(max_iterations=1) == 1
    assert scheduler.last_error == "ValueError"


def test_stop_event_prevents_scan_and_interrupts_wait():
    event = threading.Event()
    event.set()
    calls = []
    scheduler = ScanScheduler(lambda: calls.append(1), stop_event=event)
    assert scheduler.run(max_iterations=2) == 0
    assert not calls
    event.clear()
    scheduler.scan_callback = scheduler.stop
    assert scheduler.run(max_iterations=2) == 1


def test_same_scheduler_cannot_run_overlapping_jobs():
    entered = threading.Event()
    release = threading.Event()

    def scan():
        entered.set()
        assert release.wait(5)

    scheduler = ScanScheduler(scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(scheduler.run, max_iterations=1)
        try:
            assert entered.wait(5)
            with pytest.raises(RuntimeError, match="already running"):
                scheduler.run(max_iterations=1)
        finally:
            release.set()
        assert running.result(timeout=5) == 1
    assert scheduler.run(max_iterations=0) == 0


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_interval_is_rejected(interval):
    with pytest.raises(ValueError, match="positive and finite"):
        ScanScheduler(lambda: None, interval_seconds=interval)


def test_wait_can_signal_shutdown():
    scheduler = ScanScheduler(lambda: 1, wait=lambda _: True)
    assert scheduler.run(max_iterations=10) == 1
