"""Google Calendar sync — pushes the Garmin training schedule to a dedicated
'MCM Marathon Plan' calendar via the Calendar API.

Push-based by design: this process makes outbound calls to Google, so the
LAN-only/Tailscale-only server never needs to be publicly reachable.

One-time setup (interactive, run by the user in a terminal with a browser):
    python3 setup_gcal.py
That writes ~/.gcal_token.json. Everything in this module after that just
loads and silently refreshes that token — no browser interaction needed from
the background service.
"""
from __future__ import annotations
import hashlib
import os
import re
import threading
from datetime import date, datetime, timedelta
from typing import Optional

import store
from src.services.schedule import fetch_schedule, _is_hard
from builders import MILE

TOKEN_PATH = os.path.expanduser("~/.gcal_token.json")
CALENDAR_NAME = "MCM Marathon Plan"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

_lock = threading.Lock()
_service_cache = None
_plan_by_name_cache = None

_TITLE_PREFIX_RE = re.compile(r"^W\d+ \w+ ")
_MI_IN_TITLE_RE = re.compile(r"\d+(\.\d+)?\s*mi", re.I)


def _plan_by_name() -> dict:
    """name -> plan entry (payload + distance_mi), built once per process.
    Lets us recover the structured Garmin step data for a scheduled item by
    its title, even though fetch_schedule() only returns the bare name."""
    global _plan_by_name_cache
    if _plan_by_name_cache is None:
        from plan import build_plan
        _plan_by_name_cache = {p["name"]: p for p in build_plan()}
    return _plan_by_name_cache


