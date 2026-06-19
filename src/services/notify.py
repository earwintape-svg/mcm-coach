"""Notification service — macOS push + ntfy.sh phone alerts."""
import os
import subprocess
import time
import urllib.request
from datetime import date, timedelta

import store
from plan import RACE_DATE
from src.services.schedule import fetch_schedule
from src.services.actuals import fetch_actuals
from src.services.wellness import fetch_wellness
from src.services.fitness import fetch_fitness, fetch_prs
from src.services.weather import fetch_weather, heat_pct
from src.services.plan_svc import plan_summary
from src.services.trends import build_week_review
from src.services.applog import get_logger
from builders import MILE

log = get_logger("notify")


def _fmt_pace(mps: float) -> str:
    s = int(round(MILE / mps))
    return "%d:%02d" % (s // 60, s % 60)


def _push(msg: str):
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
            req = urllib.request.Request(
                "https://ntfy.sh/" + topic, data=msg.encode("utf-8"),
                headers={"Title": "timely", "Tags": "stopwatch"})
            urllib.request.urlopen(req, timeout=10)
            print("(pushed to phone via ntfy)")
    except FileNotFoundError:
        pass
    except Exception as e:
        print("(phone push failed: %s)" % e)


def cmd_notify(weekly: bool = False):
    """One-shot notification: daily briefing, or --weekly for week-in-review."""
    days_to_race = (RACE_DATE - date.today()).days

    if weekly:
        rev = build_week_review()
        if rev:
            msg = ("Week %d: %.1f of %.0f mi · %d/%d runs · on target %d×"
                   % (rev["week"], rev["mi"], rev["planned"] or 0,
                      rev["runs"], rev["plannedRuns"], rev["onTarget"]))
            if rev.get("vdot"):
                msg += " · VDOT %.1f" % rev["vdot"]
            msg += " — " + rev["line"]
            try:
                fit = fetch_fitness()
                if fit.get("marathon"):
                    msg += " · %d days to MCM, projecting %s" % (days_to_race, fit["marathon"])
            except Exception:
                pass
            try:
                prs = fetch_prs()
                wk_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
                pr_this_week = [k for k, v in prs.items() if (v.get("date") or "") >= wk_start]
                if pr_this_week:
                    labels = {"mile": "mile PR", "5k": "5K PR", "10k": "10K PR",
                              "half": "half PR", "long": "longest run"}
                    msg += " · 🏆 " + ", ".join(labels.get(k, k) for k in pr_this_week)
            except Exception:
                pass
        else:
            msg = "Tune-up phase — %d days to MCM." % days_to_race
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
            r = todays[0]
            pace_str = ("%d:%02d/mi" % (r["paceSec"] // 60, r["paceSec"] % 60)
                        if r.get("paceSec") else "")
            msg = "Run synced: %.1f mi%s. Log how it felt in timely." % (
                r["mi"], " @ " + pace_str if pace_str else "")
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
    msg += " · %d days to MCM" % days_to_race
    try:
        fit = fetch_fitness()
        if fit.get("marathon"):
            msg += ", projecting %s" % fit["marathon"]
    except Exception:
        pass
    _push(msg)


# G10: both background threads below used to be `try: ... except Exception:
# pass` -- resilient to a single bad iteration (a network blip shouldn't
# kill the loop), but a *persistent* failure (Garmin/intervals.icu auth
# broken, disk full, backup dir unmounted) would then loop forever with
# zero signal. _run_resilient_loop keeps the "don't die on one bad tick"
# behavior but adds: every failure logged with a full traceback (rotating
# file, see applog.py); the first failure in a streak triggers a phone
# push, further pushes for the same streak rate-limited to once/hour so a
# real outage alerts promptly without spamming; a push when it recovers;
# and a once-a-day "still alive" heartbeat written to the log (not pushed
# -- the user already gets a 7:30 daily briefing push, a second daily push
# that says nothing actionable would just train them to ignore pushes).
_ALERT_COOLDOWN_SEC = 3600
_last_heartbeat_date: dict = {}
_last_alert_at: dict = {}


def _run_resilient_loop(name: str, interval_sec: int, tick) -> None:
    failing_since = None
    while True:
        today = date.today()
        if _last_heartbeat_date.get(name) != today:
            log.info("%s: heartbeat -- thread alive", name)
            _last_heartbeat_date[name] = today
        try:
            tick()
            if failing_since is not None:
                log.info("%s: recovered (had been failing since %s)", name, failing_since)
                _push("timely: %s recovered" % name)
                failing_since = None
        except Exception:
            log.exception("%s: iteration failed", name)
            now = time.time()
            if failing_since is None:
                failing_since = today.isoformat()
            if now - _last_alert_at.get(name, 0) >= _ALERT_COOLDOWN_SEC:
                _push("timely: %s has been failing since %s -- check "
                      "~/Library/Logs/timely.log" % (name, failing_since))
                _last_alert_at[name] = now
        time.sleep(interval_sec)


def run_watcher():
    """Every 10 min: sync activities; push when a new run lands."""
    def _tick():
        before = {str(r.get("activityId")) for r in store.get_runs()}
        recent = (date.today() - timedelta(days=2)).isoformat()
        for r in fetch_actuals()["runs"]:
            aid = str(r.get("activityId"))
            if aid and aid not in before and r["date"] >= recent:
                _push("Run synced: %.1f mi @ %s/mi — everything worked. "
                      "Log how it felt in timely." % (r["mi"], r["pace"] or "—"))
    _run_resilient_loop("run_watcher", 600, _tick)


def backup_loop(backup_dir: str):
    """Every 5 min: snapshot the live DB to a backup file."""
    def _tick():
        store.backup(backup_dir)
    _run_resilient_loop("backup_loop", 300, _tick)
