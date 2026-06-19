"""Wellness and fitness-form service — health signals from intervals.icu / Garmin."""
from datetime import date, timedelta

import store
import intervalsicu_read
from src.services import cache
from src.services.garmin import client
from upload_garmin_workouts import api


def fetch_wellness(force: bool = False) -> dict:
    """Last 14 days of health signals. intervals.icu first, Garmin fallback."""
    if not force and cache.get("well") is not None and cache.fresh("well_ts", 1800):
        return cache.get("well")
    out: dict = {"days": []}
    try:
        days = intervalsicu_read.fetch_wellness_days(14)
        if not days:
            raise RuntimeError("no intervals.icu wellness data")
        out["days"] = days
    except Exception:
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
                    "bb": s.get("bodyBatteryMostRecentValue") or s.get("bodyBatteryHighestValue"),
                })
        except Exception as e:
            cached = store.get_wellness()
            out = {"days": cached, "stale": True} if cached else {"error": str(e)}
    if out.get("days") and not out.get("stale"):
        store.upsert_wellness(out["days"])
    cache.set("well", out, "well_ts")
    return out


def fetch_fitness_form(force: bool = False) -> dict:
    """CTL/ATL/form, HRV readiness, VO2max trend from intervals.icu."""
    if not force and cache.get("fitform") is not None and cache.fresh("fitform_ts", 1800):
        return cache.get("fitform")
    out: dict = {}
    try:
        days = intervalsicu_read.fetch_wellness_days(60)
        chrono = list(reversed(days))
        ctl_atl = [{"d": d["date"], "ctl": d["ctl"], "atl": d["atl"], "form": d["form"]}
                   for d in chrono if d.get("ctl") is not None and d.get("atl") is not None]
        hrv = [{"d": d["date"], "v": d["hrv"]} for d in chrono if d.get("hrv") is not None]
        vo2max = [{"d": d["date"], "v": d["vo2max"]} for d in chrono if d.get("vo2max") is not None]
        if ctl_atl:
            out["ctlAtl"] = ctl_atl
        if hrv:
            out["hrv"] = hrv
            if len(hrv) >= 14:
                recent = [d["v"] for d in hrv[-7:]]
                base = [d["v"] for d in hrv[:-7]]
                out["hrvReadiness"] = {
                    "recent": round(sum(recent) / len(recent), 1),
                    "baseline": round(sum(base) / len(base), 1),
                }
        if vo2max:
            out["vo2max"] = vo2max
        if days:
            today = days[0]
            if today.get("readiness") is not None:
                out["readiness"] = today["readiness"]
            if today.get("rampRate") is not None:
                out["rampRate"] = today["rampRate"]
            if today.get("form") is not None:
                out["formToday"] = today["form"]
    except Exception:
        pass
    cache.set("fitform", out, "fitform_ts")
    return out
