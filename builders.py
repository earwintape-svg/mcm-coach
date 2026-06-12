"""Workout JSON builders for the Garmin Connect workout-service API.

Python 3.9 compatible. No external dependencies.

All builders return a Workout(suffix, steps) — `suffix` becomes part of the
workout name ("W4 Tue " + suffix), `steps` is the workoutSteps tree WITHOUT
stepOrder. `finalize()` assigns sequential stepOrder (depth-first) and
`make_workout()` wraps steps in the full payload.

Critical, hard-won rules (see spec "Known bugs"):
  * NEVER include estimatedDurationInSecs anywhere in the payload.
  * Easy runs / recoveries / strides-easy portions use NO_TARGET, never HR.
  * targetValueOne = slower speed (m/s), targetValueTwo = faster speed.
"""
import copy
import json
from collections import namedtuple

MILE = 1609.34

Workout = namedtuple("Workout", ["suffix", "steps"])

# ---------------------------------------------------------------- constants

STEP_WARMUP = {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1}
STEP_COOLDOWN = {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2}
STEP_INTERVAL = {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3}
STEP_RECOVERY = {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4}
STEP_REPEAT = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}

# CRITICAL (Bug 7): Garmin honors conditionTypeId and IGNORES the key string.
# 1 = lap.button (NOT distance!), 2 = time, 3 = distance, 7 = iterations.
# Verified against Runna's stored JSON. The original spec's table was wrong,
# which made every step render as "Run Until Lap Press".
END_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance",
                "displayOrder": 3, "displayable": True}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time",
            "displayOrder": 2, "displayable": True}
END_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations",
                  "displayOrder": 7, "displayable": False}

NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target",
             "displayOrder": 1}

SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}

# Pace ranges (fast, slow) as "M:SS" per mile. Easy pace exists for reference
# only — easy steps are uploaded with NO_TARGET (Bug 2).
PACES = {
    "easy":     ("9:45", "10:30"),
    "wu":       ("9:30", "10:30"),
    "lr":       ("8:45", "9:30"),
    "mp":       ("7:45", "7:55"),
    "tempo":    ("7:35", "7:50"),
    "interval": ("6:35", "7:10"),
    "strides":  ("6:30", "7:00"),
    "hills":    ("7:00", "7:40"),
    "hill_tempo": ("7:30", "8:30"),
}

# ------------------------------------------------------------- conversions

def pace_to_mps(pace):
    """'7:50' per mile -> meters/second."""
    minutes, seconds = pace.split(":")
    return MILE / (int(minutes) * 60 + int(seconds))


def mi(miles):
    """Miles -> meters, rounded to 2 decimals."""
    return round(miles * MILE, 2)


def speed_target(pace_a, pace_b):
    """Speed-zone target. Order-insensitive: slower speed always goes in
    targetValueOne (Garmin convention: low bound first)."""
    v1, v2 = sorted([pace_to_mps(pace_a), pace_to_mps(pace_b)])
    # pace.zone (id 6) is what running apps use for pace ranges; values in m/s.
    # Same Bug 7 caveat: the ID is what counts, not the key.
    t = dict(NO_TARGET)
    t.update({"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone",
              "displayOrder": 6,
              "targetValueOne": round(v1, 3), "targetValueTwo": round(v2, 3)})
    return t


def zone_target(zone_key):
    return speed_target(*PACES[zone_key])

# ------------------------------------------------------------ step factory

def step(step_type, distance_m=None, seconds=None, target=None, desc=""):
    """ExecutableStepDTO matching Garmin Connect's stored schema:
    targetValueOne/Two live at STEP level (not inside targetType), and
    distance steps carry preferredEndConditionUnit. Nesting the target
    values inside targetType makes the watch fall back to 'lap press'."""
    if (distance_m is None) == (seconds is None):
        raise ValueError("step() needs exactly one of distance_m or seconds")
    if distance_m is not None:
        end, val = END_DISTANCE, round(float(distance_m), 2)
        unit = {"unitKey": "mile"}
    else:
        end, val = END_TIME, float(seconds)
        unit = None
    if val <= 0:
        raise ValueError("endConditionValue must be > 0")
    t = copy.deepcopy(target if target is not None else NO_TARGET)
    v1 = t.pop("targetValueOne", None)
    v2 = t.pop("targetValueTwo", None)
    return {
        "type": "ExecutableStepDTO",
        "stepId": None,
        "childStepId": None,
        "stepType": dict(step_type),
        "endCondition": dict(end),
        "endConditionValue": val,
        "endConditionCompare": None,
        "endConditionZone": None,
        "preferredEndConditionUnit": unit,
        "targetType": t,
        "targetValueOne": v1,
        "targetValueTwo": v2,
        "zoneNumber": None,
        "description": desc,
    }


