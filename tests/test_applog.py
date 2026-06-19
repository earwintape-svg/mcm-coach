"""Tests for src/services/applog.py (T11) and the HTTP request-logging
middleware it backs in main.py.

Two things worth pinning: (1) get_logger() is idempotent -- main.py,
notify.py, and any future caller can all call it without piling up
duplicate handlers and writing every line N times; (2) the FastAPI
middleware actually emits a log line per request, including on a 4xx/5xx
response, since "nothing server-side correlated a user-reported issue with
what actually ran" was the whole point of T11.
"""
import logging

from fastapi.testclient import TestClient

from src.services.applog import get_logger
import main


class TestGetLogger:
    def test_returns_namespaced_logger(self):
        log = get_logger("foo")
        assert log.name == "timely.foo"

    def test_idempotent_handler_setup(self):
        """Calling get_logger() repeatedly (every module that wants a
        logger does this at import time) must not attach a new
        RotatingFileHandler each time -- that would write every line
        once per call site."""
        get_logger("a")
        get_logger("b")
        get_logger("c")
        root = logging.getLogger("timely")
        assert len(root.handlers) == 1


class TestRequestLogging:
    def test_request_logs_method_path_and_status(self, caplog):
        # No `with TestClient(...)` -- avoids triggering main.lifespan's
        # background threads, matching the pattern in test_api.py. Hits a
        # static asset route (no Garmin client involved) rather than an
        # /api/* route, since those touch live external services that
        # aren't available in a test environment.
        client = TestClient(main.app)
        with caplog.at_level(logging.INFO, logger="timely.http"):
            client.get("/app.js")
        lines = [r.getMessage() for r in caplog.records]
        assert any("GET" in line and "/app.js" in line for line in lines)

    def test_4xx_response_logged_at_warning(self, caplog):
        client = TestClient(main.app)
        with caplog.at_level(logging.INFO, logger="timely.http"):
            # No body on a route that requires one -> FastAPI 422.
            resp = client.post("/api/move", json={})
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert resp.status_code >= 400
        assert any(str(resp.status_code) in r.getMessage() for r in warnings)
