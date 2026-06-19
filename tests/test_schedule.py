"""Tests for schedule logic — particularly _next_clean_slot invariants."""
from datetime import date
from src.services.schedule import next_clean_slot, _is_hard


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _sched(items):
    """Build a minimal schedule list from (scheduleId, date, title) tuples."""
    return [{"scheduleId": sid, "workoutId": sid * 10,
             "date": d, "title": t} for sid, d, t in items]


class TestIsHard:
    def test_tempo_is_hard(self):
        assert _is_hard("W1 Tue Tempo 5mi")

    def test_intervals_are_hard(self):
        assert _is_hard("W2 Thu 8x400m")

    def test_hill_is_hard(self):
        assert _is_hard("W3 Tue Hill Repeats")

    def test_easy_is_not_hard(self):
        assert not _is_hard("W1 Mon Easy 4mi")

    def test_long_run_is_hard(self):
        assert _is_hard("W4 Sat 14mi LR")

    def test_mp_finish_is_hard(self):
        assert _is_hard("W10 Sat 18mi MP Finish")


class TestNextCleanSlot:
    def test_finds_next_free_day(self):
        """With no schedule, slot is always tomorrow."""
        sched = _sched([])
        result = next_clean_slot(sched, 0, date(2026, 6, 17))
        assert result == "2026-06-18"

    def test_skips_occupied_days(self):
        """Slot jumps past occupied days."""
        sched = _sched([
            (1, "2026-06-18", "Easy 4mi"),
            (2, "2026-06-19", "Easy 4mi"),
        ])
        result = next_clean_slot(sched, 99, date(2026, 6, 17))
        assert result == "2026-06-20"

    def test_no_adjacent_hard_day(self):
        """Slot is never placed next to a hard workout."""
        sched = _sched([
            (1, "2026-06-20", "8x400m"),  # hard on the 20th
        ])
        result = next_clean_slot(sched, 99, date(2026, 6, 17))
        # 18th is fine, 19th is adjacent to hard 20th → skip, 18th wins
        assert result == "2026-06-18"

    def test_slot_after_hard_also_skipped(self):
        """Day immediately after a hard workout is also blocked."""
        sched = _sched([
            (1, "2026-06-18", "Tempo 5mi"),  # hard on 18th
        ])
        result = next_clean_slot(sched, 99, date(2026, 6, 17))
        # 18th occupied, 19th adjacent to hard 18th → skip, 20th wins
        assert result == "2026-06-20"

    def test_excludes_self(self):
        """The workout being moved doesn't block its own slot search."""
        sched = _sched([
            (7, "2026-06-18", "8x400m"),  # this is the workout being moved
        ])
        result = next_clean_slot(sched, 7, date(2026, 6, 17))
        # 18th should be available since we exclude scheduleId=7
        assert result == "2026-06-18"

    def test_prefer_weekend(self):
        """prefer_weekend skips weekdays within the first 7 days if possible."""
        # 2026-06-17 is a Wednesday; next weekend is Sat June 20 / Sun June 21
        sched = _sched([])
        result = next_clean_slot(sched, 0, date(2026, 6, 17), prefer_weekend=True)
        target = date.fromisoformat(result)
        assert target.weekday() in (5, 6), f"expected weekend, got weekday {target.weekday()}"

    def test_returns_none_when_no_slot(self):
        """Returns None if no clean slot exists within 10 days."""
        # Pack all 10 days with hard workouts
        hard_days = [
            (i, (date(2026, 6, 17) + __import__("datetime").timedelta(days=i)).isoformat(), "Tempo")
            for i in range(1, 11)
        ]
        sched = _sched(hard_days)
        result = next_clean_slot(sched, 99, date(2026, 6, 17))
        assert result is None


class TestPlanDeterminism:
    def test_plan_summary_is_deterministic(self):
        """plan_summary() returns the same data on repeated calls."""
        from src.services.plan_svc import plan_summary
        r1 = plan_summary()
        r2 = plan_summary()
        assert r1["plannedWeekly"] == r2["plannedWeekly"]
        assert r1["race"] == r2["race"]
        assert r1["start"] == r2["start"]

    def test_plan_has_18_weeks(self):
        from src.services.plan_svc import plan_summary
        ps = plan_summary()
        assert len(ps["plannedWeekly"]) >= 18

    def test_plan_mileage_reasonable(self):
        """No week should have 0 miles or more than 80 miles."""
        from src.services.plan_svc import plan_summary
        ps = plan_summary()
        for wk, mi in ps["plannedWeekly"].items():
            assert 0 < mi <= 80, f"Week {wk}: {mi}mi out of range"
