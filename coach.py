#!/usr/bin/env python3
"""MCM Coach — training calendar + readiness for the MCM 2026 plan.

Run:   python3 coach.py            (Mac only: http://127.0.0.1:8765)
       python3 coach.py --lan      (also from your phone on the same Wi-Fi —
                                    the phone URL is printed; Add to Home
                                    Screen in Safari for an app-like feel)
       python3 coach.py notify     (macOS notification with today's workout —
                                    wire to cron/Shortcuts for a daily nudge)

What it does:
  * Mon-Sun calendar of the plan, read live from Garmin Connect
  * Reschedule: drag on desktop, tap-to-move on phone; instant sync + Undo
  * Vacation mode: shift a date range with preview
  * Morning briefing: today's workout + readiness (resting HR vs your
    baseline, last night's sleep, Body Battery) with honest suggestions
  * Week report card: each run vs plan (distance + pace verdicts)
  * Ramp guard: warns when a week jumps >25% over your recent average
  * Weekly mileage chart, planned vs run

Health signals are limited to ones that change decisions (RHR trend, sleep,
Body Battery). No steps, no calories, no daily VO2max noise — vanity metrics
are someone else's business model.

Python 3.9 stdlib only. `upload --force` resets dates to plan.py.
"""
import argparse
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
from plan import build_plan, PLAN_START, RACE_DATE
from builders import MILE
from upload_garmin_workouts import get_client, api, is_plan_name

PORT = 8765
# Weather location (NYC default) — override with env vars if you move.
WX_LAT = float(os.environ.get("COACH_LAT", "40.78"))
WX_LON = float(os.environ.get("COACH_LON", "-73.97"))
_client = None
_client_lock = threading.Lock()
_cache = {"sched": None, "ts": 0.0, "well": None, "well_ts": 0.0,
          "wx": None, "wx_ts": 0.0}


def client():
    global _client
    with _client_lock:
        if _client is None:
            _client = get_client()
        return _client


# ------------------------------------------------------------ Garmin reads

def _refresh_schedule():
    """The slow path: ~9 Garmin calendar calls. Result is persisted, so the
    app serves instantly from the store and refreshes in the background."""
    seen = {}
    c = client()
    for m in range(3, 12):
        try:
            data = api(c, "/calendar-service/year/%d/month/%d" % (PLAN_START.year, m)) or {}
        except Exception:
            continue
        for it in data.get("calendarItems") or []:
            if (it.get("itemType") == "workout" and it.get("date")
                    and is_plan_name(it.get("title") or "")):
                seen[it.get("id")] = {
                    "scheduleId": it.get("id"),
                    "workoutId": it.get("workoutId"),
                    "title": it.get("title"),
                    "date": it.get("date"),
                }
    out = sorted(seen.values(), key=lambda x: (x["date"], x["title"]))
    store.set_kv("schedule", out)
    _cache.update(sched=out, ts=time.time())
    return out


def fetch_schedule(force=False):
    if force:
        return _refresh_schedule()
    if _cache["sched"] is not None and time.time() - _cache["ts"] < 60:
        return _cache["sched"]
    cached, age = store.get_kv("schedule")
    if cached is not None:
        _cache.update(sched=cached, ts=time.time())
        if age > 600:  # stale-while-revalidate: serve now, refresh behind
            threading.Thread(target=_refresh_schedule, daemon=True).start()
        return cached
    return _refresh_schedule()


def _weekly_of(runs):
    weekly = {}
    for r in runs:
        wk = (date.fromisoformat(r["date"]) - PLAN_START).days // 7 + 1
        if 1 <= wk <= 19:
            weekly[wk] = weekly.get(wk, 0.0) + (r.get("mi") or 0.0)
    return {str(k): round(v, 1) for k, v in weekly.items()}