def repeat(n, steps):
    """RepeatGroupDTO. childStepId is assigned in finalize()."""
    if n <= 0:
        raise ValueError("numberOfIterations must be > 0")
    return {
        "type": "RepeatGroupDTO",
        "stepId": None,
        "childStepId": None,
        "stepType": dict(STEP_REPEAT),
        "numberOfIterations": n,
        "endCondition": dict(END_ITERATIONS),
        "endConditionValue": float(n),
        "smartRepeat": False,
        "workoutSteps": copy.deepcopy(steps),
    }


def finalize(steps):
    """Deep-copy; assign sequential stepOrder (depth-first, starting at 1)
    and childStepId group ids (each RepeatGroup gets an id; its children
    carry the same id — Garmin's linking convention)."""
    steps = copy.deepcopy(steps)
    counter = {"step": 0, "group": 0}

    def walk(items, child_id=None):
        for s in items:
            counter["step"] += 1
            s["stepOrder"] = counter["step"]
            if child_id is not None:
                s["childStepId"] = child_id
            if s.get("type") == "RepeatGroupDTO":
                counter["group"] += 1
                s["childStepId"] = counter["group"]
                walk(s["workoutSteps"], counter["group"])
    walk(steps)
    return steps


def make_workout(name, steps):
    """Full payload. Deliberately NO estimatedDurationInSecs (Bug 1)."""
    return {
        "workoutName": name,
        "sportType": dict(SPORT_RUNNING),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(SPORT_RUNNING),
            "workoutSteps": finalize(steps),
        }],
    }


def total_distance_m(steps):
    """Sum of distance-based steps, repeat groups multiplied out."""
    total = 0.0
    for s in steps:
        if s.get("type") == "RepeatGroupDTO":
            total += s["numberOfIterations"] * total_distance_m(s["workoutSteps"])
        elif s["endCondition"]["conditionTypeKey"] == "distance":
            total += s["endConditionValue"]
    return total

# -------------------------------------------------------- helper formatting

def _fmt(x):
    return str(int(x)) if float(x).is_integer() else ("%g" % x)


