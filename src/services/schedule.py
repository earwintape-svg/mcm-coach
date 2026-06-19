"""Schedule service — fetch, move, and cache the Garmin workout calendar."""
from __future__ import annotations
import threading
import time
from datetime import date, timedelta
from typing import Optional

import store
from plan import PLAN_START
from src.services import cache
from src.services.garmin import client
from upload_garmin_workouts import api, is_plan_name


def _refresh_schedule() -> list:
    """Slow path: ~9 Garmin calendar calls. Persisted so the app serves
    instantly from the store and refreshes in the background."""
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
    cache.set("sched", out, "ts")
    return out


def fetch_schedule(force: bool = False) -> list:
    if force:
        return _refresh_schedule()
    if cache.get("sched") is not None and cache.fresh("ts", 60):
        return cache.get("sched")
    cached, age = store.get_kv("schedule")
    if cached is not None:
        cache.set("sched", cached, "ts")
        if age > 600:
            threading.Thread(target=_refresh_schedule, daemon=True).start()
        return cached
    return _refresh_schedule()


def move_workout(schedule_id: int, workout_id: int, new_date: str):
    date.fromisoformat(new_date)  # validate
    c = client()
    try:
        api(c, "/workout-service/schedule/%s" % schedule_id, method="DELETE")
    except Exception:
        pass
    api(c, "/workout-service/schedule/%s" % workout_id, method="POST",
        payload={"date": new_date})
    store.log_event("move", workout_id, new_date)
    cache.set("sched", None)


def unschedule_workout(schedule_id: int):
    api(client(), "/workout-service/schedule/%d" % schedule_id, method="DELETE")
    store.log_event("skip", schedule_id)
    cache.set("sched", None)


def shift_range(date_from: str, date_to: str, days: int) -> int:
    d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    moved = 0
    for it in list(fetch_schedule(force=True)):
        d = date.fromisoformat(it["date"])
        if d1 <= d <= d2:
            move_workout(it["scheduleId"], it["workoutId"],
                         (d + timedelta(days=days)).isoformat())
            moved += 1
    cache.set("sched", None)
    return moved


def next_clean_slot(sched: list, exclude_id: int, from_day: date,
                    prefer_weekend: bool = False) -> Optional[str]:
    """First open day after from_day with no adjacent hard workout."""
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


import re as _re
_HARD_RE = _re.compile(r"Tempo|Hill|\dx|MP Finish|mi LR")


def _is_hard(title: str) -> bool:
    return bool(_HARD_RE.search(title or ""))
