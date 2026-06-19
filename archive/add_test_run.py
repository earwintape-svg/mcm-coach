#!/usr/bin/env python3
"""One-off: schedule a 3mi easy test run (no pace goal) for tomorrow.

Run:    python3 add_test_run.py
Undo:   python3 add_test_run.py --remove
"""
import sys
from datetime import date, timedelta

from builders import easy_run, make_workout
from upload_garmin_workouts import get_client, api

NAME = "W1 Fri 3mi Test"   # matches the app's plan-name filter so it shows up


def main():
    c = get_client()
    if "--remove" in sys.argv:
        for w in api(c, "/workout-service/workouts?start=0&limit=999") or []:
            if w.get("workoutName") == NAME:
                api(c, "/workout-service/workout/%s" % w["workoutId"], method="DELETE")
                print("Removed", NAME)
                return
        print("Nothing to remove.")
        return
    wk = easy_run(3, label="Test")
    payload = make_workout(NAME, wk.steps)
    res = api(c, "/workout-service/workout", method="POST", payload=payload) or {}
    wid = res.get("workoutId")
    day = (date.today() + timedelta(days=1)).isoformat()
    api(c, "/workout-service/schedule/%s" % wid, method="POST", payload={"date": day})
    print("Scheduled %r for %s (id=%s)." % (NAME, day, wid))
    print("Sync your watch via the Connect app, then hit refresh in timely.")


if __name__ == "__main__":
    main()
