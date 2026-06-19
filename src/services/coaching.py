"""Dynamic coaching engine — rule-based plan adaptation proposals."""
import re
from datetime import date, timedelta

import store
from src.services.schedule import fetch_schedule, next_clean_slot, _is_hard


def adapt_training_block() -> list:
    """Return up to 2 move/skip proposals. Nothing mutates until applied.

    Rule 1 — fatigue: RPE > 8 on an easy run in the last 2 days →
        push the next quality session to a clean slot.
    Rule 2 — adjacency: two hard days back-to-back →
        move the second to a clean slot.
    """
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
            continue
        for k in range(0, 3):
            ds = (today + timedelta(days=k)).isoformat()
            s = by.get(ds)
            if s and _is_hard(s["title"]):
                to = next_clean_slot(sched, s["scheduleId"],
                                     date.fromisoformat(ds),
                                     prefer_weekend=("LR" in s["title"]
                                                     or "MP" in s["title"]))
                props.append({
                    "reason": "RPE %d on an easy run — fatigue flag. "
                              "Give the next quality session room." % a["rpe"],
                    "title": s["title"], "scheduleId": s["scheduleId"],
                    "workoutId": s["workoutId"], "date": s["date"],
                    "action": "move" if to else "skip", "to": to,
                })
                break
        break

    for k in range(0, 7):
        d1 = (today + timedelta(days=k)).isoformat()
        d2 = (today + timedelta(days=k + 1)).isoformat()
        s1, s2 = by.get(d1), by.get(d2)
        if s1 and s2 and _is_hard(s1["title"]) and _is_hard(s2["title"]):
            if any(p["scheduleId"] == s2["scheduleId"] for p in props):
                break
            to = next_clean_slot(sched, s2["scheduleId"], date.fromisoformat(d2))
            props.append({
                "reason": "Back-to-back hard days — hard work needs easy days "
                          "between it to become fitness.",
                "title": s2["title"], "scheduleId": s2["scheduleId"],
                "workoutId": s2["workoutId"], "date": s2["date"],
                "action": "move" if to else "skip", "to": to,
            })
            break

    return props[:2]
