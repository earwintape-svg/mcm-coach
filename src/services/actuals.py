"""Actuals service — completed runs and detailed run data."""
from datetime import date, timedelta

import store
import intervalsicu_read
from builders import MILE
from plan import PLAN_START, RACE_DATE
from src.services.garmin import client
from upload_garmin_workouts import api


def _weekly_of(runs: list) -> dict:
    weekly = {}
    for r in runs:
        wk = (date.fromisoformat(r["date"]) - PLAN_START).days // 7 + 1
        if 1 <= wk <= 19:
            weekly[wk] = weekly.get(wk, 0.0) + (r.get("mi") or 0.0)
    return {str(k): round(v, 1) for k, v in weekly.items()}


def fetch_actuals() -> dict:
    """Completed runs. intervals.icu first; falls back to Garmin, then store."""
    runs, stale = [], False
    try:
        acts = intervalsicu_read.fetch_activities(60)
        runs = [a for a in acts if a.get("type") == "Run" and a.get("mi")]
        if not runs:
            raise RuntimeError("no intervals.icu runs")
        store.upsert_runs(runs)
    except Exception:
        try:
            c = client()
            path = ("/activitylist-service/activities/search/activities"
                    "?start=0&limit=400&startDate=%s&endDate=%s"
                    % ((PLAN_START - timedelta(days=30)).isoformat(),
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
                    "startLocal": a.get("startTimeLocal"),
                    "durationSec": int(dur) if dur else None,
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


def fetch_run_detail(activity_id) -> dict:
    """Full run data: summary + laps + series + GPS route. Cached forever."""
    cached = store.get_run_detail(activity_id)
    if cached and cached.get("v") in (2, 3):
        return cached
    if not str(activity_id).lstrip("-").isdigit():
        out = intervalsicu_read.fetch_activity_detail(activity_id)
        if out:
            out["v"] = 3
            store.save_run_detail(activity_id, out)
        return out
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
        for lap in spl.get("lapDTOs") or []:
            ld = lap.get("distance") or 0
            lt = lap.get("movingDuration") or lap.get("duration") or 0
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


def fetch_other_activities(force: bool = False) -> list:
    """Non-running activities for the multi-sport view."""
    from src.services import cache as _cache
    import time
    if not force and _cache.get("other") is not None and _cache.fresh("other_ts", 1800):
        return _cache.get("other")
    out = []
    try:
        acts = intervalsicu_read.fetch_activities(60)
        out = [a for a in acts if a.get("type") != "Run"]
    except Exception:
        pass
    _cache.set("other", out, "other_ts")
    return out
