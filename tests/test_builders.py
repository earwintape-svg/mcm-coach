"""Garmin workout-payload schema regression tests. No network, no Garmin
account -- pure builders.py/plan.py output checks.

Ported from archive/test_upload_garmin.py (T2): that file was *moved* to
archive/ during the root cleanup (intentional, not lost), but its
assertions were never carried into the new pytest suite -- tests/ only
had test_fitness.py (VDOT math) and test_schedule.py (scheduling logic),
neither of which touches the Garmin payload schema at all. This file
closes that gap.

These specifically guard the "Known bugs" invariants in builders.py's
docstring (load-bearing, learned the hard way against the real Garmin
Connect API):
  Bug 1 -- never send estimatedDurationInSecs
  Bug 2 -- easy/recovery/easy-stride portions use NO_TARGET, never HR
  Bug 6 -- target values live at step level, not inside targetType
  Bug 7 -- conditionTypeId (not the key) is what Garmin honors:
           1=lap.button (forbidden), 2=time, 3=distance, 7=iterations
plus the delete-filter safety net that keeps schedule sync/cleanup from
ever touching Runna's own calendar entries.
"""
import json
import re
from datetime import date

import pytest

from builders import pace_to_mps, MILE
from plan import build_plan, WEEKLY_TARGET_MI, PLAN_START
from upload_garmin_workouts import is_plan_name

PLAN_BUILT = build_plan()


def iter_steps(steps):
    """Yield every step (flattened, including inside repeats)."""
    for s in steps:
        yield s
        if s.get("type") == "RepeatGroupDTO":
            for c in iter_steps(s["workoutSteps"]):
                yield c


def workout_steps(p):
    return p["payload"]["workoutSegments"][0]["workoutSteps"]


def by_name(name):
    return next(p for p in PLAN_BUILT if p["name"] == name)


# --------------------------------------------------------- bug regressions

class TestBugRegressions:
    def test_bug1_no_estimated_duration(self):
        for p in PLAN_BUILT:
            blob = json.dumps(p["payload"])
            assert "estimatedDuration" not in blob, \
                "%s: estimatedDuration found (Bug 1)" % p["name"]

    def test_bug2_no_hr_targets_anywhere(self):
        for p in PLAN_BUILT:
            for s in iter_steps(workout_steps(p)):
                t = s.get("targetType", {})
                assert t.get("workoutTargetTypeKey") != "heart.rate.zone", \
                    "%s: HR target found (Bug 2)" % p["name"]

    def test_bug2_easy_runs_no_target(self):
        for p in PLAN_BUILT:
            if p["name"].endswith("Easy") or p["name"].endswith("Shakeout"):
                steps = workout_steps(p)
                assert len(steps) == 1, "%s: easy run should be 1 step" % p["name"]
                assert steps[0]["targetType"]["workoutTargetTypeId"] == 1, \
                    "%s: easy run must be NO_TARGET" % p["name"]

    def test_bug2_strides_easy_portions_no_target(self):
        for p in PLAN_BUILT:
            if "Strides" not in p["name"]:
                continue
            steps = workout_steps(p)
            assert steps[0]["targetType"]["workoutTargetTypeId"] == 1, \
                "%s: strides easy portion must be NO_TARGET" % p["name"]
            assert steps[-1]["targetType"]["workoutTargetTypeId"] == 1, \
                "%s: strides cooldown must be NO_TARGET" % p["name"]

    def test_bug6_garmin_step_schema(self):
        """Target values at STEP level (never inside targetType); distance
        steps carry preferredEndConditionUnit; repeat children carry the
        group's childStepId."""
        for p in PLAN_BUILT:
            for s in iter_steps(workout_steps(p)):
                tt = s.get("targetType") or {}
                assert "targetValueOne" not in tt and "targetValueTwo" not in tt, \
                    "%s: target values nested inside targetType" % p["name"]
                if s["type"] != "ExecutableStepDTO":
                    continue
                assert "targetValueOne" in s and "targetValueTwo" in s, \
                    "%s: step missing targetValue fields" % p["name"]
                if s["endCondition"]["conditionTypeKey"] == "distance":
                    assert (s.get("preferredEndConditionUnit") or {}).get("unitKey") == "mile", \
                        "%s: distance step missing preferredEndConditionUnit" % p["name"]
            for top in workout_steps(p):
                if top.get("type") == "RepeatGroupDTO":
                    gid = top["childStepId"]
                    assert isinstance(gid, int) and gid > 0, \
                        "%s: repeat missing childStepId" % p["name"]
                    for c in top["workoutSteps"]:
                        assert c.get("childStepId") == gid, \
                            "%s: repeat child childStepId mismatch" % p["name"]

    def test_bug7_condition_type_ids(self):
        """Regression for 'Run Until Lap Press': Garmin honors
        conditionTypeId and ignores the key. 3=distance, 2=time,
        7=iterations, 1=lap.button (forbidden). Pace targets use
        workoutTargetTypeId 6."""
        for p in PLAN_BUILT:
            for s in iter_steps(workout_steps(p)):
                ec = s["endCondition"]
                assert ec["conditionTypeId"] != 1, \
                    "%s: lap.button id used (Bug 7)" % p["name"]
                if ec["conditionTypeKey"] == "distance":
                    assert ec["conditionTypeId"] == 3, "%s: distance must be id 3" % p["name"]
                if ec["conditionTypeKey"] == "time":
                    assert ec["conditionTypeId"] == 2, "%s: time must be id 2" % p["name"]
                if ec["conditionTypeKey"] == "iterations":
                    assert ec["conditionTypeId"] == 7, "%s: iterations must be id 7" % p["name"]
                t = s.get("targetType") or {}
                if t.get("workoutTargetTypeKey") == "pace.zone":
                    assert t["workoutTargetTypeId"] == 6, \
                        "%s: pace.zone must be id 6" % p["name"]


