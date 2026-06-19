"""Tests for G10 (crash visibility) and the logging half of T11.

Before this, run_watcher()/backup_loop() wrapped their entire iteration in
`try: ... except Exception: pass` -- a single bad tick was tolerated (good,
a network blip shouldn't kill the loop) but a *persistent* failure had zero
signal: no log line, no push, nothing, forever. _run_resilient_loop keeps
the "don't die on one bad tick" behavior but makes failures visible: every
exception logged with a traceback, a phone push on the first failure of a
streak (rate-limited on repeats), a push on recovery, and a once-daily
heartbeat log line so `tail ~/Library/Logs/timely.log` can confirm the
thread is actually alive.

These tests drive `_run_resilient_loop` directly with a stubbed `tick()`
and a patched `time.sleep` that raises after N iterations -- the loop is
intentionally infinite in production, so "run exactly N times then stop"
is how you unit test it without a real background thread.
"""
import logging
import time

import pytest

from src.services import notify


class _StopLoop(Exception):
    pass


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """_last_heartbeat_date/_last_alert_at are module-level dicts shared
    across iterations (and tests) -- clear them so tests don't leak into
    each other."""
    notify._last_heartbeat_date.clear()
    notify._last_alert_at.clear()
    # Replace the real push (osascript + a real HTTP call to ntfy.sh) with
    # a recorder; none of this should touch the network or macOS in CI.
    pushes = []
    monkeypatch.setattr(notify, "_push", pushes.append)
    return pushes


def _run_n_then_stop(monkeypatch, n):
    calls = {"n": 0}

    def fake_sleep(_secs):
        calls["n"] += 1
        if calls["n"] >= n:
            raise _StopLoop()

    monkeypatch.setattr(notify.time, "sleep", fake_sleep)


class TestResilientLoop:
    def test_failure_is_logged_and_pushed_once(self, monkeypatch, caplog, _reset_module_state):
        pushes = _reset_module_state
        _run_n_then_stop(monkeypatch, 2)

        def tick():
            raise RuntimeError("boom")

        with caplog.at_level(logging.INFO, logger="timely.notify"):
            with pytest.raises(_StopLoop):
                notify._run_resilient_loop("thing", 1, tick)

        assert any("iteration failed" in r.message for r in caplog.records)
        assert len(pushes) == 1
        assert "thing" in pushes[0] and "failing since" in pushes[0]

    def test_repeated_failure_does_not_repush_within_cooldown(self, monkeypatch, _reset_module_state):
        pushes = _reset_module_state
        _run_n_then_stop(monkeypatch, 5)

        def tick():
            raise RuntimeError("still broken")

        with pytest.raises(_StopLoop):
            notify._run_resilient_loop("thing", 1, tick)

        # 4 failed iterations, all within the same second -- only the
        # first should have triggered a push (1hr cooldown).
        assert len(pushes) == 1

    def test_recovery_after_failure_pushes_once(self, monkeypatch, _reset_module_state):
        pushes = _reset_module_state
        _run_n_then_stop(monkeypatch, 3)
        attempts = {"n": 0}

        def tick():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient")
            # succeeds on attempts 2+

        with pytest.raises(_StopLoop):
            notify._run_resilient_loop("thing", 1, tick)

        assert len(pushes) == 2
        assert "failing since" in pushes[0]
        assert "recovered" in pushes[1]

    def test_healthy_loop_never_pushes(self, monkeypatch, _reset_module_state):
        pushes = _reset_module_state
        _run_n_then_stop(monkeypatch, 3)

        with pytest.raises(_StopLoop):
            notify._run_resilient_loop("thing", 1, lambda: None)

        assert pushes == []

    def test_heartbeat_logged_once_per_day(self, monkeypatch, caplog, _reset_module_state):
        _run_n_then_stop(monkeypatch, 3)
        with caplog.at_level(logging.INFO, logger="timely.notify"):
            with pytest.raises(_StopLoop):
                notify._run_resilient_loop("thing", 1, lambda: None)
        heartbeats = [r for r in caplog.records if "heartbeat" in r.message]
        # Same calendar day across all 3 iterations -> logged exactly once.
        assert len(heartbeats) == 1


class TestRestoreDrillLoop:
    """G2: restore_drill_loop ticks hourly but only actually runs
    store.verify_backup() once _RESTORE_DRILL_INTERVAL_DAYS have elapsed
    since the last recorded drill (store.get_kv/set_kv("last_restore_drill")),
    so a process that restarts often doesn't re-run an expensive check
    every hour forever."""

    def test_runs_immediately_when_never_run_before(self, monkeypatch, tmp_path, _reset_module_state):
        import store
        calls = []
        monkeypatch.setattr(store, "verify_backup", lambda d: calls.append(d) or {"runs": 0})
        _run_n_then_stop(monkeypatch, 1)

        with pytest.raises(_StopLoop):
            notify.restore_drill_loop(str(tmp_path))

        assert calls == [str(tmp_path)]
        value, age = store.get_kv("last_restore_drill")
        assert age is not None and age < 5

    def test_does_not_rerun_within_interval(self, monkeypatch, tmp_path, _reset_module_state):
        import store
        calls = []
        monkeypatch.setattr(store, "verify_backup", lambda d: calls.append(d) or {"runs": 0})
        store.set_kv("last_restore_drill", time.time())  # "just ran"
        _run_n_then_stop(monkeypatch, 3)

        with pytest.raises(_StopLoop):
            notify.restore_drill_loop(str(tmp_path))

        assert calls == []

    def test_failure_surfaces_through_resilient_loop(self, monkeypatch, tmp_path, _reset_module_state):
        import store
        pushes = _reset_module_state
        monkeypatch.setattr(store, "verify_backup",
                             lambda d: (_ for _ in ()).throw(RuntimeError("corrupt")))
        _run_n_then_stop(monkeypatch, 1)

        with pytest.raises(_StopLoop):
            notify.restore_drill_loop(str(tmp_path))

        assert len(pushes) == 1
        assert "restore_drill" in pushes[0]
