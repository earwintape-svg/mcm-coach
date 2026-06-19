"""Read-only intervals.icu client for the actuals data plane.

Primary read-side data source for completed runs (see actuals.py for the
full precedence: intervals.icu -> Garmin Connect REST -> local store).
Workouts still push to the watch exclusively via Garmin Connect
(upload_garmin_workouts.py / src/services/garmin.py) -- this module never
writes anything, only reads completed-activity data back.

(Earlier draft of this docstring said "Suunto activities sync into
intervals.icu" -- that was a shelved alternate-device prototype, see
archive/builders_suunto.py. The runtime device is, and has always been
during this integration, a Garmin Forerunner 255.)

This turns intervals.icu's shapes into the same ones the app's fetch_*
functions already produce, so the frontend needs no changes. Mirrors the
auth pattern of the other intervals.icu scripts, but the server is
long-running (started via launchd, not an interactive shell), so it can't
rely on an `export`'d env var. Auth key resolution, in order:

  1. INTERVALS_API_KEY environment variable (if the server happens to have
     it -- e.g. you launched coach.py by hand from a terminal where it's set)
  2. a ".intervals_key" file in this directory containing just the key

Create the file once with (never paste the key in chat):
    echo "your_key_here" > .intervals_key
    chmod 600 .intervals_key

".intervals_key" is in .gitignore. If neither source is present, every
function here returns None / [] so callers fall back to their next data
source (Garmin, then the local store) -- this module is purely additive.
"""
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta

MILE = 1609.34
API_BASE = "https://intervals.icu/api/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".intervals_key")


def _api_key():
    key = os.environ.get("INTERVALS_API_KEY")
    if key:
        return key.strip()
    try:
        with open(KEY_FILE) as f:
            key = f.read().strip()
        return key or None
    except OSError:
        return None


def _athlete_id():
    return os.environ.get("INTERVALS_ATHLETE_ID", "0")


def _request(path):
    api_key = _api_key()
    if not api_key:
        return None
    url = API_BASE + path
    auth = base64.b64encode(("API_KEY:%s" % api_key).encode("ascii")).decode("ascii")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic %s" % auth)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError("intervals.icu HTTP %d: %s" % (e.code, e.read().decode("utf-8")[:200]))
    except urllib.error.URLError as e:
        raise RuntimeError("intervals.icu unreachable: %s" % e)


def fetch_wellness_days(days=14):
    """Last `days` of wellness, newest first -- same field names coach.py's
    Garmin-based fetch_wellness() used (date/rhr/sleepH/bb), plus new
    Suunto/intervals.icu-only fields (hrv, ctl, atl, form, rampRate, vo2max,
    readiness). `bb` (Body Battery) is always None -- Suunto has no
    equivalent; hrv/form/readiness are the replacement signals.

    Returns [] if no key is configured or intervals.icu has nothing yet
    (caller should fall back)."""
    oldest = (date.today() - timedelta(days=days)).isoformat()
    newest = date.today().isoformat()
    result = _request("/athlete/%s/wellness?oldest=%s&newest=%s"
                       % (_athlete_id(), oldest, newest))
    if not result:
        return []
    out = []
    for d in result:
        ctl, atl = d.get("ctl"), d.get("atl")
        sleep_s = d.get("sleepSecs")
        out.append({
            "date": d.get("id"),
            "rhr": d.get("restingHR"),
            "sleepH": round(sleep_s / 3600.0, 1) if sleep_s else None,
            "bb": None,
            "hrv": d.get("hrv"),
            "ctl": ctl,
            "atl": atl,
            "form": round(ctl - atl, 1) if ctl is not None and atl is not None else None,
            "rampRate": d.get("rampRate"),
            "vo2max": d.get("vo2max"),
            "readiness": d.get("readiness"),
        })
    out.sort(key=lambda d: d["date"] or "", reverse=True)
    return out


