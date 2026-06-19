#!/usr/bin/env python3
"""Test suite for the Garmin workout uploader. No network, no Garmin account.

Run:  python3 test_upload_garmin.py     (or: pytest test_upload_garmin.py)
"""
import json
import re
from datetime import date

from builders import (pace_to_mps, total_distance_m, MILE)
from plan import build_plan, PLAN, WEEKLY_TARGET_MI, PLAN_START
from upload_garmin_workouts import PLAN_NAME_RE, is_plan_name

PLAN_BUILT = build_plan()

_assertions = [0]


def ok(cond, msg):
    _assertions[0] += 1
    assert cond, msg


def iter_steps(steps):
    """Yield every step (flattened, including inside repeats)."""
    for s in steps:
        yield s
        if s.get("type") == "RepeatGroupDTO":
            for c in iter_steps(s["workoutSteps"]):
                yield c


def workout_steps(p):
    return p["payload"]["workoutSegments"][0]["workoutSteps"]


# ----------------------------------------------------- 1. bug regressions

def test_bug1_no_estimated_duration():
    for p in PLAN_BUILT:
        blob = json.dumps(p["payload"])
        ok("estimatedDuration" not in blob, "%s: estimatedDuration found (Bug 1)" % p["name"])


def test_bug2_no_hr_targets_anywhere():
    for p in PLAN_BUILT:
        for s in iter_steps(workout_steps(p)):
            t = s.get("targetType", {})
            ok(t.get("workoutTargetTypeKey") != "heart.rate.zone",
               "%s: HR target found (Bug 2)" % p["name"])


def test_bug2_easy_runs_no_target():
    for p in PLAN_BUILT:
        if p["name"].endswith("Easy") or p["name"].endswith("Shakeout"):
            steps = workout_steps(p)
            ok(len(steps) == 1, "%s: easy run should be 1 step" % p["name"])
            ok(steps[0]["targetType"]["workoutTargetTypeId"] == 1,
               "%s: easy run must be NO_TARGET" % p["name"])


def test_bug2_strides_easy_portions_no_target():
    for p in PLAN_BUILT:
        if "Strides" not in p["name"]:
            continue
        steps = workout_steps(p)
        ok(steps[0]["targetType"]["workoutTargetTypeId"] == 1,
           "%s: strides easy portion must be NO_TARGET" % p["name"])
        ok(steps[-1]["targetType"]["workoutTargetTypeId"] == 1,
           "%s: strides cooldown must be NO_TARGET" % p["name"])


# ---------------------------------------------------- 2. API contract

def test_api_contract():
    for p in PLAN_BUILT:
        w = p["payload"]
        for key in ("workoutName", "sportType", "workoutSegments"):
            ok(key in w, "%s: missing %s" % (p["name"], key))
        ok(w["sportType"]["sportTypeId"] == 1, "%s: not running" % p["name"])
        ok(len(w["workoutSegments"]) == 1, "%s: != 1 segment" % p["name"])
        steps = workout_steps(p)
        ok(len(steps) >= 1, "%s: no steps" % p["name"])
        for s in iter_steps(steps):
            ok(s["endConditionValue"] > 0, "%s: endConditionValue <= 0" % p["name"])
            ok(s["stepType"]["stepTypeId"] in {1, 2, 3, 4, 6},
               "%s: bad stepTypeId" % p["name"])
            # Bug 7: 1 = lap.button — must NEVER appear. 2=time, 3=distance, 7=iterations.
            ok(s["endCondition"]["conditionTypeId"] in {2, 3, 7},
               "%s: bad conditionTypeId" % p["name"])
            if s["type"] == "ExecutableStepDTO":
                for key in ("stepType", "targetType", "endCondition", "endConditionValue"):
                    ok(key in s, "%s: step missing %s" % (p["name"], key))
                ok(s["targetType"]["workoutTargetTypeId"] in {1, 6},
                   "%s: bad targetTypeId" % p["name"])
            else:
                ok(s["type"] == "RepeatGroupDTO", "%s: unknown step type" % p["name"])
                ok(s["numberOfIterations"] > 0, "%s: bad iterations" % p["name"])
                ok(len(s["workoutSteps"]) > 0, "%s: empty repeat" % p["name"])
        json.dumps(w)  # serializable
        ok(True, "serializable")


# ---------------------------------------------------- 3. step ordering

