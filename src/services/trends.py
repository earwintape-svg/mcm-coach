"""Trends and week review service."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any, Optional

import store
from plan import PLAN_START
from src.services.schedule import fetch_schedule
from src.services.plan_svc import plan_summary
from src.services.fitness import fetch_fitness


def fetch_trends() -> dict:
    """RHR trend, easy-pace trend by week, sleep vs performance correlation."""
    well = store.get_wellness(35)
    rhr = [{"d": w["date"], "v": w["rhr"]} for w in reversed(well) if w.get("rhr")]
    ps = plan_summary()
    titles = {s["date"]: s["title"] for s in fetch_schedule()}
    runs = store.get_runs()
    buckets: dict = {}
    for r in runs:
        t = titles.get(r["date"])
        if not t or ps["planTargets"].get(t) is not None or not r.get("paceSec"):
            continue
        wk = (date.fromisoformat(r["date"]) - PLAN_START).days // 7 + 1
        buckets.setdefault(wk, []).append(r["paceSec"])
    easy = []
    for wk in sorted(buckets):
        v = sorted(buckets[wk])
        easy.append({"w": wk, "v": v[len(v) // 2]})
    sleep_map = {w["date"]: w["sleepH"] for w in well if w.get("sleepH")}
    good_paces: list[int] = []
    poor_paces: list[int] = []
    for r in runs:
        pace = r.get("paceSec")
        if not pace or (r.get("mi") or 0) < 2:
            continue
        slh = sleep_map.get(r.get("date") or "")
        if slh is None:
            continue
        (good_paces if slh >= 7 else poor_paces).append(pace)
    sleep_perf = None
    if len(good_paces) >= 2 or len(poor_paces) >= 2:
        sleep_perf = {}
        if len(good_paces) >= 2:
            avg = sum(good_paces) // len(good_paces)
            sleep_perf["good"] = {"avgPace": avg,
                                  "paceStr": "%d:%02d" % (avg // 60, avg % 60),
                                  "n": len(good_paces)}
        if len(poor_paces) >= 2:
            avg = sum(poor_paces) // len(poor_paces)
            sleep_perf["poor"] = {"avgPace": avg,
                                  "paceStr": "%d:%02d" % (avg // 60, avg % 60),
                                  "n": len(poor_paces)}
    out: dict[str, Any] = {"rhr": rhr, "easy": easy}
    if sleep_perf:
        out["sleepPerf"] = sleep_perf
    return out


def build_week_review(week: Optional[int] = None) -> Optional[dict]:
    """One honest sentence about the training week, computed from the store."""
    today = date.today()
    if week is None:
        week = (today - PLAN_START).days // 7 + 1
    if week < 1 or week > 19:
        return None
    start = PLAN_START + timedelta(days=(week - 1) * 7)
    end = start + timedelta(days=6)
    s0, s1 = start.isoformat(), end.isoformat()
    runs = [r for r in store.get_runs() if s0 <= r["date"] <= s1]
    mi = round(sum(r.get("mi") or 0 for r in runs), 1)
    ps = plan_summary()
    planned = ps["plannedWeekly"].get(str(week)) or 0
    sched = [s for s in fetch_schedule() if s0 <= s["date"] <= s1]
    by_date: dict = {}
    for r in runs:
        if r["date"] not in by_date or r["mi"] > by_date[r["date"]]["mi"]:
            by_date[r["date"]] = r
    hit = judged = 0
    easy_paces = []
    for s in sched:
        r = by_date.get(s["date"])
        if not r:
            continue
        t = ps["planTargets"].get(s["title"])
        pm = ps["planMiles"].get(s["title"]) or 0
        if t is None and r.get("paceSec"):
            easy_paces.append(r["paceSec"])
        if t and r.get("paceSec"):
            judged += 1
            if (r["mi"] >= 0.9 * pm
                    and t["fastSec"] - 10 <= r["paceSec"] <= t["slowSec"] + 10):
                hit += 1
    if easy_paces and sum(easy_paces) / len(easy_paces) < 575:
        line = "easy days drifted fast — protect them, they fund the hard ones"
    elif planned and mi >= 0.95 * planned:
        line = "textbook week — the recovery is earned"
    elif planned and mi < 0.6 * planned:
        line = "rough week — absorb it and move on; the plan survives"
    else:
        line = "solid — keep stacking"
    vd = (fetch_fitness() or {}).get("current")
    rev = {"week": week, "mi": mi, "planned": planned, "runs": len(by_date),
           "plannedRuns": len(sched), "onTarget": hit, "judged": judged,
           "vdot": vd, "line": line}
    store.save_review(week, rev)
    return rev
