"""Plan data service — builds + caches the static 93-workout plan summary."""
from datetime import date
from plan import build_plan, PLAN_START, RACE_DATE  # noqa: F401
from builders import MILE

_PLAN_SUMMARY = None


def _fmt_pace(mps: float) -> str:
    s = int(round(MILE / mps))
    return "%d:%02d" % (s // 60, s % 60)


def _main_target(payload: dict):
    """Extract the primary pace target from a Garmin workout payload."""
    best = None

    def walk(steps, mult=1):
        nonlocal best
        for s in steps:
            if s.get("type") == "RepeatGroupDTO":
                walk(s["workoutSteps"], mult * s["numberOfIterations"])
                continue
            t = s.get("targetType") or {}
            if t.get("workoutTargetTypeKey") != "pace.zone":
                continue
            d = (s["endConditionValue"]
                 if s["endCondition"]["conditionTypeKey"] == "distance" else 0) * mult
            if best is None or d > best[0]:
                best = (d, s["targetValueOne"], s["targetValueTwo"])

    walk(payload["workoutSegments"][0]["workoutSteps"])
    if best is None:
        return None
    _, v_slow, v_fast = best
    return {
        "label": "%s–%s/mi" % (_fmt_pace(v_fast), _fmt_pace(v_slow)),
        "fastSec": int(round(MILE / v_fast)),
        "slowSec": int(round(MILE / v_slow)),
    }


def plan_summary() -> dict:
    """The plan is static per process — build the 93 payloads once."""
    global _PLAN_SUMMARY
    if _PLAN_SUMMARY is not None:
        return dict(_PLAN_SUMMARY, today=date.today().isoformat())
    plan = build_plan()
    weekly: dict[int, float] = {}
    for p in plan:
        weekly[p["week"]] = weekly.get(p["week"], 0.0) + p["distance_mi"]
    _PLAN_SUMMARY = {
        "race": RACE_DATE.isoformat(),
        "start": PLAN_START.isoformat(),
        "today": date.today().isoformat(),
        "plannedWeekly": {str(k): round(v, 1) for k, v in weekly.items()},
        "planMiles": {p["name"]: round(p["distance_mi"], 1) for p in plan},
        "planTargets": {p["name"]: _main_target(p["payload"]) for p in plan},
    }
    return dict(_PLAN_SUMMARY)
