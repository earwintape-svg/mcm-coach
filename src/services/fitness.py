"""Fitness service — VDOT math, marathon projection, and personal records."""
import math
from datetime import date

import store
from builders import MILE
from plan import PLAN_START, RACE_DATE


def _vdot_of(meters: float, sec: float) -> float:
    """Jack Daniels VDOT from race-equivalent distance + time."""
    t = sec / 60.0
    v = meters / t
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    pct = (0.8 + 0.1894393 * math.exp(-0.012778 * t)
           + 0.2989558 * math.exp(-0.1932605 * t))
    return vo2 / pct


def _predict_secs(meters: float, vdot: float) -> int:
    """Predicted race time at a given VDOT (bisection search)."""
    lo, hi = 3600.0, 25200.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _vdot_of(meters, mid) > vdot:
            lo = mid
        else:
            hi = mid
    return int((lo + hi) / 2)


def fetch_fitness() -> dict:
    """VDOT trend, marathon projection, and efficiency-factor cross-check."""
    runs = store.get_runs()
    weeks, best28 = {}, None
    ef_weeks: dict = {}
    today = date.today()
    for r in runs:
        if not r.get("paceSec") or (r.get("mi") or 0) < 3:
            continue
        vd = _vdot_of(r["mi"] * MILE, r["mi"] * r["paceSec"])
        wk = (date.fromisoformat(r["date"]) - PLAN_START).days // 7 + 1
        if 25 <= vd <= 75:
            if 1 <= wk <= 19:
                weeks[wk] = max(weeks.get(wk, 0), round(vd, 1))
            if (today - date.fromisoformat(r["date"])).days <= 28:
                best28 = max(best28 or 0, vd)
        if r.get("avgHr") and r["avgHr"] > 80 and 1 <= wk <= 19:
            ef = (MILE / r["paceSec"]) / r["avgHr"]
            ef_weeks[wk] = max(ef_weeks.get(wk, 0), ef)
    if best28 is None:
        return {}
    msec = _predict_secs(42195, best28)
    out = {
        "current": round(best28, 1),
        "weeks": [{"week": k, "vdot": v} for k, v in sorted(weeks.items())],
        "marathon": "%d:%02d:%02d" % (msec // 3600, msec % 3600 // 60, msec % 60),
        "goalGap": msec - (3 * 3600 + 25 * 60),
        "daysToRace": (RACE_DATE - today).days,
        "raceDate": RACE_DATE.isoformat(),
    }
    if len(ef_weeks) >= 2:
        wk_sorted = sorted(ef_weeks)
        recent = ef_weeks[wk_sorted[-1]]
        prior = sum(ef_weeks[w] for w in wk_sorted[:-1]) / (len(wk_sorted) - 1)
        out["efTrendPct"] = round((recent - prior) / prior * 100, 1)
    return out


def fetch_prs() -> dict:
    """Personal records: fastest mile/5K/10K/half pace + longest run."""
    runs = store.get_runs()
    prs: dict = {}
    for r in runs:
        mi = r.get("mi") or 0.0
        pace = r.get("paceSec")
        aid = r.get("activityId")
        d = r.get("date") or ""
        if not pace or pace < 300 or pace > 1200 or mi < 0.5:
            continue
        rec = {"pace": pace, "date": d, "mi": mi, "activityId": aid,
               "paceStr": "%d:%02d" % (pace // 60, pace % 60)}
        if mi >= 1.0:
            if "mile" not in prs or pace < prs["mile"]["pace"]:
                prs["mile"] = rec
        if mi >= 3.11:
            if "5k" not in prs or pace < prs["5k"]["pace"]:
                prs["5k"] = rec
        if mi >= 6.21:
            if "10k" not in prs or pace < prs["10k"]["pace"]:
                prs["10k"] = rec
        if mi >= 13.1:
            if "half" not in prs or pace < prs["half"]["pace"]:
                prs["half"] = rec
        if "long" not in prs or mi > prs["long"]["mi"]:
            prs["long"] = {"mi": mi, "date": d, "activityId": aid}
    return prs
