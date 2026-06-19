"""FastAPI router — all /api/* endpoints and static file routes.

Each handler is a thin adapter: validate input, call the appropriate service,
return JSON. No business logic lives here.
"""
from __future__ import annotations
import hmac
import os
import re
import struct
import zlib
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from src.services.plan_svc import plan_summary
from src.services.schedule import (fetch_schedule, move_workout,
                                   unschedule_workout, shift_range,
                                   next_clean_slot)
from src.services.actuals import fetch_actuals, fetch_run_detail, fetch_other_activities
from src.services.wellness import fetch_wellness, fetch_fitness_form
from src.services.fitness import fetch_fitness, fetch_prs
from src.services.weather import fetch_weather
from src.services.trends import fetch_trends, build_week_review
from src.services.coaching import adapt_training_block
from src.services import gcal
import store

router = APIRouter()

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
_ACCESS_KEY: Optional[str] = None


def set_access_key(key: Optional[str]):
    global _ACCESS_KEY
    _ACCESS_KEY = key


def _verify(request: Request,
            x_key: Optional[str] = Header(None, alias="X-Key"),
            key: Optional[str] = Query(None)):
    ip = request.client.host if request.client else "127.0.0.1"
    if _ACCESS_KEY is None or ip in ("127.0.0.1", "::1"):
        return
    given = x_key or key or ""
    if not hmac.compare_digest(given, _ACCESS_KEY):
        raise HTTPException(status_code=403, detail="unauthorized")


_auth = Depends(_verify)

# ---------------------------------------------------------------------------
# GET routes
# ---------------------------------------------------------------------------

@router.get("/api/data")
def api_data(refresh: str = "", _=_auth):
    return {"plan": plan_summary(), "schedule": fetch_schedule(force=refresh == "1")}


@router.get("/api/actuals")
def api_actuals(_=_auth):
    return fetch_actuals()


@router.get("/api/wellness")
def api_wellness(refresh: str = "", _=_auth):
    return fetch_wellness(force=refresh == "1")


@router.get("/api/weather")
def api_weather(_=_auth):
    return fetch_weather()


@router.get("/api/fitness_form")
def api_fitness_form(refresh: str = "", _=_auth):
    return fetch_fitness_form(force=refresh == "1")


@router.get("/api/fitness")
def api_fitness(_=_auth):
    return fetch_fitness()


@router.get("/api/gear")
def api_gear(_=_auth):
    return {"gear": store.gear_summary()}


@router.get("/api/coach")
def api_coach(_=_auth):
    return {"proposals": adapt_training_block()}


@router.get("/api/trends")
def api_trends(_=_auth):
    return fetch_trends()


@router.get("/api/prs")
def api_prs(_=_auth):
    return fetch_prs()


@router.get("/api/suggest_move")
def api_suggest_move(scheduleId: int, fromDate: str, _=_auth):
    sched = fetch_schedule()
    to = next_clean_slot(sched, scheduleId, date.fromisoformat(fromDate))
    to_label = datetime.fromisoformat(to).strftime("%A, %b %-d") if to else None
    return {"to": to, "toLabel": to_label}


@router.get("/api/other_activities")
def api_other_activities(refresh: str = "", _=_auth):
    return {"activities": fetch_other_activities(force=refresh == "1")}


@router.get("/api/review")
def api_review(_=_auth):
    today = date.today()
    wk = (today - __import__("plan").PLAN_START).days // 7 + 1
    rev = (store.get_review(wk) or build_week_review(wk)) \
        if today.weekday() in (6, 0) else None
    return {"review": rev}


@router.get("/api/calendar_status")
def api_calendar_status(_=_auth):
    return {"connected": os.path.exists(gcal.TOKEN_PATH)}


@router.get("/api/run/{activity_id}")
def api_run(activity_id: str, _=_auth):
    if activity_id.isdigit():
        aid = int(activity_id)
    elif re.fullmatch(r"i\d+", activity_id):
        aid = activity_id
    else:
        raise HTTPException(status_code=400, detail="bad activity id")
    return fetch_run_detail(aid)


# ---------------------------------------------------------------------------
# POST routes
# ---------------------------------------------------------------------------

@router.post("/api/move")
def api_move(body: dict, _=_auth):
    move_workout(int(body["scheduleId"]), int(body["workoutId"]), str(body["date"]))
    return {"ok": True}


@router.post("/api/shift_range")
def api_shift_range(body: dict, _=_auth):
    days = int(body["days"])
    if abs(days) > 90:
        raise HTTPException(status_code=400, detail="shift limited to ±90 days")
    return {"ok": True, "moved": shift_range(str(body["from"]), str(body["to"]), days)}


@router.post("/api/unschedule")
def api_unschedule(body: dict, _=_auth):
    unschedule_workout(int(body["scheduleId"]))
    return {"ok": True}


@router.post("/api/import")
def api_import(body: dict, _=_auth):
    date.fromisoformat(str(body["date"]))
    store.save_external(str(body["source"]), str(body["date"]), body.get("metrics") or {})
    return {"ok": True}


@router.post("/api/gear")
def api_gear_post(body: dict, _=_auth):
    store.set_gear(str(body["key"]), display=body.get("display"),
                   start_mi=body.get("startMi"), threshold_mi=body.get("thresholdMi"),
                   retired=body.get("retired"), brand=body.get("brand"),
                   model=body.get("model"), is_default=body.get("isDefault"))
    return {"ok": True}


@router.post("/api/run/{activity_id}/gear")
def api_run_gear(activity_id: str, body: dict, _=_auth):
    store.set_annotation(activity_id[:32], shoes=str(body.get("gearId") or ""))
    return {"ok": True}


@router.post("/api/coach/apply")
def api_coach_apply(body: dict, _=_auth):
    if body.get("action") == "move":
        move_workout(int(body["scheduleId"]), int(body["workoutId"]), str(body["to"]))
    else:
        unschedule_workout(int(body["scheduleId"]))
    return {"ok": True}


@router.post("/api/sync_calendar")
def api_sync_calendar(_=_auth):
    try:
        result = gcal.sync_schedule()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/annotate")
def api_annotate(body: dict, _=_auth):
    store.set_annotation(str(body["activityId"])[:32],
                         rpe=body.get("rpe"), note=body.get("note"),
                         shoes=body.get("shoes"))
    return {"ok": True}