# -------------------------------------------------------------- API contract

class TestApiContract:
    def test_api_contract(self):
        for p in PLAN_BUILT:
            w = p["payload"]
            for key in ("workoutName", "sportType", "workoutSegments"):
                assert key in w, "%s: missing %s" % (p["name"], key)
            assert w["sportType"]["sportTypeId"] == 1, "%s: not running" % p["name"]
            assert len(w["workoutSegments"]) == 1, "%s: != 1 segment" % p["name"]
            steps = workout_steps(p)
            assert len(steps) >= 1, "%s: no steps" % p["name"]
            for s in iter_steps(steps):
                assert s["endConditionValue"] > 0, "%s: endConditionValue <= 0" % p["name"]
                assert s["stepType"]["stepTypeId"] in {1, 2, 3, 4, 6}, \
                    "%s: bad stepTypeId" % p["name"]
                assert s["endCondition"]["conditionTypeId"] in {2, 3, 7}, \
                    "%s: bad conditionTypeId" % p["name"]
                if s["type"] == "ExecutableStepDTO":
                    for key in ("stepType", "targetType", "endCondition", "endConditionValue"):
                        assert key in s, "%s: step missing %s" % (p["name"], key)
                    assert s["targetType"]["workoutTargetTypeId"] in {1, 6}, \
                        "%s: bad targetTypeId" % p["name"]
                else:
                    assert s["type"] == "RepeatGroupDTO", "%s: unknown step type" % p["name"]
                    assert s["numberOfIterations"] > 0, "%s: bad iterations" % p["name"]
                    assert len(s["workoutSteps"]) > 0, "%s: empty repeat" % p["name"]
            json.dumps(w)  # must be serializable

    def test_step_order_sequential(self):
        for p in PLAN_BUILT:
            orders = [s["stepOrder"] for s in iter_steps(workout_steps(p))]
            assert orders == list(range(1, len(orders) + 1)), \
                "%s: stepOrder not sequential: %s" % (p["name"], orders)

    def test_speed_targets_sane(self):
        lo, hi = pace_to_mps("13:00"), pace_to_mps("5:30")
        for p in PLAN_BUILT:
            for s in iter_steps(workout_steps(p)):
                t = s.get("targetType", {})
                if t.get("workoutTargetTypeId") != 6:
                    continue
                v1, v2 = s["targetValueOne"], s["targetValueTwo"]
                assert lo <= v1 <= hi and lo <= v2 <= hi, "%s: speed out of range" % p["name"]
                assert v1 <= v2, "%s: targetValueOne > targetValueTwo" % p["name"]


# ------------------------------------------------------------ distance/volume

class TestDistanceAndVolume:
    def test_distance_matches_name(self):
        # Names like "W7 Sat 16mi MP Finish" claim total distance right after day.
        pat = re.compile(r"^W\d+ \w+ (\d+(?:\.\d+)?)mi ")
        for p in PLAN_BUILT:
            m = pat.match(p["name"])
            if not m:
                continue
            claimed = float(m.group(1))
            assert abs(p["distance_mi"] - claimed) / claimed <= 0.20, \
                "%s: computed %.1fmi vs claimed %.0fmi" % (p["name"], p["distance_mi"], claimed)

    def test_weekly_volume_near_target(self):
        vols = {}
        for p in PLAN_BUILT:
            vols[p["week"]] = vols.get(p["week"], 0) + p["distance_mi"]
        for week, target in WEEKLY_TARGET_MI.items():
            assert abs(vols[week] - target) / target <= 0.10, \
                "week %d: %.1f mi vs target %d" % (week, vols[week], target)