def _fmt_pace(mps: float) -> str:
    s = int(round(MILE / mps))
    return "%d:%02d" % (s // 60, s % 60)


def _fmt_dist(meters: float) -> str:
    miles = meters / MILE
    if miles >= 0.15:
        s = ("%.2f" % miles).rstrip("0").rstrip(".")
        return "%smi" % s
    return "%dm" % int(round(meters))


def _fmt_secs(secs: float) -> str:
    secs = int(round(secs))
    return "%dmin" % round(secs / 60) if secs >= 60 else "%ds" % secs


def _step_line(s: dict) -> str:
    cond = s["endCondition"]["conditionTypeKey"]
    amt = (_fmt_dist(s["endConditionValue"]) if cond == "distance"
           else _fmt_secs(s["endConditionValue"]))
    t = s.get("targetType") or {}
    if t.get("workoutTargetTypeKey") == "pace.zone":
        pace = "%s–%s/mi" % (_fmt_pace(s["targetValueTwo"]),
                                  _fmt_pace(s["targetValueOne"]))
        return "%s at %s" % (amt, pace)
    desc = (s.get("description") or "").split("—")[-1].strip()
    return "%s %s" % (amt, desc.lower() if desc else "easy")


def _fmt_secs_clock(secs) -> str:
    secs = int(round(secs or 0))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def _fmt_pace_sec(secs) -> Optional[str]:
    if not secs:
        return None
    secs = int(round(secs))
    return "%d:%02d" % (secs // 60, secs % 60)


def _describe_steps(steps: list) -> list:
    """Runna-style description: plain lines for sequential steps, a dashed
    "Repeat the following Nx:" block for repeat groups (matches Runna's own
    formatting for interval workouts)."""
    lines = []
    for s in steps:
        if s.get("type") == "RepeatGroupDTO":
            n = s["numberOfIterations"]
            lines.append("Repeat the following %dx:" % n)
            lines.append("----------")
            for c in s["workoutSteps"]:
                lines.append(_step_line(c))
            lines.append("----------")
        else:
            lines.append(_step_line(s))
    return lines


def _display_title(raw_title: str, distance_mi: Optional[float]) -> str:
    short = _TITLE_PREFIX_RE.sub("", raw_title)
    if distance_mi and not _MI_IN_TITLE_RE.search(short):
        return "\U0001F3C3 %s • %.1fmi" % (short, distance_mi)
    return "\U0001F3C3 %s" % short


def _credentials():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not os.path.exists(TOKEN_PATH):
        raise RuntimeError(
            "No Google Calendar token found. Run `python3 setup_gcal.py` "
            "once (it opens a browser for one-time consent), then retry.")
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _service():
    global _service_cache
    with _lock:
        if _service_cache is None:
            from googleapiclient.discovery import build
            _service_cache = build("calendar", "v3", credentials=_credentials(),
                                    cache_discovery=False)
        return _service_cache


def _get_or_create_calendar_id() -> str:
    cached, _ = store.get_kv("gcal_calendar_id")
    if cached:
        return cached
    svc = _service()
    page_token = None
    while True:
        resp = svc.calendarList().list(pageToken=page_token).execute()
        for cal in resp.get("items", []):
            if cal.get("summary") == CALENDAR_NAME:
                store.set_kv("gcal_calendar_id", cal["id"])
                return cal["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    created = svc.calendars().insert(body={
        "summary": CALENDAR_NAME,
        "description": "Synced automatically from timely — do not edit times here, "
                        "edit the plan in the coach app instead.",
        "timeZone": "America/New_York",
    }).execute()
    cal_id = created["id"]
    store.set_kv("gcal_calendar_id", cal_id)
    return cal_id


def _color_for(title: str) -> str:
    # Google event colorId palette: 11=tomato (hard), 2=sage (easy)
    return "11" if _is_hard(title) else "2"


def _planned_body(item: dict, plan: Optional[dict]) -> dict:
    d = date.fromisoformat(item["date"])
    desc_lines = []
    if plan:
        steps = plan["payload"]["workoutSegments"][0]["workoutSteps"]
        desc_lines.extend(_describe_steps(steps))
    desc_lines.append("")
    desc_lines.append("Synced from timely — edit the plan in the coach app, not here.")
    return {
        "summary": _display_title(item["title"], plan["distance_mi"] if plan else None),
        "description": "\n".join(desc_lines),
        "start": {"date": d.isoformat()},
        "end": {"date": (d + timedelta(days=1)).isoformat()},
        "colorId": _color_for(item["title"]),
        "extendedProperties": {"private": {"timelyScheduleId": str(item["scheduleId"])}},
    }


def _parse_local_dt(ts: Optional[str]) -> Optional[datetime]:
    """Parses both intervals.icu ('2026-06-02T07:33:00') and Garmin
    ('2026-06-02 07:33:00') local-time formats. Returns None on anything
    unparseable so callers can fall back to the planned all-day event."""
    if not ts:
        return None
    s = ts.replace("T", " ").strip()[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _runs_by_date(runs: list) -> dict:
    """date -> the best (largest-mi) run that day, mirroring the convention
    app.js's assess() already uses for multi-run days."""
    by_date = {}
    for r in runs:
        d = r.get("date")
        if not d:
            continue
        cur = by_date.get(d)
        if cur is None or (r.get("mi") or 0) > (cur.get("mi") or 0):
            by_date[d] = r
    return by_date


def _laps_for(run: dict) -> list:
    try:
        from src.services.actuals import fetch_run_detail
        aid = run.get("activityId")
        if not aid:
            return []
        detail = fetch_run_detail(aid)
        return detail.get("laps") or []
    except Exception:
        return []


def _completed_body(item: dict, run: dict, plan: Optional[dict]) -> Optional[dict]:
    """Runna-style completed-run event: timed start/end + Summary/Description
    /Laps. Returns None (caller falls back to the planned body) if there's no
    usable clock time."""
    start_dt = _parse_local_dt(run.get("startLocal"))
    if start_dt is None:
        return None
    dur = run.get("durationSec") or 0
    end_dt = start_dt + timedelta(seconds=dur)

    desc_lines = ["\U0001F4CA Summary"]
    mi = run.get("mi")
    desc_lines.append("Distance: %.2fmi" % mi if mi else "Distance: —")
    desc_lines.append("Time: %s" % _fmt_secs_clock(dur))
    desc_lines.append("Avg Pace: %s/mi" % run["pace"] if run.get("pace") else "Avg Pace: —")
    desc_lines.append("")
    desc_lines.append("\U0001F4CB Description")
    if plan:
        steps = plan["payload"]["workoutSegments"][0]["workoutSteps"]
        desc_lines.extend(_describe_steps(steps))
    laps = _laps_for(run)
    if laps:
        desc_lines.append("")
        desc_lines.append("♻️ Laps")
        for lap in laps:
            p = _fmt_pace_sec(lap.get("paceSec"))
            desc_lines.append("%.2f mi%s" % (lap.get("mi") or 0,
                                              " @ %s/mi" % p if p else ""))
    desc_lines.append("")
    desc_lines.append("Synced from timely — edit the plan in the coach app, not here.")

    tz = {"timeZone": "America/New_York"}
    return {
        "summary": _display_title(item["title"], plan["distance_mi"] if plan else None),
        "description": "\n".join(desc_lines),
        "start": dict({"dateTime": start_dt.isoformat()}, **tz),
        "end": dict({"dateTime": end_dt.isoformat()}, **tz),
        "colorId": _color_for(item["title"]),
        "extendedProperties": {"private": {"timelyScheduleId": str(item["scheduleId"])}},
    }


def _event_body(item: dict, runs_by_date: Optional[dict] = None) -> dict:
    """Picks the completed (timed, Summary/Description/Laps) rendering if a
    matching actual run exists for this date, else the planned all-day one."""
    plan = _plan_by_name().get(item["title"])
    run = (runs_by_date or {}).get(item["date"])
    if run:
        body = _completed_body(item, run, plan)
        if body is not None:
            return body
    return _planned_body(item, plan)


def _fingerprint(item: dict, runs_by_date: Optional[dict] = None) -> str:
    # Hash the rendered event body (not just date+title) so a format change
    # to _event_body (like this one) naturally re-syncs existing events
    # instead of silently leaving them in the old style. Also means an event
    # auto-upgrades from planned -> completed style as soon as the run syncs.
    body = _event_body(item, runs_by_date)
    start = body["start"].get("date") or body["start"].get("dateTime")
    raw = "%s|%s|%s" % (start, body["summary"], body["description"])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def sync_schedule(force: bool = False) -> dict:
    """Diff the Garmin schedule against the last-known Calendar mapping and
    push only what changed. Safe to call often — unchanged items are a
    single fingerprint comparison, no API call."""
    items = fetch_schedule(force=force)
    svc = _service()
    cal_id = _get_or_create_calendar_id()
    existing = store.get_gcal_map()

    try:
        from src.services.actuals import fetch_actuals
        runs_by_date = _runs_by_date(fetch_actuals().get("runs") or [])
    except Exception:
        runs_by_date = {}

    created = updated = deleted = unchanged = 0
    seen = set()

    for item in items:
        sid = str(item["scheduleId"])
        seen.add(sid)
        fp = _fingerprint(item, runs_by_date)
        prior = existing.get(sid)
        if prior and prior["fingerprint"] == fp:
            unchanged += 1
            continue
        body = _event_body(item, runs_by_date)
        if prior:
            try:
                svc.events().update(calendarId=cal_id, eventId=prior["eventId"],
                                     body=body).execute()
                updated += 1
            except Exception:
                # Event may have been deleted on the Google side; recreate it.
                ev = svc.events().insert(calendarId=cal_id, body=body).execute()
                prior = {"eventId": ev["id"]}
                created += 1
        else:
            ev = svc.events().insert(calendarId=cal_id, body=body).execute()
            prior = {"eventId": ev["id"]}
            created += 1
        store.set_gcal_event(sid, prior["eventId"], fp)

    for sid, info in existing.items():
        if sid not in seen:
            try:
                svc.events().delete(calendarId=cal_id, eventId=info["eventId"]).execute()
            except Exception:
                pass
            store.delete_gcal_event(sid)
            deleted += 1

    return {"created": created, "updated": updated, "deleted": deleted,
            "unchanged": unchanged, "total": len(items)}


def sync_in_background():
    """Fire-and-forget sync for use after schedule-mutating actions."""
    def _run():
        try:
            sync_schedule()
        except Exception as e:
            print("gcal sync failed:", e)
    threading.Thread(target=_run, daemon=True).start()