def test_step_order_sequential():
    for p in PLAN_BUILT:
        orders = [s["stepOrder"] for s in iter_steps(workout_steps(p))]
        ok(orders == list(range(1, len(orders) + 1)),
           "%s: stepOrder not sequential: %s" % (p["name"], orders))


# ---------------------------------------------------- 4. speed targets

def test_speed_targets_sane():
    lo, hi = pace_to_mps("13:00"), pace_to_mps("5:30")
    for p in PLAN_BUILT:
        for s in iter_steps(workout_steps(p)):
            t = s.get("targetType", {})
            if t.get("workoutTargetTypeId") != 6:
                continue
            v1, v2 = s["targetValueOne"], s["targetValueTwo"]
            ok(lo <= v1 <= hi and lo <= v2 <= hi,
               "%s: speed out of range" % p["name"])
            ok(v1 <= v2, "%s: targetValueOne > targetValueTwo" % p["name"])


def test_bug7_condition_type_ids():
    """Regression for 'Run Until Lap Press': Garmin honors conditionTypeId
    and ignores the key. Verified vs Runna: 3=distance, 2=time, 7=iterations,
    1=lap.button (forbidden). Pace targets use workoutTargetTypeId 6."""
    for p in PLAN_BUILT:
        for s in iter_steps(workout_steps(p)):
            ec = s["endCondition"]
            ok(ec["conditionTypeId"] != 1,
               "%s: lap.button id used (Bug 7)" % p["name"])
            if ec["conditionTypeKey"] == "distance":
                ok(ec["conditionTypeId"] == 3, "%s: distance must be id 3" % p["name"])
            if ec["conditionTypeKey"] == "time":
                ok(ec["conditionTypeId"] == 2, "%s: time must be id 2" % p["name"])
            if ec["conditionTypeKey"] == "iterations":
                ok(ec["conditionTypeId"] == 7, "%s: iterations must be id 7" % p["name"])
            t = s.get("targetType") or {}
            if t.get("workoutTargetTypeKey") == "pace.zone":
                ok(t["workoutTargetTypeId"] == 6, "%s: pace.zone must be id 6" % p["name"])


def test_bug6_garmin_step_schema():
    """Regression for the 'Run Until Lap Press' smoke failure: target values
    must be at STEP level (never inside targetType), distance steps need
    preferredEndConditionUnit, repeat children carry the group childStepId."""
    for p in PLAN_BUILT:
        for s in iter_steps(workout_steps(p)):
            tt = s.get("targetType") or {}
            ok("targetValueOne" not in tt and "targetValueTwo" not in tt,
               "%s: target values nested inside targetType" % p["name"])
            if s["type"] != "ExecutableStepDTO":
                continue
            ok("targetValueOne" in s and "targetValueTwo" in s,
               "%s: step missing targetValue fields" % p["name"])
            if s["endCondition"]["conditionTypeKey"] == "distance":
                ok((s.get("preferredEndConditionUnit") or {}).get("unitKey") == "mile",
                   "%s: distance step missing preferredEndConditionUnit" % p["name"])
        for top in workout_steps(p):
            if top.get("type") == "RepeatGroupDTO":
                gid = top["childStepId"]
                ok(isinstance(gid, int) and gid > 0,
                   "%s: repeat missing childStepId" % p["name"])
                for c in top["workoutSteps"]:
                    ok(c.get("childStepId") == gid,
                       "%s: repeat child childStepId mismatch" % p["name"])


# ---------------------------------------------------- 5. distance sanity

def test_distance_matches_name():
    # Names like "W7 Sat 16mi MP Finish" claim total distance right after day.
    pat = re.compile(r"^W\d+ \w+ (\d+(?:\.\d+)?)mi ")
    for p in PLAN_BUILT:
        m = pat.match(p["name"])
        if not m:
            continue
        claimed = float(m.group(1))
        ok(abs(p["distance_mi"] - claimed) / claimed <= 0.20,
           "%s: computed %.1fmi vs claimed %.0fmi" % (p["name"], p["distance_mi"], claimed))


def test_weekly_volume_near_target():
    vols = {}
    for p in PLAN_BUILT:
        vols[p["week"]] = vols.get(p["week"], 0) + p["distance_mi"]
    for week, target in WEEKLY_TARGET_MI.items():
        ok(abs(vols[week] - target) / target <= 0.10,
           "week %d: %.1f mi vs target %d" % (week, vols[week], target))


# ---------------------------------------------------- 6. schedule