def fetch_actuals():
    """Runs from Garmin, mirrored into the local store. If Garmin is down,
    serve from the store — the app keeps working offline."""
    runs, stale = [], False
    try:
        c = client()
        path = ("/activitylist-service/activities/search/activities"
                "?start=0&limit=400&startDate=%s&endDate=%s"
                % ((PLAN_START - timedelta(days=30)).isoformat(),   # include tune-up runs
                   (RACE_DATE + timedelta(days=1)).isoformat()))
        for a in api(c, path) or []:
            if "running" not in ((a.get("activityType") or {}).get("typeKey") or ""):
                continue
            day = (a.get("startTimeLocal") or "")[:10]
            if not day:
                continue
            mi = (a.get("distance") or 0.0) / MILE
            dur = a.get("movingDuration") or a.get("duration") or 0.0
            pace_sec = int(dur / mi) if mi > 0.1 and dur else None
            if a.get("activityId"):
                store.save_raw_activity(a["activityId"], a)
            runs.append({
                "activityId": a.get("activityId"),
                "date": day,
                "mi": round(mi, 2),
                "paceSec": pace_sec,
                "pace": ("%d:%02d" % (pace_sec // 60, pace_sec % 60)) if pace_sec else None,
                "name": a.get("activityName") or "Run",
            })
        store.upsert_runs(runs)
    except Exception:
        runs, stale = store.get_runs(), True
    return {"weekly": _weekly_of(runs), "runs": runs,
            "ann": store.get_annotations(), "stale": stale}


def fetch_wellness(force=False):
    """Last 7 days of decision-grade health signals: resting HR, sleep,
    Body Battery. Degrades gracefully if the endpoints change."""
    if not force and _cache["well"] is not None and time.time() - _cache["well_ts"] < 1800:
        return _cache["well"]
    out = {"days": []}
    try:
        c = client()
        dn = getattr(c, "display_name", None)
        if not dn:
            prof = api(c, "/userprofile-service/socialProfile") or {}
            dn = prof.get("displayName")
        if not dn:
            raise RuntimeError("no display name")
        today = date.today()
        for i in range(7):
            d = (today - timedelta(days=i)).isoformat()
            try:
                s = api(c, "/usersummary-service/usersummary/daily/%s?calendarDate=%s"
                        % (dn, d)) or {}
            except Exception:
                s = {}
            sleep_s = s.get("sleepingSeconds") or 0
            out["days"].append({
                "date": d,
                "rhr": s.get("restingHeartRate"),
                "sleepH": round(sleep_s / 3600.0, 1) if sleep_s else None,
                "bb": s.get("bodyBatteryMostRecentValue")
                      or s.get("bodyBatteryHighestValue"),
            })
    except Exception as e:
        cached = store.get_wellness()
        out = {"days": cached, "stale": True} if cached else {"error": str(e)}
    if out.get("days") and not out.get("stale"):
        store.upsert_wellness(out["days"])
    _cache.update(well=out, well_ts=time.time())
    return out


def fetch_run_detail(activity_id):
    """One run's full story: summary stats, laps, downsampled pace/HR/elev
    series, and the GPS trace (rendered as an abstract route — no map tiles,
    no third-party requests, no home-location leak).

    Cached forever in the local store after first fetch — past runs don't
    change, so run sheets open instantly and work offline."""
    cached = store.get_run_detail(activity_id)
    if cached and cached.get("v") == 2:   # v2 added elevation series
        return cached
    c = client()
    summ = api(c, "/activity-service/activity/%s" % activity_id) or {}
    s = summ.get("summaryDTO") or {}
    dist = s.get("distance") or 0.0
    dur = s.get("movingDuration") or s.get("duration") or 0.0
    mi = dist / MILE
    out = {"summary": {
        "name": summ.get("activityName") or "Run",
        "mi": round(mi, 2),
        "durSec": int(dur),
        "paceSec": int(dur / mi) if mi > 0.1 else None,
        "avgHr": s.get("averageHR"),
        "maxHr": s.get("maxHR"),
        "cad": s.get("averageRunCadence"),
        "elevFt": int(round((s.get("elevationGain") or 0) * 3.28084)),
    }, "laps": [], "series": {}, "route": []}
    try:
        spl = api(c, "/activity-service/activity/%s/splits" % activity_id) or {}
        for l in spl.get("lapDTOs") or []:
            ld = l.get("distance") or 0
            lt = l.get("movingDuration") or l.get("duration") or 0
            if ld > 30 and lt:
                out["laps"].append({"mi": round(ld / MILE, 2),
                                    "paceSec": int(lt / (ld / MILE))})
    except Exception:
        pass
    try:
        det = api(c, "/activity-service/activity/%s/details"
                     "?maxChartSize=300&maxPolylineSize=300" % activity_id) or {}
        idx = {}
        for i, m in enumerate(det.get("metricDescriptors") or []):
            idx[m.get("key")] = m.get("metricsIndex", i)
        pts = det.get("activityDetailMetrics") or []

        def col(key):
            i = idx.get(key)
            if i is None:
                return None
            return [(p.get("metrics") or [])[i] if i < len(p.get("metrics") or []) else None
                    for p in pts]
        dist_m, spd = col("sumDistance"), col("directSpeed")
        hr, elev = col("directHeartRate"), col("directElevation")
        lat, lon = col("directLatitude"), col("directLongitude")
        d_arr, p_arr, h_arr, e_arr, rt = [], [], [], [], []
        for i in range(len(pts)):
            if not dist_m or dist_m[i] is None:
                continue
            d_arr.append(round(dist_m[i] / MILE, 3))
            v = spd[i] if spd else None
            p_arr.append(int(MILE / v) if v and v > 0.5 else None)
            h_arr.append(hr[i] if hr else None)
            e_arr.append(round(elev[i] * 3.28084, 1)
                         if elev and elev[i] is not None else None)
            if lat and lon and lat[i] is not None and lon[i] is not None:
                rt.append([round(lat[i], 5), round(lon[i], 5)])
        out["series"] = {"d": d_arr, "pace": p_arr, "hr": h_arr, "elev": e_arr}
        out["route"] = rt
    except Exception:
        pass
    if out["laps"] or out.get("series"):
        out["v"] = 2
        store.save_run_detail(activity_id, out)
    return out


def fetch_weather():
    """Current conditions from Open-Meteo (free, no key). 30-min cache.
    Heat + humidity are the two numbers that change how a run should feel."""
    if _cache["wx"] is not None and time.time() - _cache["wx_ts"] < 1800:
        return _cache["wx"]
    out = {}
    try:
        import urllib.request
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
               "&current=temperature_2m,apparent_temperature,relative_humidity_2m"
               "&temperature_unit=fahrenheit" % (WX_LAT, WX_LON))
        with urllib.request.urlopen(url, timeout=6) as r:
            cur = (json.load(r).get("current") or {})
        out = {"tempF": round(cur.get("temperature_2m") or 0),
               "feelsF": round(cur.get("apparent_temperature") or 0),
               "humidity": cur.get("relative_humidity_2m")}
    except Exception as e:
        out = {"error": str(e)}
    if out.get("tempF") is not None:
        out["heatPct"] = heat_pct(out)
        store.save_weather(date.today().isoformat(), out)
    _cache.update(wx=out, wx_ts=time.time())
    return out


# ----------------------------------------------------------- Garmin writes

def move_workout(schedule_id, workout_id, new_date):
    date.fromisoformat(new_date)
    c = client()
    try:
        api(c, "/workout-service/schedule/%s" % schedule_id, method="DELETE")
    except Exception:
        pass
    api(c, "/workout-service/schedule/%s" % workout_id, method="POST",
        payload={"date": new_date})
    store.log_event("move", workout_id, new_date)
    _cache["sched"] = None


def unschedule_workout(schedule_id):
    """Remove a workout from the calendar (the workout itself stays in the
    library). Used by vacation mode's 'skip' recommendations."""
    api(client(), "/workout-service/schedule/%d" % schedule_id, method="DELETE")
    store.log_event("skip", schedule_id)
    _cache["sched"] = None


def shift_range(date_from, date_to, days):
    d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    moved = 0
    for it in list(fetch_schedule(force=True)):
        d = date.fromisoformat(it["date"])
        if d1 <= d <= d2:
            move_workout(it["scheduleId"], it["workoutId"],
                         (d + timedelta(days=days)).isoformat())
            moved += 1
    _cache["sched"] = None
    return moved


# --------------------------------------------------------------- plan data

def _fmt_pace(mps):
    s = int(round(MILE / mps))
    return "%d:%02d" % (s // 60, s % 60)


def _main_target(payload):
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
            d = (s["endConditionValue"] if s["endCondition"]["conditionTypeKey"] == "distance"
                 else 0) * mult
            if best is None or d > best[0]:
                best = (d, s["targetValueOne"], s["targetValueTwo"])
    walk(payload["workoutSegments"][0]["workoutSteps"])
    if best is None:
        return None
    _, v_slow, v_fast = best
    return {"label": "%s–%s/mi" % (_fmt_pace(v_fast), _fmt_pace(v_slow)),
            "fastSec": int(round(MILE / v_fast)), "slowSec": int(round(MILE / v_slow))}


_PLAN_SUMMARY = None


def plan_summary():
    """The plan is static per process — build the 93 payloads once."""
    global _PLAN_SUMMARY
    if _PLAN_SUMMARY is not None:
        return dict(_PLAN_SUMMARY, today=date.today().isoformat())
    plan = build_plan()
    weekly = {}
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


# ----------------------------------------------------------------- app icon

_ICON = None


def _icon_png():
    """180x180 apple-touch-icon: the chevrons on Asphalt — rendered with
    pure stdlib (zlib PNG). No Pillow, no asset files, can't go missing."""
    global _ICON
    if _ICON is not None:
        return _ICON
    import struct
    import zlib
    S, R = 180, 10.0
    BG, MINT, CORAL = (16, 20, 24), (93, 202, 165), (240, 153, 123)
    mint_segs = [(56, 56, 93, 90), (93, 90, 56, 124)]
    coral_segs = [(101, 68, 129, 90), (129, 90, 101, 113)]

    def dist(px, py, segs):
        best = 1e9
        for ax, ay, bx, by in segs:
            vx, vy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
            dx, dy = px - (ax + t * vx), py - (ay + t * vy)
            best = min(best, (dx * dx + dy * dy) ** 0.5)
        return best

    def mix(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    rows = []
    for y in range(S):
        row = bytearray()
        for x in range(S):
            c = BG
            d1, d2 = dist(x, y, mint_segs), dist(x, y, coral_segs)
            d, col = (d2, CORAL) if d2 <= d1 else (d1, MINT)
            if d <= R + 1:
                c = mix(c, col, max(0.0, min(1.0, R + 1 - d)))
            row += bytes(c)
        rows.append(bytes(row))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + r for r in rows)
    _ICON = (b"\x89PNG\r\n\x1a\n"
             + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 2, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return _ICON


# --------------------------------------------------- dynamic coaching engine

_HARD_RE = re.compile(r"Tempo|Hill|\dx|MP Finish|mi LR")


def _is_hard(title):
    return bool(_HARD_RE.search(title or ""))


def _next_clean_slot(sched, exclude_id, from_day, prefer_weekend=False):
    """First day after from_day with nothing scheduled and no hard day
    adjacent — the same invariant the vacation planner keeps."""
    occupied = {s["date"] for s in sched if s["scheduleId"] != exclude_id}
    hard = {s["date"] for s in sched
            if s["scheduleId"] != exclude_id and _is_hard(s["title"])}

    def near_hard(d):
        return any((d + timedelta(days=k)).isoformat() in hard for k in (-1, 0, 1))
    for k in range(1, 11):
        d = from_day + timedelta(days=k)
        if d.isoformat() in occupied or near_hard(d):
            continue
        if prefer_weekend and k <= 7 and d.weekday() not in (5, 6):
            continue
        return d.isoformat()
    return None


def adapt_training_block():
    """Rule-based plan adaptation over the next 7 days. Returns proposals —
    nothing mutates until /api/coach/apply is called with one.
    Rule 1 (fatigue): RPE > 8 logged on an easy run in the last 2 days →
    propose pushing the next quality session to a clean day.
    Rule 2 (adjacency): two hard days back-to-back → propose moving the
    second to the next clean slot."""
    today = date.today()
    sched = fetch_schedule()
    by = {s["date"]: s for s in sched}
    props = []

    anns = store.get_annotations()
    runsmap = {str(r.get("activityId")): r for r in store.get_runs()}
    for aid, a in anns.items():
        r = runsmap.get(aid)
        if not r or not a.get("rpe") or a["rpe"] <= 8:
            continue
        if (today - date.fromisoformat(r["date"])).days > 2:
            continue
        planned = by.get(r["date"])
        if planned and _is_hard(planned["title"]):
            continue   # high RPE on a hard day is the assignment, not a flag
        for k in range(0, 3):
            ds = (today + timedelta(days=k)).isoformat()
            s = by.get(ds)
            if s and _is_hard(s["title"]):
                to = _next_clean_slot(sched, s["scheduleId"],
                                      date.fromisoformat(ds),
                                      prefer_weekend=("LR" in s["title"]
                                                      or "MP" in s["title"]))
                props.append({"reason": "RPE %d on an easy run — fatigue flag. "
                                        "Give the next quality session room."
                                        % a["rpe"],
                              "title": s["title"], "scheduleId": s["scheduleId"],
                              "workoutId": s["workoutId"], "date": s["date"],
                              "action": "move" if to else "skip", "to": to})
                break
        break

    for k in range(0, 7):
        d1 = (today + timedelta(days=k)).isoformat()
        d2 = (today + timedelta(days=k + 1)).isoformat()
        s1, s2 = by.get(d1), by.get(d2)
        if s1 and s2 and _is_hard(s1["title"]) and _is_hard(s2["title"]):
            if any(p["scheduleId"] == s2["scheduleId"] for p in props):
                break
            to = _next_clean_slot(sched, s2["scheduleId"], date.fromisoformat(d2))
            props.append({"reason": "Back-to-back hard days — hard work needs "
                                    "easy days between it to become fitness.",
                          "title": s2["title"], "scheduleId": s2["scheduleId"],
                          "workoutId": s2["workoutId"], "date": s2["date"],
                          "action": "move" if to else "skip", "to": to})
            break
    return props[:2]


def heat_pct(wx):
    """Pace slowdown for heat: +0.4% per °F of apparent temp above 60°F,
    +1% extra when humidity ≥65%, capped at 10%. Display-only — the watch
    plan never changes."""
    if not wx or wx.get("tempF") is None:
        return 0.0
    feels = wx.get("feelsF") or wx["tempF"]
    excess = max(0, feels - 60)
    pct = excess * 0.004
    if excess > 0 and (wx.get("humidity") or 0) >= 65:
        pct += 0.01   # humidity only costs pace when it's actually warm
    return round(min(0.10, pct), 3)


# ------------------------------------------------------------- week review

def build_week_review(week=None):
    """One honest sentence about the week, computed from the store."""
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
    by_date = {}
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
    if easy_paces and sum(easy_paces) / len(easy_paces) < 575:   # < ~9:35/mi
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


def fetch_trends():
    well = store.get_wellness(35)
    rhr = [{"d": w["date"], "v": w["rhr"]} for w in reversed(well) if w.get("rhr")]
    ps = plan_summary()
    titles = {s["date"]: s["title"] for s in fetch_schedule()}
    buckets = {}
    for r in store.get_runs():
        t = titles.get(r["date"])
        if not t or ps["planTargets"].get(t) is not None or not r.get("paceSec"):
            continue
        wk = (date.fromisoformat(r["date"]) - PLAN_START).days // 7 + 1
        buckets.setdefault(wk, []).append(r["paceSec"])
    easy = []
    for wk in sorted(buckets):
        v = sorted(buckets[wk])
        easy.append({"w": wk, "v": v[len(v) // 2]})
    return {"rhr": rhr, "easy": easy}


# ------------------------------------------------------------ fitness math

def _vdot_of(meters, sec):
    """Jack Daniels: race-equivalent VDOT from a distance + time."""
    import math
    t = sec / 60.0
    v = meters / t
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    pct = (0.8 + 0.1894393 * math.exp(-0.012778 * t)
           + 0.2989558 * math.exp(-0.1932605 * t))
    return vo2 / pct


def _predict_secs(meters, vdot):
    """Time for a race distance at a given VDOT (bisection — vdot falls as
    time rises)."""
    lo, hi = 60 * 60.0, 60 * 420.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _vdot_of(meters, mid) > vdot:
            lo = mid
        else:
            hi = mid
    return int((lo + hi) / 2)


def fetch_fitness():
    """Fitness trend from the store. Honest caveat baked in: these are
    training runs, not races, so this is a floor — race-day VDOT reads
    higher. Trend matters more than the absolute number."""
    runs = store.get_runs()
    weeks, best28 = {}, None
    today = date.today()
    for r in runs:
        if not r.get("paceSec") or (r.get("mi") or 0) < 3:
            continue
        vd = _vdot_of(r["mi"] * MILE, r["mi"] * r["paceSec"])
        if vd < 25 or vd > 75:
            continue
        wk = (date.fromisoformat(r["date"]) - PLAN_START).days // 7 + 1
        if 1 <= wk <= 19:
            weeks[wk] = max(weeks.get(wk, 0), round(vd, 1))
        if (today - date.fromisoformat(r["date"])).days <= 28:
            best28 = max(best28 or 0, vd)
    if best28 is None:
        return {}
    msec = _predict_secs(42195, best28)
    return {"current": round(best28, 1),
            "weeks": [{"week": k, "vdot": v} for k, v in sorted(weeks.items())],
            "marathon": "%d:%02d:%02d" % (msec // 3600, msec % 3600 // 60, msec % 60),
            "goalGap": msec - (3 * 3600 + 25 * 60)}


# ------------------------------------------------------------------- HTTP

ACCESS_KEY = None  # set in --lan mode; localhost is always allowed


class Handler(BaseHTTPRequestHandler):

    MAX_BODY = 64 * 1024  # plenty for any legitimate request

    def _authorized(self):
        """Localhost is trusted; LAN clients need the key. Comparison is
        constant-time (hmac.compare_digest) — never compare secrets with ==."""
        if ACCESS_KEY is None or self.client_address[0] in ("127.0.0.1", "::1"):
            return True
        given = self.headers.get("X-Key") or ""
        if hmac.compare_digest(given, ACCESS_KEY):
            return True
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        return any(p.startswith("key=") and hmac.compare_digest(p[4:], ACCESS_KEY)
                   for p in query.split("&"))

    def _headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            route = self.path.split("?")[0]
            if route == "/apple-touch-icon.png":   # public asset (iOS fetches keyless)
                body = _icon_png()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                return self.wfile.write(body)
            if not self._authorized():
                return self._json({"error": "unauthorized — use the link with ?key=… printed in Terminal"}, 403)
            if route == "/app.js":
                body = _asset("app.js")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._headers()
                self.end_headers()
                return self.wfile.write(body)
            if route == "/" or route.startswith("/index"):
                body = _asset("ui.html")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._headers()
                self.end_headers()
                self.wfile.write(body)
            elif route.startswith("/api/data"):
                force = "refresh=1" in self.path
                self._json({"plan": plan_summary(), "schedule": fetch_schedule(force)})
            elif route.startswith("/api/actuals"):
                self._json(fetch_actuals())
            elif route.startswith("/api/wellness"):
                self._json(fetch_wellness("refresh=1" in self.path))
            elif route.startswith("/api/weather"):
                self._json(fetch_weather())
            elif route.startswith("/api/fitness"):
                self._json(fetch_fitness())
            elif route.startswith("/api/gear"):
                self._json({"gear": store.gear_summary()})
            elif route.startswith("/api/coach"):
                self._json({"proposals": adapt_training_block()})
            elif route.startswith("/api/trends"):
                self._json(fetch_trends())
            elif route.startswith("/api/review"):
                today = date.today()
                wk = (today - timedelta(days=1) - PLAN_START).days // 7 + 1
                rev = (store.get_review(wk) or build_week_review(wk)) \
                    if today.weekday() in (6, 0) else None
                self._json({"review": rev})
            elif route.startswith("/api/run/"):
                aid = route.split("/api/run/")[1]
                if not aid.isdigit():           # activity ids are numeric
                    return self._json({"error": "bad activity id"}, 400)
                self._json(fetch_run_detail(int(aid)))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            if not self._authorized():
                return self._json({"error": "unauthorized"}, 403)
            n = int(self.headers.get("Content-Length") or 0)
            if n > self.MAX_BODY:
                return self._json({"error": "request too large"}, 413)
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/move":
                # ints + ISO date only — never format raw client input into API paths
                move_workout(int(req["scheduleId"]), int(req["workoutId"]),
                             str(req["date"]))
                self._json({"ok": True})
            elif self.path == "/api/shift_range":
                days = int(req["days"])
                if abs(days) > 90:
                    return self._json({"error": "shift limited to ±90 days"}, 400)
                moved = shift_range(str(req["from"]), str(req["to"]), days)
                self._json({"ok": True, "moved": moved})
            elif self.path == "/api/unschedule":
                unschedule_workout(int(req["scheduleId"]))
                self._json({"ok": True})
            elif self.path == "/api/import":
                # Generic intake: {"source":"apple_health","date":"2026-06-15","metrics":{...}}
                date.fromisoformat(str(req["date"]))
                store.save_external(str(req["source"]), str(req["date"]),
                                    req.get("metrics") or {})
                self._json({"ok": True})
            elif self.path == "/api/gear":
                store.set_gear(str(req["key"]), display=req.get("display"),
                               start_mi=req.get("startMi"),
                               threshold_mi=req.get("thresholdMi"),
                               retired=req.get("retired"),
                               brand=req.get("brand"), model=req.get("model"),
                               is_default=req.get("isDefault"))
                self._json({"ok": True})
            elif re.fullmatch(r"/api/run/\w+/gear", self.path):
                aid = self.path.split("/")[3][:32]
                store.set_annotation(aid, shoes=str(req.get("gearId") or ""))
                self._json({"ok": True})
            elif self.path == "/api/coach/apply":
                if req.get("action") == "move":
                    move_workout(int(req["scheduleId"]), int(req["workoutId"]),
                                 str(req["to"]))
                else:
                    unschedule_workout(int(req["scheduleId"]))
                self._json({"ok": True})
            elif self.path == "/api/annotate":
                store.set_annotation(str(req["activityId"])[:32],
                                     rpe=req.get("rpe"), note=req.get("note"),
                                     shoes=req.get("shoes"))
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (KeyError, ValueError) as e:
            self._json({"error": "bad request: %s" % e}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, fmt, *args):
        pass


# Frontend lives in ui.html + app.js next to this file (extracted from the
# old embedded PAGE string). Assets are cached and hot-reload on mtime change.
ASSET_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS = {}


def _asset(fname):
    path = os.path.join(ASSET_DIR, fname)
    mt = os.path.getmtime(path)
    hit = _ASSETS.get(fname)
    if hit is None or hit[0] != mt:
        with open(path, "rb") as f:
            _ASSETS[fname] = (mt, f.read())
    return _ASSETS[fname][1]


# ------------------------------------------------------------------ notify

def cmd_notify(weekly=False):
    """One-shot notification: daily briefing/nudge, or --weekly for the
    Sunday week-in-review. Scheduled by ./lan.sh notify-on."""
    if weekly:
        rev = build_week_review()
        if rev:
            msg = ("Week %d: %.1f of %.0f mi · %d/%d runs · on target %d×"
                   % (rev["week"], rev["mi"], rev["planned"] or 0,
                      rev["runs"], rev["plannedRuns"], rev["onTarget"]))
            if rev.get("vdot"):
                msg += " · VDOT %.1f" % rev["vdot"]
            msg += " — " + rev["line"]
        else:
            msg = "Tune-up phase — weekly reviews start with week 1."
        _push(msg)
        return
    today = date.today().isoformat()
    items = [i for i in fetch_schedule() if i["date"] == today]
    if items:
        msg = "Today: " + " + ".join(i["title"] for i in items)
        try:
            wx = fetch_weather()
            p = wx.get("heatPct") or 0
            t = plan_summary()["planTargets"].get(items[0]["title"])
            if t and p >= 0.02:
                msg += " · heat-adj %s–%s/mi" % (
                    _fmt_pace(MILE / (t["fastSec"] * (1 + p))),
                    _fmt_pace(MILE / (t["slowSec"] * (1 + p))))
        except Exception:
            pass
    else:
        msg = "Rest day — no workout scheduled. Recovery is training too."
    try:
        acts = fetch_actuals()
        todays = [r for r in acts["runs"] if r["date"] == today and r.get("activityId")]
        if todays and str(todays[0]["activityId"]) not in (acts.get("ann") or {}):
            msg = "Run synced: %.1f mi. Log how it felt (RPE) in timely — that data is yours alone." % todays[0]["mi"]
    except Exception:
        pass
    try:
        w = fetch_wellness()
        d = (w.get("days") or [{}])[0]
        bits = []
        if d.get("rhr"):
            bits.append("RHR %d" % d["rhr"])
        if d.get("sleepH"):
            bits.append("slept %sh" % d["sleepH"])
        if bits:
            msg += " (" + ", ".join(bits) + ")"
    except Exception:
        pass
    _push(msg)


def _push(msg):
    """Mac notification + phone push via ntfy.sh (topic in ~/.timely_ntfy)."""
    print(msg)
    try:
        subprocess.run(["osascript", "-e",
                        'display notification "%s" with title "timely"'
                        % msg.replace('"', "'")], check=False)
    except Exception:
        pass
    try:
        with open(os.path.expanduser("~/.timely_ntfy")) as f:
            topic = f.read().strip()
        if topic:
            import urllib.request
            req = urllib.request.Request(
                "https://ntfy.sh/" + topic, data=msg.encode("utf-8"),
                headers={"Title": "timely", "Tags": "stopwatch"})
            urllib.request.urlopen(req, timeout=10)
            print("(pushed to phone via ntfy)")
    except FileNotFoundError:
        pass
    except Exception as e:
        print("(phone push failed: %s)" % e)


def _run_watcher():
    """Every 10 min: sync activities; push when a NEW recent run lands so
    you know the whole pipeline worked before you've untied your shoes."""
    while True:
        try:
            before = {str(r.get("activityId")) for r in store.get_runs()}
            recent = (date.today() - timedelta(days=2)).isoformat()
            for r in fetch_actuals()["runs"]:
                aid = str(r.get("activityId"))
                if aid and aid not in before and r["date"] >= recent:
                    _push("Run synced: %.1f mi @ %s/mi — everything worked. "
                          "Log how it felt in timely." % (r["mi"], r["pace"] or "—"))
        except Exception:
            pass
        time.sleep(600)


# -------------------------------------------------------------------- main

def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _load_key():
    """Persistent LAN key so the Home Screen bookmark survives restarts."""
    key_file = os.path.expanduser("~/.mcm_coach_key")
    try:
        with open(key_file) as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(6)
    with open(key_file, "w") as f:
        f.write(key)
    os.chmod(key_file, 0o600)
    return key


def main():
    global ACCESS_KEY
    ap = argparse.ArgumentParser(description="MCM Coach — training dashboard")
    ap.add_argument("command", nargs="?", choices=["serve", "notify"], default="serve",
                    help="serve (default) or notify (one-shot macOS notification)")
    ap.add_argument("--lan", action="store_true",
                    help="also listen on your Wi-Fi network (key-protected)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true",
                    help="don't auto-open the dashboard")
    ap.add_argument("--weekly", action="store_true",
                    help="with notify: send the week-in-review")
    args = ap.parse_args()

    if args.command == "notify":
        cmd_notify(weekly=args.weekly)
        return
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), Handler)
    url = "http://127.0.0.1:%d" % args.port
    print("Coach dashboard: %s  (Ctrl+C to stop)" % url)
    if args.lan:
        ACCESS_KEY = _load_key()
        ip = lan_ip()
        if ip:
            print("On your phone (same Wi-Fi): http://%s:%d/?key=%s"
                  % (ip, args.port, ACCESS_KEY))
            print("(the key keeps others on the network out — use the full link)")
            print("Tip: in Safari, Share → Add to Home Screen for an app-like icon.")
    backup_dir = os.environ.get("TIMELY_BACKUP_DIR") or os.getcwd()

    def _backup_loop():
        while True:
            try:
                store.backup(backup_dir)
            except Exception:
                pass
            time.sleep(86400)
    threading.Thread(target=_backup_loop, daemon=True).start()
    threading.Thread(target=_run_watcher, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
