"""Prototype: generate intervals.icu calendar-event payloads for a sample
of workouts from plan.py, via builders_intervalsicu.py.

Read-only against the real plan; no network calls, no intervals.icu
credentials needed. For each sample workout, writes <slug>.json containing
the event dict exactly as it would appear in the array POSTed to:

    POST https://intervals.icu/api/v1/athlete/0/events/bulk?upsert=true

and prints the human-readable workout text plus a validation status.

Usage:
    python3 make_intervalsicu_workouts.py [out_dir]   # default: intervalsicu_prototype
"""
import json
import os
import sys

from plan import PLAN, workout_date
from builders_intervalsicu import build_event, build_bulk_payload, validate_event

# Same representative sample as make_suunto_guides.py: easy run (NO_TARGET),
# strides, hills (two repeat blocks), tempo, intervals (repeat + pace
# target), MP-finish long run, plain long run.
SAMPLES = {
    (1, "Mon"),   # easy_run(4)
    (1, "Tue"),   # easy_strides(5)
    (3, "Tue"),   # hill_repeats_short(8)
    (3, "Thu"),   # tempo_run(5, 2)
    (4, "Tue"),   # interval_workout(6, 800, 400)
    (7, "Sat"),   # long_run_mp_finish(16, 4)
    (11, "Sat"),  # long_run_easy(20)
}


def main(out_dir="intervalsicu_prototype"):
    os.makedirs(out_dir, exist_ok=True)
    events = []
    print("%-30s %s" % ("workout", "validation"))
    for week, day, wk in PLAN:
        if (week, day) not in SAMPLES:
            continue
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
        print("%-30s %s" % (name, status))
        print("  " + event["description"].replace("\n", "\n  "))
        print()

    bulk = build_bulk_payload(events)
    with open(os.path.join(out_dir, "bulk_events.json"), "w") as f:
        json.dump(bulk, f, indent=2)
    print("Wrote %d events -> %s/bulk_events.json (POST body for "
          "/api/v1/athlete/0/events/bulk?upsert=true)" % (len(events), out_dir))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "intervalsicu_prototype")