def test_schedule():
    ok(PLAN_START == date(2026, 6, 15), "plan start")
    ok(PLAN_START.weekday() == 0, "start must be Monday")
    dates = [p["date"] for p in PLAN_BUILT]
    ok(max(dates) == date(2026, 10, 24), "last workout must be Oct 24")
    ok(len(set(dates)) == len(dates), "duplicate dates")
    for d in dates:
        ok(d.weekday() in {0, 1, 3, 4, 5}, "%s falls on Wed/Sun" % d)


# ---------------------------------------------------- 7. data integrity

def test_integrity():
    ok(len(PLAN_BUILT) == 93, "expected 93 workouts, got %d" % len(PLAN_BUILT))
    names = [p["name"] for p in PLAN_BUILT]
    ok(len(set(names)) == len(names), "duplicate workout names")
    suffixes = " | ".join(p["name"] for p in PLAN_BUILT)
    for marker in ("Easy", "Strides", "LR", "MP Finish", "Tempo", "Cat Hill",
                   "Harlem Hill", "Mixed Hills", "Hill Tempo", "x Mile", "x800"):
        ok(marker in suffixes, "builder output %r never used" % marker)


# ---------------------------------------------------- 8. delete filter

def test_delete_filter_safety():
    for name in ("W1 Mon 4mi Easy", "W10 Sat 18mi MP Finish", "W19 Sat 2mi Shakeout"):
        ok(is_plan_name(name), "filter must match %r" % name)
    for p in PLAN_BUILT:
        ok(is_plan_name(p["name"]), "filter must match %r" % p["name"])
    for name in ("Weekly Run", "Workout A", "My Tempo Run", "Wednesday Hills",
                 "W20 Mon Easy", "W1 Sun Easy", "W1Mon Easy", "Warmup Routine",
                 # Runna's naming — observed on Earwin's account; must be safe:
                 "W10 Sat Long Run - 14mi Hilly Long Run (14mi)",
                 "W11 Tue Easy Run - 7.5mi Easy Run (7.5mi)",
                 "W9 Thu Easy Run - 6mi Easy Run (6mi)"):
        ok(not is_plan_name(name), "filter must NOT match %r" % name)


# ---------------------------------------------------- 9. deep inspections

def by_name(name):
    return next(p for p in PLAN_BUILT if p["name"] == name)


def test_w1_mon_easy():
    p = by_name("W1 Mon 4mi Easy")
    steps = workout_steps(p)
    ok(len(steps) == 1, "1 step")
    ok(abs(steps[0]["endConditionValue"] - 4 * MILE) < 1, "~4mi")
    ok(steps[0]["targetType"]["workoutTargetTypeId"] == 1, "NO_TARGET")


def test_w4_tue_6x800():
    p = by_name("W4 Tue 6x800")
    steps = workout_steps(p)
    ok(len(steps) == 3, "warmup/repeat/cooldown")
    rep = steps[1]
    ok(rep["numberOfIterations"] == 6, "6 iterations")
    work, rec = rep["workoutSteps"]
    ok(work["endConditionValue"] == 800.0, "800m intervals")
    ok(rec["endConditionValue"] == 400.0, "400m recovery")
    ok(work["targetType"]["workoutTargetTypeId"] == 6, "interval speed target")
    ok(steps[0]["targetType"]["workoutTargetTypeId"] == 6, "warmup pace target")
    ok(steps[2]["targetType"]["workoutTargetTypeId"] == 6, "cooldown pace target")


def test_w7_sat_mp_finish():
    p = by_name("W7 Sat 16mi MP Finish")
    steps = workout_steps(p)
    ok(len(steps) == 2, "2 steps")
    ok(abs(steps[0]["endConditionValue"] - 12 * MILE) < 1, "~12mi easy")
    ok(abs(steps[1]["endConditionValue"] - 4 * MILE) < 1, "~4mi MP")
    for s in steps:
        ok(s["targetType"]["workoutTargetTypeId"] == 6, "speed target")


def test_w1_tue_strides():
    p = by_name("W1 Tue 5mi Strides")
    steps = workout_steps(p)
    ok(len(steps) == 3, "3 top-level steps")
    ok(steps[0]["targetType"]["workoutTargetTypeId"] == 1, "easy NO_TARGET")
    ok(steps[1]["numberOfIterations"] == 4, "4 strides")
    ok(steps[2]["targetType"]["workoutTargetTypeId"] == 1, "cooldown NO_TARGET")


# --------------------------------------------------------------- runner

def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%d/%d tests passed, %d assertions checked." %
          (len(tests) - failed, len(tests), _assertions[0]))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
