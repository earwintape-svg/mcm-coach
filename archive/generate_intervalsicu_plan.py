"""Generate intervals.icu calendar-event payloads for the FULL training plan.

Walks every entry in plan.PLAN (93 workouts) via builders_intervalsicu.py and
writes:
  - one <slug>.json per workout (the event dict for that day), and
  - bulk_events.json, the full array -- the POST body for:

        POST https://intervals.icu/api/v1/athlete/{id}/events/bulk?upsert=true

Read-only against plan.py: no network calls, no credentials needed. Run
upload_intervalsicu.py separately to actually send bulk_events.json.

Usage:
    python3 generate_intervalsicu_plan.py [out_dir]   # default: intervalsicu_plan
"""
import json
import os
import sys

from plan import PLAN, workout_date
from builders_intervalsicu import build_event, build_bulk_payload, validate_event


def main(out_dir="intervalsicu_plan"):
    os.makedirs(out_dir, exist_ok=True)
    events = []
    any_issues = False

    print("%-30s %s" % ("workout", "validation"))
    for week, day, wk in PLAN:
        name = "W%d %s %s" % (week, day, wk.suffix)
        d = workout_date(week, day)
        ext_id = "timely-w%d-%s" % (week, day.lower())

        event = build_event(name, wk.steps, start_date_local=d, external_id=ext_id)
        problems = validate_event(event)
        events.append(event)

        slug = name.replace(" ", "_")
        with open(os.path.join(out_dir, slug + ".json"), "w") as f:
            json.dump(event, f, indent=2)

        status = "OK" if not problems else "ISSUES: " + "; ".join(problems)
        if problems:
            any_issues = True
        print("%-30s %s" % (name, status))

    bulk = build_bulk_payload(events)
    with open(os.path.join(out_dir, "bulk_events.json"), "w") as f:
        json.dump(bulk, f, indent=2)

    print()
    print("Wrote %d events -> %s/bulk_events.json" % (len(events), out_dir))
    print("(POST body for /api/v1/athlete/{id}/events/bulk?upsert=true)")
    if any_issues:
        print("WARNING: some workouts had validation issues -- see above.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "intervalsicu_plan")
