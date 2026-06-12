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
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

def fetch_schedule(force=False):
    if not force and _cache["sched"] is not None and time.time() - _cache["ts"] < 60:
        return _cache["sched"]
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
    _cache.update(sched=out, ts=time.time())
    return out


def fetch_actuals():
    c = client()
    path = ("/activitylist-service/activities/search/activities"
            "?start=0&limit=400&startDate=%s&endDate=%s"
            % (PLAN_START.isoformat(), (RACE_DATE + timedelta(days=1)).isoformat()))
    acts = api(c, path) or []
    weekly, runs = {}, []
    for a in acts:
        if "running" not in ((a.get("activityType") or {}).get("typeKey") or ""):
            continue
        day = (a.get("startTimeLocal") or "")[:10]
        if not day:
            continue
        dist = a.get("distance") or 0.0
        mi = dist / MILE
        dur = a.get("movingDuration") or a.get("duration") or 0.0
        pace_sec = int(dur / mi) if mi > 0.1 and dur else None
        runs.append({
            "activityId": a.get("activityId"),
            "date": day,
            "mi": round(mi, 2),
            "paceSec": pace_sec,
            "pace": ("%d:%02d" % (pace_sec // 60, pace_sec % 60)) if pace_sec else None,
            "name": a.get("activityName") or "Run",
        })
        wk = (date.fromisoformat(day) - PLAN_START).days // 7 + 1
        if 1 <= wk <= 19:
            weekly[wk] = weekly.get(wk, 0.0) + mi
    return {"weekly": {str(k): round(v, 1) for k, v in weekly.items()},
            "runs": runs}


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
        out["error"] = str(e)
    _cache.update(well=out, well_ts=time.time())
    return out


def fetch_run_detail(activity_id):
    """One run's full story: summary stats, laps, downsampled pace/HR/elev
    series, and the GPS trace (rendered as an abstract route — no map tiles,
    no third-party requests, no home-location leak)."""
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
        d_arr, p_arr, h_arr, rt = [], [], [], []
        for i in range(len(pts)):
            if not dist_m or dist_m[i] is None:
                continue
            d_arr.append(round(dist_m[i] / MILE, 3))
            v = spd[i] if spd else None
            p_arr.append(int(MILE / v) if v and v > 0.5 else None)
            h_arr.append(hr[i] if hr else None)
            if lat and lon and lat[i] is not None and lon[i] is not None:
                rt.append([round(lat[i], 5), round(lon[i], 5)])
        out["series"] = {"d": d_arr, "pace": p_arr, "hr": h_arr}
        out["route"] = rt
    except Exception:
        pass
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


def plan_summary():
    plan = build_plan()
    weekly = {}
    for p in plan:
        weekly[p["week"]] = weekly.get(p["week"], 0.0) + p["distance_mi"]
    return {
        "race": RACE_DATE.isoformat(),
        "start": PLAN_START.isoformat(),
        "today": date.today().isoformat(),
        "plannedWeekly": {str(k): round(v, 1) for k, v in weekly.items()},
        "planMiles": {p["name"]: round(p["distance_mi"], 1) for p in plan},
        "planTargets": {p["name"]: _main_target(p["payload"]) for p in plan},
    }


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
            if not self._authorized():
                return self._json({"error": "unauthorized — use the link with ?key=… printed in Terminal"}, 403)
            route = self.path.split("?")[0]
            if route == "/" or route.startswith("/index"):
                body = PAGE.encode()
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
            else:
                self._json({"error": "not found"}, 404)
        except (KeyError, ValueError) as e:
            self._json({"error": "bad request: %s" % e}, 400)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, fmt, *args):
        pass


PAGE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MCM Coach</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0e1116">
<style>
 :root{
  --bg:#0e1116; --panel:#161b22; --cell:#11151b; --line:#232a33;
  --tx:#f0f3f6; --dim:#93a0ad; --faint:#5b6671;
  --easy:#34c77b; --strides:#3ec6c0; --long:#5aa2ff; --tempo:#f5a623;
  --hard:#ff6b6b; --accent:#5aa2ff; --good:#34c77b;
 }
 *{box-sizing:border-box;-webkit-font-smoothing:antialiased;-webkit-tap-highlight-color:transparent}
 body{margin:0;background:var(--bg);color:var(--tx);
  font:14px/1.45 -apple-system,'SF Pro Text',Helvetica,Arial,sans-serif;
  padding-top:env(safe-area-inset-top)}
 .wrap{max-width:1140px;margin:0 auto;padding:22px 18px 70px}

 .hero{display:flex;align-items:flex-end;gap:20px;flex-wrap:wrap;margin-bottom:16px}
 .hero h1{font-size:24px;font-weight:700;letter-spacing:-.4px;margin:0}
 .hero .race{color:var(--dim);font-size:13px;margin-top:3px}
 .stats{display:flex;gap:10px;margin-left:auto;flex-wrap:wrap}
 .stat{background:var(--panel);border:1px solid var(--line);border-radius:13px;
  padding:9px 16px;text-align:center;min-width:88px}
 .stat b{display:block;font-size:20px;letter-spacing:-.5px}
 .stat span{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.6px}

 /* tab navigation — pills on desktop, fixed bottom bar on mobile */
 .tabbar{display:flex;gap:4px;background:var(--panel);border:1px solid var(--line);
  border-radius:999px;padding:4px;width:fit-content;margin:0 0 16px;z-index:40}
 .tab{display:flex;gap:7px;align-items:center;padding:8px 18px;border-radius:999px;
  color:var(--dim);font-size:13px;font-weight:600;cursor:pointer;user-select:none}
 .tab .ic{font-size:15px}
 .tab.active{background:var(--accent);color:#fff}
 .wstrip{display:flex;gap:2px;background:var(--panel);border:1px solid var(--line);
  border-radius:15px;padding:10px 6px 8px;margin-bottom:12px}
 .wd{flex:1;text-align:center;cursor:pointer;border-radius:10px;padding:4px 0}
 .wd:hover{background:#1b222b}
 .wd .l{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
 .wd .n{font-size:14px;font-weight:600;margin:4px 0 3px;width:28px;height:28px;line-height:28px;
  border-radius:50%;display:inline-block}
 .wd.today .n{background:var(--accent);color:#fff}
 .wd .dots{height:10px;display:flex;gap:3px;justify-content:center;align-items:center}
 .wd .dots i{width:7px;height:7px;border-radius:4px;display:inline-block}
 .wd .dots .ck{color:var(--good);font-size:10px;font-weight:800;line-height:10px}
 .brief{background:linear-gradient(135deg,#1a2433,#16202c);border:1px solid #27384d;
  border-radius:15px;padding:15px 18px 14px 22px;margin-bottom:14px;position:relative;overflow:hidden}
 .brief::before{content:'';position:absolute;left:0;top:0;bottom:0;width:6px;
  background:var(--bcolor,var(--easy))}
 .brief .top{display:flex;gap:12px;align-items:flex-start}
 .brief b{font-size:18px;letter-spacing:-.3px}
 .brief .sub{color:var(--dim);font-size:13px;margin-top:3px}
 .wx{background:#11161b;border:1px solid var(--line);border-radius:999px;
  padding:6px 12px;font-size:13px;white-space:nowrap;margin-left:auto;flex:none}
 .donechip{background:var(--good);color:#06210f;border-radius:999px;padding:6px 12px;
  font-size:12.5px;font-weight:700;margin-left:auto;flex:none}
 .brief .cta{margin-top:11px;display:flex;gap:8px}
 .ready{display:flex;gap:14px;margin-top:9px;flex-wrap:wrap;color:var(--dim);font-size:12.5px}
 .ready b{color:var(--tx);font-weight:600}
 .banner{border-radius:12px;padding:10px 15px;margin-bottom:14px;font-size:13.5px;display:none}
 .banner.amber{display:block;background:#2c2410;border:1px solid #4d3f1c;color:#f0d28a}
 .banner.red{display:block;background:#2d1515;border:1px solid #553030;color:#f3a9a0}

 .bar{display:flex;align-items:center;gap:9px;margin-bottom:12px;flex-wrap:wrap}
 .mnav{display:flex;align-items:center;gap:2px;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;padding:2px}
 .mnav b{min-width:118px;text-align:center;font-size:14px}
 button{background:var(--panel);color:var(--tx);border:1px solid var(--line);
  border-radius:9px;padding:8px 14px;font-size:13px;cursor:pointer;font-family:inherit}
 button:hover{border-color:var(--accent)}
 button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
 button.ghost{border:none;background:none;color:var(--accent);font-weight:600;padding:8px 9px}
 .legend{margin-left:auto;display:flex;gap:11px;color:var(--dim);font-size:11.5px;flex-wrap:wrap}
 .legend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:4px}

 .cal{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}
 .dow,.grid{display:grid;grid-template-columns:repeat(7,1fr)}
 .dow div{padding:9px 0 7px;text-align:center;font-size:10.5px;color:var(--dim);
  text-transform:uppercase;letter-spacing:.8px;border-bottom:1px solid var(--line)}
 .cell{min-height:92px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);
  padding:5px;background:var(--cell);transition:background .12s}
 .cell:nth-child(7n){border-right:none}
 .cell.other{opacity:.35}
 .cell.over,.movemode .cell:hover{background:#1b2533;box-shadow:inset 0 0 0 2px var(--accent)}
 .movemode .cell{cursor:copy}
 .dnum{font-size:11.5px;color:var(--faint);margin:0 0 4px 3px;display:flex;align-items:center;gap:5px}
 .cell.today .dnum b{background:var(--accent);color:#fff;border-radius:50%;
  width:21px;height:21px;display:inline-flex;align-items:center;justify-content:center;font-size:11.5px}
 .ran{color:var(--good);font-size:10.5px;font-weight:700}
 .chip{border-radius:8px;padding:5px 7px;margin-bottom:4px;font-size:11.5px;font-weight:600;
  cursor:grab;color:#08130d;position:relative;line-height:1.25;user-select:none}
 .chip:active{cursor:grabbing}
 .chip .mi{display:block;font-weight:500;font-size:10.5px;opacity:.75}
 .chip.dragging{opacity:.4}
 .chip.past{filter:grayscale(.5);opacity:.6}
 .chip .warn{position:absolute;top:-6px;right:-4px;font-size:11px}
 .c-easy{background:var(--easy)} .c-strides{background:var(--strides)}
 .c-long{background:var(--long);color:#071120} .c-tempo{background:var(--tempo)}
 .c-hard{background:var(--hard);color:#1c0707}
 .skel{padding:42px;text-align:center;color:var(--faint);grid-column:1/-1}
 .skel i{display:inline-block;width:16px;height:16px;border:2px solid var(--faint);
  border-top-color:var(--accent);border-radius:50%;animation:sp 1s linear infinite;
  vertical-align:-3px;margin-right:9px}
 @keyframes sp{to{transform:rotate(360deg)}}

 .panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;
  padding:15px 17px;margin-top:16px}
 .panel h3{margin:0 0 9px;font-size:15px}
 .rampnote{color:var(--dim);font-size:12.5px;margin-top:7px}

 .scrim{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;
  align-items:center;justify-content:center;z-index:50;padding:16px}
 .scrim.show{display:flex}
 .modal{background:var(--panel);border:1px solid var(--line);border-radius:18px;
  padding:21px;width:390px;max-width:94vw;max-height:84vh;overflow:auto}
 .modal h3{margin:0 0 4px;font-size:17px}
 .modal p{color:var(--dim);margin:0 0 14px;font-size:13px}
 .modal label{display:block;font-size:12px;color:var(--dim);margin:10px 0 4px}
 .modal input{width:100%;background:var(--cell);border:1px solid var(--line);color:var(--tx);
  border-radius:9px;padding:10px 11px;font-size:16px;font-family:inherit} /* 16px stops iOS zoom-on-focus */
 .modal .preview{background:var(--cell);border-radius:10px;padding:10px 12px;margin-top:12px;
  font-size:13px;color:var(--dim);max-height:150px;overflow:auto;line-height:1.6}
 .modal .row{display:flex;gap:9px;justify-content:flex-end;margin-top:16px;flex-wrap:wrap}
 /* run sheet */
 .runsheet{width:560px}
 .runsheet h4{margin:16px 0 6px;font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.7px}
 .statgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin:12px 0 2px}
 .statgrid div{background:var(--cell);border-radius:10px;padding:9px 11px}
 .statgrid b{display:block;font-size:17px;letter-spacing:-.3px}
 .statgrid span{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px}
 .lapbars{display:flex;align-items:flex-end;gap:3px;height:110px;margin:6px 0 2px;position:relative}
 .lapbars .b{flex:1;background:var(--good);border-radius:3px 3px 0 0;min-width:3px;opacity:.92;position:relative;z-index:1}
 .lapbars .tband{position:absolute;left:0;right:0;background:rgba(255,255,255,.14);border-radius:3px;z-index:0}
 .splitrow{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12.5px}
 .splitrow .n{width:34px;color:var(--dim);flex:none;text-align:right}
 .splitrow .bar{height:23px;border-radius:6px;background:var(--strides);color:#06221f;font-weight:600;
  display:flex;align-items:center;padding:0 8px;font-size:12px;white-space:nowrap}
 .splitrow .pm{width:52px;text-align:right;font-size:12px}
 #toast{position:fixed;bottom:max(20px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);
  background:#222b36;border:1px solid var(--line);color:var(--tx);border-radius:12px;
  padding:11px 16px;display:none;align-items:center;gap:13px;z-index:60;
  box-shadow:0 8px 30px rgba(0,0,0,.5);font-size:13px;max-width:92vw}
 #toast.err{border-color:var(--hard)}

 @media (max-width:700px){
  .wrap{padding:max(12px,env(safe-area-inset-top)) 10px 96px}
  .tabbar{position:fixed;bottom:0;left:0;right:0;width:auto;margin:0;border-radius:0;
   border:none;border-top:1px solid var(--line);background:rgba(14,17,22,.97);
   -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
   padding:7px 0 calc(7px + env(safe-area-inset-bottom))}
  .tab{flex:1;flex-direction:column;gap:2px;padding:2px 0;font-size:10px;border-radius:0}
  .tab .ic{font-size:21px}
  .tab.active{background:none;color:var(--accent)}
  .hero{gap:12px;margin-bottom:12px}
  .hero h1{font-size:21px}
  .hero .race{font-size:12px}
  .stats{margin-left:0;width:100%;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
  .stat{min-width:0;padding:9px 6px}.stat b{font-size:19px}
  .legend{display:none}
  .bar{gap:7px}
  .bar button{padding:9px 12px}            /* >=44pt tap targets */
  .mnav b{min-width:104px}
  .cell{min-height:76px;padding:3px 2px}
  .dnum{font-size:10.5px;margin-left:2px}
  .cell.today .dnum b{width:19px;height:19px;font-size:10.5px}
  .ran{font-size:9.5px}
  .chip{font-size:10.5px;padding:5px 5px;border-radius:6px}
  .chip .mi{display:none}
  /* modals become bottom sheets — the native-app gesture language */
  .scrim{align-items:flex-end;padding:0}
  .modal{width:100%;max-width:100%;max-height:88vh;border-radius:18px 18px 0 0;
   padding:18px 16px calc(18px + env(safe-area-inset-bottom))}
  .runsheet{width:100%}
  .statgrid b{font-size:15px}
  .splitrow .pm{width:46px}
 }
</style></head><body><div class="wrap">

 <div class="hero">
   <div><h1>Marine Corps Marathon</h1>
   <div class="race">Sun Oct 25, 2026 · goal sub-3:25 · 7:50/mi MP</div></div>
   <div class="stats">
     <div class="stat"><b id="cdays">—</b><span>days to go</span></div>
     <div class="stat"><b id="wkmi">—</b><span>mi this week</span></div>
     <div class="stat"><b id="nsched">—</b><span>scheduled</span></div>
   </div>
 </div>

 <nav class="tabbar">
  <div class="tab active" data-v="today" onclick="setView('today')"><span class="ic">☀️</span>Today</div>
  <div class="tab" data-v="plan" onclick="setView('plan')"><span class="ic">📅</span>Plan</div>
  <div class="tab" data-v="acts" onclick="setView('acts')"><span class="ic">📈</span>Activities</div>
 </nav>

 <div id="v-today">
 <div class="wstrip" id="wstrip"></div>
 <div class="banner" id="banner"></div>
 <div class="brief" id="brief" style="display:none"></div>
 <div class="panel" id="weekpanel" style="display:none"></div>
 </div>

 <div id="v-plan" style="display:none">
 <div class="bar">
   <div class="mnav">
     <button class="ghost" onclick="nav(-1)">‹</button>
     <b id="mlabel"></b>
     <button class="ghost" onclick="nav(1)">›</button>
   </div>
   <button onclick="openVacation()">✈️ Vacation</button>
   <button onclick="load(true)">↻</button>
   <div class="legend">
     <span><i style="background:var(--easy)"></i>Easy</span>
     <span><i style="background:var(--strides)"></i>Strides</span>
     <span><i style="background:var(--tempo)"></i>Tempo·Hills</span>
     <span><i style="background:var(--hard)"></i>Intervals</span>
     <span><i style="background:var(--long)"></i>Long</span>
   </div>
 </div>

 <div class="cal">
   <div class="dow"><div>Mon</div><div>Tue</div><div>Wed</div><div>Thu</div>
     <div>Fri</div><div>Sat</div><div>Sun</div></div>
   <div class="grid" id="grid"><div class="skel"><i></i>Loading your calendar from Garmin…</div></div>
 </div>

 <div class="panel"><h3>Weekly mileage — planned vs run</h3>
   <div id="chart"></div><div class="rampnote" id="rampnote"></div></div>
 </div>

 <div id="v-acts" style="display:none">
 <div class="panel" id="actpanel"><h3>Activities</h3></div>
 </div>

 <div class="scrim" id="scrim">
  <div class="modal">
    <h3>Vacation mode</h3>
    <p>Pick the days you'll be away. Every workout in that range moves by the
       number of days you choose — preview first, nothing changes until you confirm.</p>
    <label>Away from</label><input type="date" id="vfrom">
    <label>Until</label><input type="date" id="vto">
    <label>Move those workouts by (days; 7 = one week later)</label>
    <input type="number" id="vdays" value="7">
    <div class="preview" id="vpreview">Pick dates to preview.</div>
    <div class="row">
      <button onclick="closeVacation()">Cancel</button>
      <button class="primary" id="vgo" onclick="doVacation()" disabled>Move workouts</button>
    </div>
  </div>
 </div>

 <div class="scrim" id="dscrim" onclick="if(event.target===this)closeDetail()">
  <div class="modal" id="dmodal"></div>
 </div>

 <div class="scrim" id="rscrim" onclick="if(event.target===this)closeRun()">
  <div class="modal runsheet" id="rmodal"></div>
 </div>

 <div id="toast"></div>

<script>
let S={schedule:[],plan:null,runs:[],weeklyActual:{},wellness:null,month:null,undo:null,moveItem:null};
const DAY=864e5;
const fmt=d=>d.toISOString().slice(0,10);
const parse=s=>new Date(s+'T12:00:00');
const fmtPace=s=>s?Math.floor(s/60)+':'+String(s%60).padStart(2,'0'):'—';

function kind(t){
 if(/MP Finish|mi LR/.test(t))return'c-long';
 if(/Strides/.test(t))return'c-strides';
 if(/Tempo|Hill/.test(t))return'c-tempo';
 if(/\dx/.test(t))return'c-hard';
 return'c-easy';
}
const isHard=t=>/Tempo|Hill|\dx|MP Finish/.test(t);
const kindVar={'c-easy':'easy','c-strides':'strides','c-tempo':'tempo','c-hard':'hard','c-long':'long'};

function toast(msg,opts){
 opts=opts||{};
 const t=document.getElementById('toast');
 t.className=opts.err?'err':'';
 t.innerHTML=msg+(opts.undo?' <button class="ghost" onclick="doUndo()">Undo</button>':'')+
   (opts.cancel?' <button class="ghost" onclick="exitMoveMode()">Cancel</button>':'');
 t.style.display='flex';
 clearTimeout(t._h);
 if(!opts.sticky)t._h=setTimeout(()=>t.style.display='none',opts.undo?6000:3000);
}
function hideToast(){document.getElementById('toast').style.display='none';}
const KEY=new URLSearchParams(location.search).get('key')||'';
async function jget(u){const r=await fetch(u,{headers:{'X-Key':KEY}});const j=await r.json();
 if(j.error)throw new Error(j.error);return j;}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'X-Key':KEY},body:JSON.stringify(b)});
 const j=await r.json();if(j.error)throw new Error(j.error);return j;}

async function load(force){
 try{
  const d=await jget('/api/data'+(force?'?refresh=1':''));
  S.plan=d.plan;S.schedule=d.schedule;
  if(S.month===null){
   const t=parse(S.plan.today),lo=parse(S.plan.start),hi=parse(S.plan.race);
   const c=t<lo?lo:(t>hi?hi:t);S.month=c.getFullYear()*12+c.getMonth();
  }
  render();
  jget('/api/actuals').then(j=>{S.runs=j.runs||[];S.weeklyActual=j.weekly||{};render();}).catch(()=>{});
  jget('/api/wellness').then(j=>{S.wellness=j;render();}).catch(()=>{});
  jget('/api/weather').then(j=>{S.weather=j;render();}).catch(()=>{});
 }catch(e){toast('Couldn’t reach Garmin: '+e.message,{err:1});}
}
function nav(d){S.month+=d;render();}

function setView(v){
 S.view=v;
 const m={today:'v-today',plan:'v-plan',acts:'v-acts'};
 Object.keys(m).forEach(k=>document.getElementById(m[k]).style.display=k===v?'':'none');
 document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.v===v));
 try{localStorage.setItem('coachView',v);}catch(e){}
 window.scrollTo(0,0);
}

/* ---------------- readiness ---------------- */
function readiness(){
 const w=S.wellness;
 if(!w||!w.days||!w.days.length)return null;
 const today=w.days[0],hist=w.days.slice(1).filter(d=>d.rhr);
 const base=hist.length?Math.round(hist.reduce((a,d)=>a+d.rhr,0)/hist.length):null;
 const flags=[];
 if(today.rhr&&base&&today.rhr-base>=5)flags.push('resting HR +'+(today.rhr-base)+' over your baseline');
 if(today.sleepH!==null&&today.sleepH>0&&today.sleepH<6)flags.push('only '+today.sleepH+'h sleep');
 if(today.bb!==null&&today.bb!==undefined&&today.bb<30)flags.push('Body Battery at '+today.bb);
 return {today:today,base:base,flags:flags,level:flags.length>=2?'red':(flags.length?'amber':'ok')};
}

function render(){
 if(!S.plan)return;
 const today=S.plan.today;
 document.getElementById('cdays').textContent=
   Math.max(0,Math.round((parse(S.plan.race)-parse(today))/DAY));
 document.getElementById('nsched').textContent=S.schedule.length;
 const wk=Math.floor((parse(today)-parse(S.plan.start))/DAY/7)+1;
 const ran=S.weeklyActual[wk]||0, plan=S.plan.plannedWeekly[wk];
 document.getElementById('wkmi').textContent=plan?(ran.toFixed(0)+' / '+Math.round(plan)):'—';
 renderStrip(today);
 renderBrief(today);
 renderGrid(today);
 renderWeek(today);
 renderChart();
 renderRamp(wk);
 renderActs();
}

function renderActs(){
 const el=document.getElementById('actpanel');
 const runs=S.runs.slice().sort((a,b)=>b.date.localeCompare(a.date));
 if(!runs.length){
  el.innerHTML='<h3>Activities</h3><p style="color:var(--dim)">Completed runs land here automatically once training starts — tap any for the full breakdown.</p>';
  return;
 }
 const tot=runs.reduce((a,r)=>a+r.mi,0);
 let h='<h3>Activities <span style="color:var(--dim);font-weight:400">— '+
   runs.length+' runs · '+tot.toFixed(0)+' mi</span></h3>';
 let lastWk=null;
 runs.forEach(r=>{
  const wk=Math.floor((parse(r.date)-parse(S.plan.start))/DAY/7)+1;
  if(wk!==lastWk){
   h+='<div style="color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.7px;margin:14px 0 2px">Week '+wk+
    (S.weeklyActual[wk]?' · '+S.weeklyActual[wk]+' of '+(S.plan.plannedWeekly[wk]||'?')+' mi':'')+'</div>';
   lastWk=wk;
  }
  const it=S.schedule.find(i=>i.date===r.date);
  const title=(it?it.title:r.name).replace(/^W\d+ \w+ /,'');
  const a=it?assess(it):null;
  const col=a?(a.distOk&&a.paceOk!==false?'var(--good)':'var(--tempo)'):'var(--dim)';
  h+='<div onclick="openRun(\''+r.activityId+'\',\''+(it?it.title:'')+'\')"'+
   ' style="display:flex;gap:11px;align-items:center;padding:10px 0;border-top:1px solid var(--line);cursor:pointer">'+
   '<div style="width:9px;height:9px;border-radius:5px;flex:none;background:var(--'+
     (it?kindVar[kind(it.title)]:'easy')+')"></div>'+
   '<div style="flex:1;min-width:0"><b>'+title+'</b>'+
   '<div style="color:var(--dim);font-size:12px">'+
     parse(r.date).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'</div></div>'+
   '<div style="text-align:right"><b>'+r.mi.toFixed(1)+' mi</b>'+
   '<div style="color:'+col+';font-size:12px">'+(r.pace?r.pace+'/mi':'')+
     (a&&a.paceMsg?' · '+a.paceMsg:'')+'</div></div>'+
   '<span style="color:var(--faint)">›</span></div>';
 });
 el.innerHTML=h;
}

function renderStrip(today){
 const t=parse(today);
 const mon=new Date(t);mon.setDate(t.getDate()-((t.getDay()+6)%7));
 let h='';
 for(let i=0;i<7;i++){
  const d=new Date(mon.getTime()+i*DAY),ds=fmt(d);
  const its=S.schedule.filter(x=>x.date===ds);
  const ran=runsOn(ds).length>0;
  h+='<div class="wd'+(ds===today?' today':'')+'" onclick="stripTap(\''+ds+'\')">'+
   '<div class="l">'+['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][i]+'</div>'+
   '<span class="n">'+d.getDate()+'</span>'+
   '<div class="dots">'+(ran?'<span class="ck">✓</span>':
    its.slice(0,3).map(x=>'<i style="background:var(--'+kindVar[kind(x.title)]+')"></i>').join(''))+
   '</div></div>';
 }
 document.getElementById('wstrip').innerHTML=h;
}
function stripTap(ds){
 const rs=runsOn(ds).filter(r=>r.activityId).sort((a,b)=>b.mi-a.mi);
 const its=S.schedule.filter(x=>x.date===ds);
 if(rs.length)openRun(rs[0].activityId,its[0]?its[0].title:null);
 else if(its.length)openDetail(its[0].scheduleId);
}
function estRange(title){
 const mi=S.plan.planMiles[title];if(!mi)return'';
 const t=S.plan.planTargets[title];
 const lo=Math.round(mi*(t?t.fastSec:585)/60),hi=Math.round(mi*(t?t.slowSec:630)/60);
 return lo+'–'+hi+'m';
}
function renderBrief(today){
 const br=document.getElementById('brief'),bn=document.getElementById('banner');
 const next=S.schedule.filter(i=>i.date>=today).sort((a,b)=>a.date.localeCompare(b.date))[0];
 const r=readiness();
 bn.className='banner';bn.textContent='';
 if(!next){br.style.display='none';return;}
 const isToday=next.date===today;
 const t=S.plan.planTargets[next.title];
 const done=isToday&&runsOn(today).length>0;
 const short=next.title.replace(/^W\d+ \w+ /,'');
 const rel=isToday?'Today':parse(next.date).toLocaleDateString(undefined,{weekday:'long',month:'short',day:'numeric'});
 const wx=S.weather&&S.weather.tempF?
  '<span class="wx">☁️ '+S.weather.tempF+'°'+
   (S.weather.humidity>=70?' · '+S.weather.humidity+'% hum':'')+'</span>':'';
 br.style.setProperty('--bcolor','var(--'+kindVar[kind(next.title)]+')');
 let h='<div class="top"><div style="flex:1;min-width:0">'+
  '<b>'+short+'</b>'+
  '<div class="sub">'+rel+' · '+(S.plan.planMiles[next.title]||'?')+' mi · '+estRange(next.title)+
  (t?' · '+t.label:'')+'</div></div>'+
  (done?'<span class="donechip">✓ Done</span>':wx)+'</div>';
 if(r&&r.today){
  h+='<div class="ready">'+
   (r.today.rhr?'<span>RHR <b>'+r.today.rhr+'</b>'+(r.base?' <span style="color:var(--faint)">(7-day '+r.base+')</span>':'')+'</span>':'')+
   (r.today.sleepH?'<span>Sleep <b>'+r.today.sleepH+'h</b></span>':'')+
   ((r.today.bb!==null&&r.today.bb!==undefined)?'<span>Body Battery <b>'+r.today.bb+'</b></span>':'')+
   (r.level==='ok'?'<span style="color:var(--good)">● ready</span>':'')+'</div>';
 }
 const hot=S.weather&&(S.weather.feelsF>=80||(S.weather.tempF>=78&&S.weather.humidity>=70));
 if(hot&&isToday){
  h+='<div class="ready" style="color:var(--tempo)">🔥 Feels like '+
   (S.weather.feelsF||S.weather.tempF)+'° — add 15–20s/mi to targets and hydrate; effort over pace today.</div>';
 }
 h+='<div class="cta"><button onclick="openDetail('+next.scheduleId+')">Details</button>'+
  (done?'<button onclick="stripTap(\''+today+'\')">View run ▸</button>':'')+'</div>';
 br.innerHTML=h;br.style.display='block';
 if(r&&r.flags.length&&isToday&&isHard(next.title)){
  bn.className='banner '+(r.level==='red'?'red':'amber');
  bn.textContent=(r.level==='red'?'Rough recovery: ':'Heads up: ')+r.flags.join(', ')+
   '. Today is a quality day — consider swapping it with an easy day (drag it on the calendar). One moved workout beats one forced injury.';
 }
}

function renderGrid(today){
 const y=Math.floor(S.month/12),m=S.month%12;
 document.getElementById('mlabel').textContent=
   new Date(y,m,1).toLocaleDateString(undefined,{month:'long',year:'numeric'});
 const first=new Date(y,m,1);
 let start=new Date(first); start.setDate(1-((first.getDay()+6)%7));
 const byDate={};
 S.schedule.forEach(i=>(byDate[i.date]=byDate[i.date]||[]).push(i));
 const ranByDate={},ranTip={};
 S.runs.forEach(r=>{ranByDate[r.date]=(ranByDate[r.date]||0)+r.mi;
  ranTip[r.date]=((ranTip[r.date]||'')+' '+r.mi.toFixed(1)+'mi'+(r.pace?' @ '+r.pace+'/mi':'')).trim();});
 const hardDates=new Set(S.schedule.filter(i=>isHard(i.title)).map(i=>i.date));
 let html='';
 for(let i=0;i<42;i++){
  const d=new Date(start.getTime()+i*DAY), ds=fmt(d);
  const other=d.getMonth()!==m;
  html+='<div class="cell'+(other?' other':'')+(ds===today?' today':'')+'"'+
   ' data-date="'+ds+'" onclick="cellTap(event)"'+
   ' ondragover="dragOver(event)" ondragleave="dragLeave(event)" ondrop="drop(event)">'+
   '<div class="dnum"><b>'+d.getDate()+'</b>'+
   (ranByDate[ds]?'<span class="ran" onclick="ranTap(event,\''+ds+'\')" style="cursor:pointer" title="'+ranTip[ds]+' — tap for run details">✓ '+ranByDate[ds].toFixed(1)+'</span>':'')+'</div>';
  (byDate[ds]||[]).forEach(it=>{
   const prev=fmt(new Date(d.getTime()-DAY)),nxt=fmt(new Date(d.getTime()+DAY));
   const clash=isHard(it.title)&&(hardDates.has(prev)||hardDates.has(nxt));
   const short=it.title.replace(/^W\d+ \w+ /,'');
   html+='<div class="chip '+kind(it.title)+(it.date<today?' past':'')+'" draggable="true"'+
    ' id="c'+it.scheduleId+'" data-sid="'+it.scheduleId+'"'+
    ' ondragstart="dragStart(event)" ondragend="dragEnd(event)"'+
    ' onclick="chipTap(event,'+it.scheduleId+')"'+
    ' title="'+it.title+' — tap for details">'+short+
    '<span class="mi">'+(S.plan.planMiles[it.title]||'?')+' mi</span>'+
    (clash?'<span class="warn" title="Back-to-back hard days">⚠️</span>':'')+'</div>';
  });
  html+='</div>';
 }
 document.getElementById('grid').innerHTML=html;
}

/* ---------------- performance ---------------- */
function runsOn(ds){return S.runs.filter(r=>r.date===ds);}
function assess(it){
 const planned=S.plan.planMiles[it.title]||0;
 const target=S.plan.planTargets[it.title];
 const rs=runsOn(it.date);
 if(!rs.length)return null;
 const mi=rs.reduce((a,r)=>a+r.mi,0);
 const main=rs.slice().sort((a,b)=>b.mi-a.mi)[0];
 const distOk=mi>=planned*0.9;
 let paceMsg='',paceOk=null;
 if(target&&main.paceSec){
  if(main.paceSec<target.fastSec-10){paceMsg='faster than target';paceOk=false;}
  else if(main.paceSec>target.slowSec+10){paceMsg='slower than target';paceOk=false;}
  else{paceMsg='on target';paceOk=true;}
 }
 return {mi:mi,paceSec:main.paceSec,distOk:distOk,paceOk:paceOk,paceMsg:paceMsg};
}

function renderWeek(today){
 const wk=Math.floor((parse(today)-parse(S.plan.start))/DAY/7)+1;
 const panel=document.getElementById('weekpanel');
 if(wk<1||wk>19){panel.style.display='none';return;}
 const items=S.schedule.filter(i=>{
  const w=Math.floor((parse(i.date)-parse(S.plan.start))/DAY/7)+1;return w===wk;
 }).sort((a,b)=>a.date.localeCompare(b.date));
 if(!items.length){panel.style.display='none';return;}
 const ran=S.weeklyActual[wk]||0,goal=S.plan.plannedWeekly[wk]||0;
 const pct=goal?Math.min(100,Math.round(ran/goal*100)):0;
 let h='<h3>Week '+wk+' report card <span style="color:var(--dim);font-weight:400">— '+
   ran.toFixed(1)+' of '+Math.round(goal)+' mi ('+pct+'%)</span></h3>'+
   '<div style="background:var(--cell);border-radius:6px;height:7px;margin:2px 0 13px">'+
   '<div style="background:var(--good);height:7px;border-radius:6px;width:'+pct+'%"></div></div>';
 items.forEach(it=>{
  const t=S.plan.planTargets[it.title];const a=assess(it);
  let status,color;
  if(a){
   const ok=a.distOk&&(a.paceOk!==false);
   status='✓ '+a.mi.toFixed(1)+' mi @ '+fmtPace(a.paceSec)+'/mi'+(a.paceMsg?' · '+a.paceMsg:'');
   color=ok?'var(--good)':'var(--tempo)';
  }else if(it.date<today){status='✗ missed — tap to reschedule';color='var(--hard)';}
  else if(it.date===today){status='today';color='var(--accent)';}
  else{status='upcoming';color='var(--faint)';}
  h+='<div onclick="openDetail('+it.scheduleId+')" style="display:flex;gap:10px;align-items:baseline;'+
   'padding:6px 0;border-top:1px solid var(--line);cursor:pointer;flex-wrap:wrap">'+
   '<span style="width:84px;color:var(--dim);flex:none">'+
     parse(it.date).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'</span>'+
   '<b style="flex:1;min-width:120px">'+it.title.replace(/^W\d+ \w+ /,'')+'</b>'+
   '<span style="color:var(--dim)">'+(S.plan.planMiles[it.title]||'?')+' mi'+(t?' · '+t.label:'')+'</span>'+
   '<span style="color:'+color+'">'+status+'</span></div>';
 });
 panel.innerHTML=h;panel.style.display='block';
}

function renderChart(){
 const planned=S.plan.plannedWeekly;
 const weeks=Object.keys(planned).map(Number).sort((a,b)=>a-b);
 const maxv=Math.max(...weeks.map(w=>Math.max(planned[w]||0,S.weeklyActual[w]||0)),1);
 const W=1040,H=140,bw=W/weeks.length;
 let s='<svg viewBox="0 0 '+W+' '+(H+22)+'" xmlns="http://www.w3.org/2000/svg" style="width:100%">';
 weeks.forEach((w,i)=>{
  const ph=(planned[w]||0)/maxv*H, ah=(S.weeklyActual[w]||0)/maxv*H, x=i*bw;
  s+='<rect x="'+(x+3)+'" y="'+(H-ph)+'" width="'+(bw/2-5)+'" height="'+Math.max(ph,1)+'" fill="#5aa2ff" opacity=".85" rx="2"/>';
  if(S.weeklyActual[w])s+='<rect x="'+(x+bw/2-1)+'" y="'+(H-ah)+'" width="'+(bw/2-5)+'" height="'+ah+'" fill="#34c77b" rx="2"/>';
  s+='<text x="'+(x+bw/2)+'" y="'+(H+15)+'" fill="#93a0ad" font-size="10.5" text-anchor="middle">W'+w+'</text>';
 });
 document.getElementById('chart').innerHTML=s+'</svg>';
}

function renderRamp(wk){
 const el=document.getElementById('rampnote');el.textContent='';
 if(wk<2||wk>19)return;
 const get=w=>S.weeklyActual[w]!==undefined&&w<wk?S.weeklyActual[w]:S.plan.plannedWeekly[w];
 const hist=[wk-1,wk-2,wk-3].filter(w=>w>=1).map(get).filter(v=>v);
 if(!hist.length)return;
 const avg=hist.reduce((a,b)=>a+b,0)/hist.length;
 const cur=S.plan.plannedWeekly[wk]||0;
 if(!avg||!cur)return;
 const pct=Math.round((cur/avg-1)*100);
 if(pct>25)el.innerHTML='⚠️ This week is <b style="color:var(--tempo)">+'+pct+'%</b> over your recent average — a big jump. Protect sleep, keep easy days truly easy.';
 else if(pct<-20)el.textContent='Recovery/taper week: '+pct+'% vs recent — let it be easy, the fitness is already in the bank.';
}

/* ---------------- drag & drop + tap-to-move ---------------- */
let dragSid=null;
function dragStart(e){dragSid=e.target.dataset.sid;e.target.classList.add('dragging');
 e.dataTransfer.effectAllowed='move';}
function dragEnd(e){e.target.classList.remove('dragging');
 document.querySelectorAll('.cell.over').forEach(c=>c.classList.remove('over'));}
function dragOver(e){e.preventDefault();e.currentTarget.classList.add('over');}
function dragLeave(e){e.currentTarget.classList.remove('over');}
function drop(e){
 e.preventDefault();
 const cell=e.currentTarget;cell.classList.remove('over');
 const it=S.schedule.find(i=>String(i.scheduleId)===String(dragSid));
 const nd=cell.dataset.date;
 if(it&&nd&&it.date!==nd)applyMove(it,nd,true);
}
function chipTap(e,sid){
 if(S.moveItem)return;        // bubbling to cellTap completes a move
 e.stopPropagation();
 openDetail(sid);
}
function cellTap(e){
 if(!S.moveItem)return;
 const nd=e.currentTarget.dataset.date;
 const it=S.moveItem;
 exitMoveMode();
 if(nd&&it.date!==nd)applyMove(it,nd,true);
}
function enterMoveMode(sid){
 const it=S.schedule.find(i=>String(i.scheduleId)===String(sid));
 if(!it)return;
 closeDetail();
 S.moveItem=it;
 document.body.classList.add('movemode');
 toast('Tap a day to move <b>'+it.title.replace(/^W\d+ \w+ /,'')+'</b>',{sticky:1,cancel:1});
}
function exitMoveMode(){
 S.moveItem=null;document.body.classList.remove('movemode');hideToast();
}
function applyMove(it,newDate,allowUndo){
 const old=it.date;
 it.date=newDate;render();
 jpost('/api/move',{scheduleId:it.scheduleId,workoutId:it.workoutId,date:newDate})
  .then(()=>{
   if(allowUndo){S.undo={it:it,back:old};
    toast('<b>'+it.title.replace(/^W\d+ \w+ /,'')+'</b>&nbsp;→ '+
     parse(newDate).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}),
     {undo:1});}
   load(true);
  })
  .catch(err=>{it.date=old;render();toast('Move failed — put it back. '+err.message,{err:1});});
}
function doUndo(){
 if(!S.undo)return;
 applyMove(S.undo.it,S.undo.back,false);
 toast('Undone');S.undo=null;
}

/* ---------------- workout detail ---------------- */
function openDetail(sid){
 const it=S.schedule.find(i=>String(i.scheduleId)===String(sid));
 if(!it)return;
 const t=S.plan.planTargets[it.title],a=assess(it),isRepeat=/\dx/.test(it.title);
 let h='<h3>'+it.title+'</h3><p>'+
   parse(it.date).toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'})+'</p>'+
   '<div class="preview" style="margin-top:0">'+
   '<b>Plan:</b> '+(S.plan.planMiles[it.title]||'?')+' mi'+
   (t?' · target '+t.label:' · no pace target (easy)')+'<br>';
 if(a){
  h+='<b>You ran:</b> '+a.mi.toFixed(2)+' mi @ '+fmtPace(a.paceSec)+'/mi<br>'+
   '<b>Distance:</b> '+(a.distOk?'✓ covered':'▲ short of plan')+'<br>'+
   (t&&a.paceMsg?'<b>Pace:</b> '+(a.paceOk?'🎯 ':'▲ ')+a.paceMsg+'<br>':'')+
   (isRepeat?'<span style="color:var(--faint)">Interval day: average pace includes recovery jogs — check lap splits in Garmin Connect for true rep paces.</span>':'');
 }else if(it.date<S.plan.today){
  h+='<span style="color:var(--hard)">No run recorded this day.</span>';
 }else{
  h+='<span style="color:var(--faint)">Not run yet.</span>';
 }
 const big=runsOn(it.date).filter(r=>r.activityId).sort((x,y)=>y.mi-x.mi)[0];
 h+='</div><div class="row">'+
  (big?'<button onclick="closeDetail();openRun(\''+big.activityId+'\',\''+it.title.replace(/'/g,'')+'\')">View run ▸</button>':'')+
  '<button onclick="enterMoveMode('+it.scheduleId+')">Move to another day…</button>'+
  '<button class="primary" onclick="closeDetail()">Done</button></div>';
 document.getElementById('dmodal').innerHTML=h;
 document.getElementById('dscrim').classList.add('show');
}
function closeDetail(){document.getElementById('dscrim').classList.remove('show');}

/* ---------------- run detail sheet ---------------- */
const fmtDur=s=>{const h=Math.floor(s/3600),m=Math.floor(s/60)-h*60,x=s-h*3600-m*60;
 return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(x).padStart(2,'0');};
function ranTap(e,ds){
 e.stopPropagation();
 if(S.moveItem)return;
 const rs=runsOn(ds).filter(r=>r.activityId).sort((a,b)=>b.mi-a.mi);
 if(!rs.length)return;
 const it=S.schedule.find(i=>i.date===ds);
 openRun(rs[0].activityId,it?it.title:null);
}
async function openRun(aid,title){
 const m=document.getElementById('rmodal');
 m.innerHTML='<div class="skel" style="padding:60px 20px"><i></i>Loading run from Garmin…</div>';
 document.getElementById('rscrim').classList.add('show');
 let j;
 try{j=await jget('/api/run/'+aid);}
 catch(e){m.innerHTML='<h3>Couldn’t load run</h3><p>'+e.message+
  '</p><div class="row"><button onclick="closeRun()">Close</button></div>';return;}
 renderRun(j,title?S.plan.planTargets[title]:null,title);
}
function closeRun(){document.getElementById('rscrim').classList.remove('show');}

function routeSvg(rt){
 if(!rt||rt.length<8)return'';
 const lats=rt.map(p=>p[0]),lons=rt.map(p=>p[1]);
 const la0=Math.min(...lats),la1=Math.max(...lats),lo0=Math.min(...lons),lo1=Math.max(...lons);
 const W=520,H=180,pad=12;
 const kx=Math.cos((la0+la1)/2*Math.PI/180);   // shrink longitude at latitude
 const spanX=(lo1-lo0)*kx||1e-9, spanY=(la1-la0)||1e-9;
 const sc=Math.min((W-2*pad)/spanX,(H-2*pad)/spanY);
 const ox=(W-spanX*sc)/2, oy=(H-spanY*sc)/2;
 const pt=p=>[(ox+((p[1]-lo0)*kx)*sc).toFixed(1),(oy+(la1-p[0])*sc).toFixed(1)];
 let dStr='M'+pt(rt[0]).join(' ');
 for(let i=1;i<rt.length;i++)dStr+=' L'+pt(rt[i]).join(' ');
 const a=pt(rt[0]),b=pt(rt[rt.length-1]);
 return '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;background:var(--cell);border-radius:12px">'+
  '<path d="'+dStr+'" fill="none" stroke="var(--strides)" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" opacity=".95"/>'+
  '<circle cx="'+a[0]+'" cy="'+a[1]+'" r="5" fill="var(--good)"/>'+
  '<circle cx="'+b[0]+'" cy="'+b[1]+'" r="5" fill="#fff"/></svg>';
}

function lineSvg(d,vals,color,invert){
 const pts=[];
 for(let i=0;i<d.length;i++)if(vals[i]!=null)pts.push([d[i],vals[i]]);
 if(pts.length<5)return'';
 const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
 const x1=Math.max(...xs),v0=Math.min(...ys),v1=Math.max(...ys);
 const W=520,H=90,sp=(v1-v0)||1;
 const path=pts.map((p,i)=>{
  const x=(p[0]/x1)*W;
  let y=(p[1]-v0)/sp; if(invert)y=1-y;
  return (i?'L':'M')+x.toFixed(1)+' '+(H-8-y*(H-16)).toFixed(1);
 }).join(' ');
 return '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;background:var(--cell);border-radius:10px">'+
  '<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="2"/></svg>';
}

function renderRun(j,target,title){
 const s=j.summary||{},laps=j.laps||[],ser=j.series||{};
 let h='<h3>'+(title||s.name)+'</h3><p>'+s.name+'</p>';
 h+=routeSvg(j.route);
 h+='<div class="statgrid">'+
  '<div><b>'+(s.mi!=null?s.mi.toFixed(2):'—')+'</b><span>miles</span></div>'+
  '<div><b>'+(s.durSec?fmtDur(s.durSec):'—')+'</b><span>time</span></div>'+
  '<div><b>'+(s.paceSec?fmtPace(s.paceSec):'—')+'</b><span>avg /mi</span></div>'+
  '<div><b>'+(s.avgHr||'—')+'</b><span>avg hr</span></div>'+
  '<div><b>'+(s.cad?Math.round(s.cad)+'':'—')+'</b><span>cadence</span></div>'+
  '<div><b>'+(s.elevFt!=null?s.elevFt+'′':'—')+'</b><span>elev gain</span></div></div>';
 if(laps.length>1){
  // laps vs target: bar height ∝ speed, white band = target pace range
  const v=laps.map(l=>1/l.paceSec);
  let vmin=Math.min(...v),vmax=Math.max(...v);
  if(target){vmin=Math.min(vmin,1/target.slowSec);vmax=Math.max(vmax,1/target.fastSec);}
  const lo=vmin*0.93,span=(vmax*1.03-lo)||1e-9;
  const hpc=x=>Math.round((1/x-lo)/span*100);
  h+='<h4>Laps'+(target?' vs target ('+target.label+')':'')+'</h4><div class="lapbars">';
  if(target){
   const top=hpc(target.fastSec),bot=hpc(target.slowSec);
   h+='<div class="tband" style="bottom:'+bot+'%;height:'+Math.max(3,top-bot)+'%"></div>';
  }
  laps.forEach(l=>{h+='<div class="b" style="height:'+Math.max(4,hpc(l.paceSec))+'%" title="'+
    l.mi.toFixed(2)+' mi @ '+fmtPace(l.paceSec)+'/mi"></div>';});
  h+='</div>';
  h+='<h4>Splits</h4>';
  const fastest=Math.min(...laps.map(l=>l.paceSec));
  laps.forEach((l,i)=>{
   const w=35+55*(fastest/l.paceSec);
   let pm='';
   if(i>0){const dlt=laps[i-1].paceSec-l.paceSec;
    pm='<span class="pm" style="color:'+(dlt>=0?'var(--good)':'var(--hard)')+'">'+
     (dlt>=0?'+':'−')+fmtPace(Math.abs(dlt))+'</span>';}
   h+='<div class="splitrow"><span class="n">'+(l.mi>=0.95&&l.mi<=1.05?(i+1):l.mi.toFixed(2))+
    '</span><div class="bar" style="width:'+w.toFixed(0)+'%">'+fmtPace(l.paceSec)+'/mi</div>'+pm+'</div>';
  });
 }
 if(ser.d&&ser.d.length){
  if(ser.pace&&ser.pace.some(x=>x!=null)){h+='<h4>Pace</h4>'+lineSvg(ser.d,ser.pace,'#3ec6c0',true);}
  if(ser.hr&&ser.hr.some(x=>x!=null)){h+='<h4>Heart rate</h4>'+lineSvg(ser.d,ser.hr,'#ff6b6b',false);}
 }
 h+='<div class="row"><button class="primary" onclick="closeRun()">Done</button></div>';
 document.getElementById('rmodal').innerHTML=h;
}

/* ---------------- vacation mode ---------------- */
function openVacation(){
 document.getElementById('scrim').classList.add('show');
 ['vfrom','vto','vdays'].forEach(id=>document.getElementById(id).oninput=previewVacation);
 previewVacation();
}
function closeVacation(){document.getElementById('scrim').classList.remove('show');}
function previewVacation(){
 const f=document.getElementById('vfrom').value,tt=document.getElementById('vto').value;
 const days=parseInt(document.getElementById('vdays').value,10);
 const pv=document.getElementById('vpreview'),go=document.getElementById('vgo');
 if(!f||!tt||isNaN(days)){pv.textContent='Pick dates to preview.';go.disabled=true;return;}
 const hits=S.schedule.filter(i=>i.date>=f&&i.date<=tt);
 if(!hits.length){pv.textContent='No workouts in that range — nothing to move.';go.disabled=true;return;}
 pv.innerHTML=hits.map(i=>{
  const nd=fmt(new Date(parse(i.date).getTime()+days*DAY));
  return i.title+': '+i.date+' → <b>'+nd+'</b>';
 }).join('<br>');
 go.disabled=false;go.textContent='Move '+hits.length+' workout'+(hits.length>1?'s':'');
}
async function doVacation(){
 const f=document.getElementById('vfrom').value,tt=document.getElementById('vto').value;
 const days=parseInt(document.getElementById('vdays').value,10);
 closeVacation();toast('Moving workouts…',{sticky:1});
 try{
  const r=await jpost('/api/shift_range',{from:f,to:tt,days:days});
  toast('Moved '+r.moved+' workouts — sync your watch when you’re ready');
  load(true);
 }catch(e){toast('Shift failed: '+e.message,{err:1});}
}

try{setView(localStorage.getItem('coachView')||'today');}catch(e){setView('today');}
load(false);
</script></div></body></html>
"""


# ------------------------------------------------------------------ notify

def cmd_notify():
    """One-shot macOS notification with today's workout + readiness.
    Schedule it (cron / Shortcuts / launchd) for a daily nudge, e.g.:
      crontab -e   →   0 7 * * * cd "<this folder>" && /usr/bin/python3 coach.py notify
    """
    today = date.today().isoformat()
    items = [i for i in fetch_schedule() if i["date"] == today]
    if items:
        msg = "Today: " + " + ".join(i["title"] for i in items)
    else:
        msg = "Rest day — no workout scheduled. Recovery is training too."
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
    print(msg)
    try:
        subprocess.run(["osascript", "-e",
                        'display notification "%s" with title "MCM Coach"'
                        % msg.replace('"', "'")], check=False)
    except Exception:
        pass


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
    args = ap.parse_args()

    if args.command == "notify":
        cmd_notify()
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
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
