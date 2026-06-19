"""Web-layer tests: routing, the _verify auth dependency, and request
validation on the mutating endpoints (T5/the product agent's rev-3
addendum flagged this as a real gap -- every other test exercises pure
logic in builders/fitness/schedule, never the FastAPI layer itself).

Uses TestClient WITHOUT the `with` context-manager form deliberately:
entering it as a context manager would run main.py's lifespan, which
starts the real backup_loop/run_watcher daemon threads (touching the
filesystem and trying to reach Garmin/intervals.icu). Plain
TestClient(app) skips lifespan entirely, which is what every test here
wants -- pure request/response behavior, no background side effects.
"""
import pytest
from fastapi.testclient import TestClient

from main import app
from src.api.routes import set_access_key

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_access_key():
    """_ACCESS_KEY is module-global state in src.api.routes; make sure one
    test setting a key can't leak into the next."""
    set_access_key(None)
    yield
    set_access_key(None)


class TestAuth:
    def test_authed_get_with_no_key_set_succeeds(self):
        """Default state (no --lan key configured): everything is open,
        matching the documented 'localhost trusted' behavior."""
        resp = client.get("/api/weather")
        assert resp.status_code == 200

    def test_lan_request_without_key_is_rejected(self):
        """Once a LAN key is set, a request that doesn't look like it came
        from localhost (TestClient's default client host is 'testclient',
        not 127.0.0.1) must be rejected without the right X-Key."""
        set_access_key("supersecret")
        resp = client.get("/api/weather")
        assert resp.status_code == 403

    def test_lan_request_with_correct_key_succeeds(self):
        set_access_key("supersecret")
        resp = client.get("/api/weather", headers={"X-Key": "supersecret"})
        assert resp.status_code == 200

    def test_lan_request_with_wrong_key_is_rejected(self):
        set_access_key("supersecret")
        resp = client.get("/api/weather", headers={"X-Key": "nope"})
        assert resp.status_code == 403

    def test_key_via_query_param_also_works(self):
        set_access_key("supersecret")
        resp = client.get("/api/weather?key=supersecret")
        assert resp.status_code == 200


class TestRequestValidation:
    """Pydantic models (T5) should 422 on bad input before the handler
    ever runs -- this is the whole point of replacing body: dict."""

    def test_move_missing_field_422(self):
        resp = client.post("/api/move", json={"scheduleId": 1, "workoutId": 2})
        assert resp.status_code == 422

    def test_move_bad_date_422(self):
        resp = client.post("/api/move", json={
            "scheduleId": 1, "workoutId": 2, "date": "not-a-date",
        })
        assert resp.status_code == 422

    def test_move_wrong_type_422(self):
        resp = client.post("/api/move", json={
            "scheduleId": "not-an-int", "workoutId": 2, "date": "2026-07-01",
        })
        assert resp.status_code == 422

    def test_shift_range_over_90_days_422(self):
        """Previously a hand-rolled 400; now Pydantic's ge/le on the field
        itself returns 422 before shift_range() is ever called."""
        resp = client.post("/api/shift_range", json={
            "from": "2026-07-01", "to": "2026-07-08", "days": 91,
        })
        assert resp.status_code == 422

    def test_shift_range_bad_date_422(self):
        resp = client.post("/api/shift_range", json={
            "from": "not-a-date", "to": "2026-07-08", "days": 5,
        })
        assert resp.status_code == 422

    def test_annotate_rpe_out_of_range_422(self):
        resp = client.post("/api/annotate", json={
            "activityId": "123", "rpe": 11,
        })
        assert resp.status_code == 422

    def test_annotate_accepts_string_or_int_activity_id(self):
        """Observed both Garmin (int) and intervals.icu ('i123', str) ids
        from the frontend -- the model must accept either."""
        resp = client.post("/api/annotate", json={"activityId": 12345, "rpe": 5})
        assert resp.status_code == 200
        resp = client.post("/api/annotate", json={"activityId": "i999", "rpe": 5})
        assert resp.status_code == 200

    def test_import_metrics_stays_free_form(self):
        """The envelope (date/source) is typed; the inner metrics blob is
        deliberately untyped -- /api/import is a generic third-party inbox."""
        resp = client.post("/api/import", json={
            "date": "2026-07-01", "source": "apple_health",
            "metrics": {"anything": [1, 2, {"nested": True}]},
        })
        assert resp.status_code == 200

    def test_import_bad_date_422(self):
        resp = client.post("/api/import", json={
            "date": "not-a-date", "source": "apple_health",
        })
        assert resp.status_code == 422