# ------------------------------------------------------------------ schedule

class TestScheduleIntegrity:
    def test_schedule(self):
        assert PLAN_START == date(2026, 6, 15), "plan start"
        assert PLAN_START.weekday() == 0, "start must be Monday"
        dates = [p["date"] for p in PLAN_BUILT]
        assert max(dates) == date(2026, 10, 24), "last workout must be Oct 24"
        assert len(set(dates)) == len(dates), "duplicate dates"
        for d in dates:
            assert d.weekday() in {0, 1, 3, 4, 5}, "%s falls on Wed/Sun" % d

    def test_integrity(self):
        assert len(PLAN_BUILT) == 93, "expected 93 workouts, got %d" % len(PLAN_BUILT)
        names = [p["name"] for p in PLAN_BUILT]
        assert len(set(names)) == len(names), "duplicate workout names"
        suffixes = " | ".join(p["name"] for p in PLAN_BUILT)
        for marker in ("Easy", "Strides", "LR", "MP Finish", "Tempo", "Cat Hill",
                       "Harlem Hill", "Mixed Hills", "Hill Tempo", "x Mile", "x800"):
            assert marker in suffixes, "builder output %r never used" % marker


# --------------------------------------------------------------- delete filter

class TestDeleteFilterSafety:
    """Schedule sync/cleanup must only ever touch OUR workouts -- never
    Runna's, which uses a similar 'W<n> <day> ...' prefix but with a
    ' - ' separator that PLAN_NAME_RE/is_plan_name must reject."""

    @pytest.mark.parametrize("name", [
        "W1 Mon 4mi Easy", "W10 Sat 18mi MP Finish", "W19 Sat 2mi Shakeout",
    ])
    def test_must_match_our_names(self, name):
        assert is_plan_name(name), "filter must match %r" % name

    def test_must_match_every_built_workout(self):
        for p in PLAN_BUILT:
            assert is_plan_name(p["name"]), "filter must match %r" % p["name"]

    @pytest.mark.parametrize("name", [
        "Weekly Run", "Workout A", "My Tempo Run", "Wednesday Hills",
        "W20 Mon Easy", "W1 Sun Easy", "W1Mon Easy", "Warmup Routine",
        # Runna's naming -- observed on Earwin's account; must stay safe:
        "W10 Sat Long Run - 14mi Hilly Long Run (14mi)",
        "W11 Tue Easy Run - 7.5mi Easy Run (7.5mi)",
        "W9 Thu Easy Run - 6mi Easy Run (6mi)",
    ])
    def test_must_not_match_other_names(self, name):
        assert not is_plan_name(name), "filter must NOT match %r" % name


# ------------------------------------------------------------- deep inspections

class TestDeepInspections:
    def test_w1_mon_easy(self):
        p = by_name("W1 Mon 4mi Easy")
        steps = workout_steps(p)
        assert len(steps) == 1
        assert abs(steps[0]["endConditionValue"] - 4 * MILE) < 1
        assert steps[0]["targetType"]["workoutTargetTypeId"] == 1

    def test_w4_tue_6x800(self):
        p = by_name("W4 Tue 6x800")
        steps = workout_steps(p)
        assert len(steps) == 3, "warmup/repeat/cooldown"
        rep = steps[1]
        assert rep["numberOfIterations"] == 6
        work, rec = rep["workoutSteps"]
        assert work["endConditionValue"] == 800.0
        assert rec["endConditionValue"] == 400.0
        assert work["targetType"]["workoutTargetTypeId"] == 6
        assert steps[0]["targetType"]["workoutTargetTypeId"] == 6, "warmup pace target"
        assert steps[2]["targetType"]["workoutTargetTypeId"] == 6, "cooldown pace target"

    def test_w7_sat_mp_finish(self):
        p = by_name("W7 Sat 16mi MP Finish")
        steps = workout_steps(p)
        assert len(steps) == 2
        assert abs(steps[0]["endConditionValue"] - 12 * MILE) < 1, "~12mi easy"
        assert abs(steps[1]["endConditionValue"] - 4 * MILE) < 1, "~4mi MP"
        for s in steps:
            assert s["targetType"]["workoutTargetTypeId"] == 6

    def test_w1_tue_strides(self):
        p = by_name("W1 Tue 5mi Strides")
        steps = workout_steps(p)
        assert len(steps) == 3, "3 top-level steps"
        assert steps[0]["targetType"]["workoutTargetTypeId"] == 1, "easy NO_TARGET"
        assert steps[1]["numberOfIterations"] == 4, "4 strides"
        assert steps[2]["targetType"]["workoutTargetTypeId"] == 1, "cooldown NO_TARGET"