def fetch_activities(days=60):
    """Activities synced from Suunto, newest first. Each item has the same
    fields coach.py's Garmin-based fetch_actuals() runs use (activityId,
    date, mi, paceSec, pace, name), plus new intervals.icu-only fields:

      type        -- "Run", "Ride", "Swim", etc. (for the multi-sport view)
      avgHr/maxHr -- from the watch's HR strap/optical sensor
      compliance  -- 0-100, intervals.icu's own match of this activity
                     against its paired planned workout (None if unpaired)
      load        -- training load (TRIMP/HRSS-style) for this activity

    `mi`/`paceSec`/`pace` are None for non-running activities.
    Returns [] if no key is configured or nothing has synced yet."""
    oldest = (date.today() - timedelta(days=days)).isoformat()
    newest = date.today().isoformat()
    result = _request("/athlete/%s/activities?oldest=%s&newest=%s"
                       % (_athlete_id(), oldest, newest))
    if not result:
        return []
    out = []
    for a in result:
        is_run = a.get("type") == "Run"
        dist_m = a.get("distance") or 0.0
        dur = a.get("moving_time") or a.get("elapsed_time") or 0.0
        mi = dist_m / MILE if dist_m else 0.0
        pace_sec = int(dur / mi) if is_run and mi > 0.1 and dur else None
        compliance = a.get("compliance")
        out.append({
            "activityId": a.get("id"),
            "date": (a.get("start_date_local") or "")[:10],
            "startLocal": a.get("start_date_local"),
            "type": a.get("type"),
            "name": a.get("name") or a.get("type") or "Activity",
            "mi": round(mi, 2) if is_run else None,
            "paceSec": pace_sec,
            "pace": ("%d:%02d" % (pace_sec // 60, pace_sec % 60)) if pace_sec else None,
            "avgHr": a.get("average_heartrate"),
            "maxHr": a.get("max_heartrate"),
            "durationSec": int(dur) if dur else None,
            "compliance": round(compliance, 1) if compliance is not None else None,
            "load": a.get("icu_training_load"),
            "hrRecovery": a.get("icu_hrr"),
            "source": "intervals",
        })
    out.sort(key=lambda r: r["date"] or "", reverse=True)
    return out


def fetch_activity_detail(activity_id):
    """One activity's summary + streams + auto-detected intervals, mapped to
    the same {summary, laps, series, route} shape as coach.py's Garmin-based
    fetch_run_detail(). Laps come from intervals.icu's effort-detection engine
    (/intervals endpoint) — populated for quality workouts, empty for easy runs.
    Streams are best-effort: summary is returned even if the stream request
    fails or the activity has no GPS/pace data."""
    a = _request("/activity/%s" % activity_id)
    if not a:
        return {}
    dist_m = a.get("distance") or 0.0
    dur = a.get("moving_time") or a.get("elapsed_time") or 0.0
    mi = dist_m / MILE if dist_m else 0.0
    compliance = a.get("compliance")
    out = {"summary": {
        "name": a.get("name") or "Activity",
        "mi": round(mi, 2),
        "durSec": int(dur),
        "paceSec": int(dur / mi) if mi > 0.1 and dur else None,
        "avgHr": a.get("average_heartrate"),
        "maxHr": a.get("max_heartrate"),
        "cad": round(a["average_cadence"]) if a.get("average_cadence") else None,
        "elevFt": int(round((a.get("total_elevation_gain") or 0) * 3.28084)),
        "hrRecovery": a.get("icu_hrr"),
        "compliance": round(compliance, 1) if compliance is not None else None,
    }, "laps": [], "series": {}, "route": []}
    # Fetch intervals.icu's auto-detected effort intervals (populated for
    # quality workouts; empty or a single "all" interval for easy runs).
    try:
        ivs = _request("/activity/%s/intervals" % activity_id) or []
        laps = []
        for iv in ivs:
            # Filter to effort intervals only (skip recovery, warmup, cooldown)
            typ = (iv.get("type") or "").lower()
            if typ in ("recovery", "warmup", "cooldown", "rest"):
                continue
            iv_dist = iv.get("distance") or 0.0
            iv_dur = iv.get("moving_time") or iv.get("elapsed_time") or 0.0
            iv_mi = iv_dist / MILE if iv_dist else 0.0
            if iv_mi < 0.05 or iv_dur < 10:
                continue
            pace_sec = int(iv_dur / iv_mi) if iv_mi > 0.05 else None
            laps.append({
                "mi": round(iv_mi, 2),
                "paceSec": pace_sec,
                "avgHr": iv.get("average_heartrate"),
                "label": iv.get("label") or iv.get("name") or "",
            })
        # Only surface laps if there's more than one (single = whole activity)
        if len(laps) > 1:
            out["laps"] = laps
    except Exception:
        pass
    try:
        avail = set(a.get("stream_types") or [])
        want = [t for t in ("time", "distance", "heartrate", "altitude", "velocity_smooth", "latlng")
                if t in avail]
        if "distance" in want:
            streams = _request("/activity/%s/streams.json?types=%s"
                                % (activity_id, ",".join(want)))
            by_type = {s.get("type"): (s.get("data") or [])
                       for s in (streams or []) if isinstance(s, dict)}
            dist, hr = by_type.get("distance") or [], by_type.get("heartrate") or []
            elev, vel = by_type.get("altitude") or [], by_type.get("velocity_smooth") or []
            latlng = by_type.get("latlng") or []
            n = len(dist)
            # Grade-adjusted pace: per-point gradient → effort multiplier → GAP.
            # Minetti approximation: +3.3% effort per 1% uphill, -1.8% downhill.
            # GAP = equivalent flat pace at the same perceived effort.
            def _gap_factor(grade_frac):
                g = max(-0.4, min(0.4, grade_frac))
                gp = g * 100
                return (1.0 + 0.033 * gp) if gp >= 0 else (1.0 + 0.018 * gp)
            grades = [0.0] * n
            if len(elev) == n and n > 1:
                for i in range(1, n):
                    dd = (dist[i] or 0) - (dist[i - 1] or 0)
                    de = ((elev[i] or 0) - (elev[i - 1] or 0)
                          if elev[i] is not None and elev[i - 1] is not None else 0)
                    grades[i] = de / dd if dd > 0 else 0
                grades[0] = grades[1]
            gap_raw = []
            for i in range(n):
                v = vel[i] if i < len(vel) else None
                if v and v > 0.5:
                    gap_v = v * _gap_factor(grades[i])
                    gap_raw.append(int(MILE / gap_v) if gap_v > 0.1 else None)
                else:
                    gap_raw.append(None)
            gap_num, gap_den = 0.0, 0.0
            for i in range(1, n):
                dd = ((dist[i] or 0) - (dist[i - 1] or 0)) / MILE
                g = gap_raw[i]
                if g is not None and dd > 0:
                    gap_num += g * dd
                    gap_den += dd
            if gap_den > 0:
                out["summary"]["gapSec"] = int(gap_num / gap_den)
            # downsample to ~300 points, matching Garmin's maxChartSize
            step = max(1, n // 300)
            d_arr, p_arr, h_arr, e_arr, gap_arr, rt = [], [], [], [], [], []
            for i in range(0, n, step):
                d_arr.append(round((dist[i] or 0) / MILE, 3))
                v = vel[i] if i < len(vel) else None
                p_arr.append(int(MILE / v) if v and v > 0.5 else None)
                h_arr.append(hr[i] if i < len(hr) else None)
                e_arr.append(round(elev[i] * 3.28084, 1)
                              if i < len(elev) and elev[i] is not None else None)
                gap_arr.append(gap_raw[i] if i < len(gap_raw) else None)
                if i < len(latlng) and latlng[i]:
                    rt.append([round(latlng[i][0], 5), round(latlng[i][1], 5)])
            if d_arr:
                out["series"] = {"d": d_arr, "pace": p_arr, "hr": h_arr,
                                 "elev": e_arr, "gap": gap_arr}
                out["route"] = rt
    except Exception:
        pass
    return out
