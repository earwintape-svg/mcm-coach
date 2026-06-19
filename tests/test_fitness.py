"""Tests for VDOT math and personal records."""
from src.services.fitness import _vdot_of, _predict_secs, fetch_prs


class TestVdot:
    def test_reasonable_range(self):
        """A 4-hour marathoner should have a VDOT around 37-38."""
        vd = _vdot_of(42195, 4 * 3600)
        assert 36 <= vd <= 39

    def test_sub325_vdot(self):
        """Sub-3:25 marathon (goal) maps to VDOT ~47-48."""
        vd = _vdot_of(42195, 3 * 3600 + 25 * 60)
        assert 45 <= vd <= 49

    def test_predict_round_trips(self):
        """predict_secs(42195, vdot_of(42195, t)) ≈ t (within 30s)."""
        original_sec = 3 * 3600 + 25 * 60  # 3:25:00
        vd = _vdot_of(42195, original_sec)
        predicted = _predict_secs(42195, vd)
        assert abs(predicted - original_sec) < 30

    def test_faster_runner_higher_vdot(self):
        """A faster runner at the same distance has a higher VDOT."""
        vd_fast = _vdot_of(42195, 3 * 3600)       # 3:00 marathon
        vd_slow = _vdot_of(42195, 4 * 3600 + 30 * 60)  # 4:30 marathon
        assert vd_fast > vd_slow

    def test_short_run_vdot_valid(self):
        """VDOT from a 5K is in a plausible range."""
        vd = _vdot_of(5000, 25 * 60)  # 25-min 5K
        assert 30 <= vd <= 65


class TestFetchPrs:
    def test_empty_store_returns_empty(self):
        """No runs → empty PRs dict, not an error."""
        result = fetch_prs()
        assert result == {}

    def test_detects_mile_pr(self, isolated_store):
        import store
        store.upsert_runs([
            {"activityId": "1", "date": "2026-06-01", "mi": 5.0,
             "paceSec": 480, "pace": "8:00", "name": "Easy"},
            {"activityId": "2", "date": "2026-06-08", "mi": 5.0,
             "paceSec": 460, "pace": "7:40", "name": "Fast"},  # faster
        ])
        prs = fetch_prs()
        assert "mile" in prs
        assert prs["mile"]["pace"] == 460  # faster run wins

    def test_pr_requires_sufficient_distance(self, isolated_store):
        """A 0.3mi run should not produce a mile PR."""
        import store
        store.upsert_runs([
            {"activityId": "1", "date": "2026-06-01", "mi": 0.3,
             "paceSec": 300, "pace": "5:00", "name": "Warmup"},
        ])
        prs = fetch_prs()
        assert "mile" not in prs

    def test_long_run_tracked(self, isolated_store):
        import store
        store.upsert_runs([
            {"activityId": "1", "date": "2026-06-01", "mi": 14.0,
             "paceSec": 570, "pace": "9:30", "name": "LR"},
            {"activityId": "2", "date": "2026-06-08", "mi": 16.0,
             "paceSec": 580, "pace": "9:40", "name": "LR"},
        ])
        prs = fetch_prs()
        assert "long" in prs
        assert prs["long"]["mi"] == 16.0  # longer run wins

    def test_prs_link_to_correct_activity(self, isolated_store):
        import store
        store.upsert_runs([
            {"activityId": "fast1", "date": "2026-06-01", "mi": 6.0,
             "paceSec": 420, "pace": "7:00", "name": "Fast"},
            {"activityId": "slow1", "date": "2026-06-08", "mi": 6.0,
             "paceSec": 540, "pace": "9:00", "name": "Slow"},
        ])
        prs = fetch_prs()
        assert prs["mile"]["activityId"] == "fast1"
        assert prs["5k"]["activityId"] == "fast1"