def _rep_label(dist_m):
    if dist_m == MILE:
        return "Mile"
    d = int(round(dist_m))
    return "%dkm" % (d // 1000) if d % 1000 == 0 else str(d)

# ----------------------------------------------------------- atomic pieces

def wu(miles=1.5):
    return step(STEP_WARMUP, distance_m=mi(miles),
                target=zone_target("wu"), desc="Warmup")


def cd(miles=1.5):
    return step(STEP_COOLDOWN, distance_m=mi(miles),
                target=zone_target("wu"), desc="Cooldown")

# -------------------------------------------------------- workout builders

def easy_run(miles, label="Easy"):
    """Single step, NO_TARGET — watch shows distance only (Bug 2)."""
    s = step(STEP_INTERVAL, distance_m=mi(miles), target=NO_TARGET,
             desc="Easy — conversational")
    return Workout("%smi %s" % (_fmt(miles), label), [s])


def easy_strides(miles):
    """Easy run + 4x100m strides + 0.25mi cooldown. Easy and cooldown
    portions are NO_TARGET (Bug 2)."""
    easy_mi = miles - 0.5  # strides 4x100m ~= 0.25mi + 0.25mi cooldown
    if easy_mi <= 0:
        raise ValueError("strides workout too short")
    steps = [
        step(STEP_INTERVAL, distance_m=mi(easy_mi), target=NO_TARGET,
             desc="Easy — conversational"),
        repeat(4, [
            step(STEP_INTERVAL, distance_m=100.0,
                 target=zone_target("strides"), desc="Stride — fast, relaxed"),
            step(STEP_RECOVERY, seconds=60.0, target=NO_TARGET,
                 desc="Walk it off"),
        ]),
        step(STEP_COOLDOWN, distance_m=mi(0.25), target=NO_TARGET,
             desc="Easy cooldown"),
    ]
    return Workout("%smi Strides" % _fmt(miles), steps)


def interval_workout(reps, dist_m, rest_m, pace_key="interval"):
    """WU 1.5 -> reps x (interval @ pace + jog recovery) -> CD 1.5."""
    steps = [
        wu(),
        repeat(reps, [
            step(STEP_INTERVAL, distance_m=float(dist_m),
                 target=zone_target(pace_key), desc="On pace, controlled"),
            step(STEP_RECOVERY, distance_m=float(rest_m), target=NO_TARGET,
                 desc="Easy jog recovery"),
        ]),
        cd(),
    ]
    return Workout("%dx%s" % (reps, _rep_label(dist_m)), steps)


def mile_repeats(reps):
    w = interval_workout(reps, MILE, 800.0)
    return Workout("%dx Mile" % reps, w.steps)


def tempo_run(total_mi, tempo_mi):
    """WU and CD split the non-tempo distance evenly."""
    pad = (total_mi - tempo_mi) / 2.0
    if pad <= 0:
        raise ValueError("tempo_mi must be < total_mi")
    steps = [
        wu(pad),
        step(STEP_INTERVAL, distance_m=mi(tempo_mi),
             target=zone_target("tempo"), desc="Tempo — comfortably hard"),
        cd(pad),
    ]
    return Workout("Tempo %smi" % _fmt(tempo_mi), steps)


def long_run_easy(miles):
    s = step(STEP_INTERVAL, distance_m=mi(miles), target=zone_target("lr"),
             desc="Long run — steady")
    return Workout("%smi LR" % _fmt(miles), [s])


def long_run_mp_finish(total_mi, mp_mi):
    steps = [
        step(STEP_INTERVAL, distance_m=mi(total_mi - mp_mi),
             target=zone_target("lr"), desc="Long run — steady"),
        step(STEP_INTERVAL, distance_m=mi(mp_mi),
             target=zone_target("mp"), desc="Marathon pace finish"),
    ]
    return Workout("%smi MP Finish" % _fmt(total_mi), steps)


def _hill_block(reps, up_m, desc):
    return repeat(reps, [
        step(STEP_INTERVAL, distance_m=float(up_m),
             target=zone_target("hills"), desc=desc),
        step(STEP_RECOVERY, distance_m=float(up_m), target=NO_TARGET,
             desc="Jog down recovery"),
    ])


def hill_repeats_short(reps):
    steps = [wu(), _hill_block(reps, 320, "Cat Hill — strong uphill"), cd()]
    return Workout("%dx Cat Hill" % reps, steps)


def hill_repeats_long(reps):
    steps = [wu(), _hill_block(reps, 640, "Harlem Hill — strong uphill"), cd()]
    return Workout("%dx Harlem Hill" % reps, steps)


def hill_mixed():
    steps = [
        wu(),
        _hill_block(4, 320, "Cat Hill — strong uphill"),
        _hill_block(2, 640, "Harlem Hill — strong uphill"),
        cd(),
    ]
    return Workout("Mixed Hills", steps)


def hill_tempo(total_mi):
    steps = [
        wu(1.5),
        step(STEP_INTERVAL, distance_m=mi(total_mi - 3.0),
             target=zone_target("hill_tempo"), desc="Hill tempo — by effort"),
        cd(1.5),
    ]
    return Workout("Hill Tempo", steps)


ALL_BUILDERS = [
    "easy_run", "easy_strides", "interval_workout", "mile_repeats",
    "tempo_run", "long_run_easy", "long_run_mp_finish",
    "hill_repeats_short", "hill_repeats_long", "hill_mixed", "hill_tempo",
]


def assert_serializable(payload):
    json.dumps(payload)
    return True
