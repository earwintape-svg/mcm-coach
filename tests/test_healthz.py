"""Tests for the G12 /healthz endpoint.

"A trivial health endpoint plus the existing launchd, with an alert if
the service is unreachable." This file covers the FastAPI half (the
endpoint itself); the alerting half is lan.sh's healthcheck-on, a launchd
job + bash that isn't unit-testable the way Python is -- verified
manually instead (see the commit message).

Uses TestClient WITHOUT the `with` context-manager form, same as
test_api.py/test_applog.py: that form skips main.lifespan, so the four
real background threads (backup_loop, run_watcher, etc.) never start
during the test run.
"""
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


class TestHealthz:
    def test_returns_200_and_ok_status(self):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_reports_uptime_increasing(self, monkeypatch):
        import time as time_mod
        monkeypatch.setattr(main, "_START_TIME", time_mod.time() - 12.0)
        r = client.get("/healthz")
        assert r.json()["uptime_sec"] >= 12.0

    def test_requires_no_access_key(self):
        """The whole point: an external uptime monitor can't carry the
        LAN key (src/api/routes.py's _auth dependency), and /healthz is
        defined directly on `app`, not behind `router` -- same
        unauthenticated pattern as "/", "/app.js", the icon."""
        r = client.get("/healthz?key=definitely-not-the-real-key")
        assert r.status_code == 200

    def test_does_not_touch_the_database(self, monkeypatch):
        """A health check that can fail for reasons unrelated to "is the
        process up" (e.g. a locked DB file) defeats its own purpose --
        pin that /healthz never imports/calls store."""
        import store
        monkeypatch.setattr(store, "get_runs", lambda: (_ for _ in ()).throw(
            AssertionError("healthz must not touch the database")))
        r = client.get("/healthz")
        assert r.status_code == 200
