"""MCM 2026 training plan — declarative, one line per workout.

Edit THIS file to change the plan; the uploader and tests consume it.
Plan starts Monday 2026-06-15. Race: Marine Corps Marathon, Sun 2026-10-25.

Reconstructed easy-run distances target each week's mileage from the spec
(within ~10%). Quality workouts include 1.5mi WU + 1.5mi CD.
"""
from datetime import date, timedelta

from builders import (
    easy_run, easy_strides, interval_workout, mile_repeats, tempo_run,
    long_run_easy, long_run_mp_finish, hill_repeats_short, hill_repeats_long,
    hill_mixed, hill_tempo, make_workout, total_distance_m, MILE,
)

PLAN_START = date(2026, 6, 15)          # must be a Monday
RACE_DATE = date(2026, 10, 25)
DAY_OFFSET = {"Mon": 0, "Tue": 1, "Thu": 3, "Fri": 4, "Sat": 5}

# Target weekly mileage from the spec (week -> miles); week 19 is race week.
WEEKLY_TARGET_MI = {1: 26, 2: 29, 3: 33, 4: 35, 5: 36, 6: 30, 7: 40, 8: 42,
                    9: 32, 10: 47, 11: 50, 12: 42, 13: 51, 14: 48, 15: 38,
                    16: 31, 17: 26, 18: 19}

# (week, day, Workout) — 93 entries.
PLAN = [
    # Week 1 — 26 mi
    (1, "Mon", easy_run(4)),
    (1, "Tue", easy_strides(5)),
    (1, "Thu", easy_run(4)),
    (1, "Fri", easy_run(5)),
    (1, "Sat", long_run_easy(8)),
    # Week 2 — 29 mi
    (2, "Mon", easy_run(4)),
    (2, "Tue", easy_strides(5)),
    (2, "Thu", easy_run(5)),
    (2, "Fri", easy_run(6)),
    (2, "Sat", long_run_easy(9)),
    # Week 3 — 33 mi
    (3, "Mon", easy_run(5)),
    (3, "Tue", hill_repeats_short(8)),
    (3, "Thu", tempo_run(5, 2)),
    (3, "Fri", easy_run(6)),
    (3, "Sat", long_run_easy(10)),
    # Week 4 — 35 mi
    (4, "Mon", easy_run(5)),
    (4, "Tue", interval_workout(6, 800, 400)),
    (4, "Thu", tempo_run(6, 3)),
    (4, "Fri", easy_run(6)),
    (4, "Sat", long_run_easy(11)),
    # Week 5 — 36 mi
    (5, "Mon", easy_run(5)),
    (5, "Tue", hill_mixed()),
    (5, "Thu", tempo_run(6, 3)),
    (5, "Fri", easy_run(6)),
    (5, "Sat", long_run_easy(12)),
    # Week 6 — 30 mi (recovery)
    (6, "Mon", easy_run(4)),
    (6, "Tue", interval_workout(4, 1000, 400)),
    (6, "Thu", tempo_run(6, 3)),
    (6, "Fri", easy_run(4)),
    (6, "Sat", long_run_easy(10)),
    # Week 7 — 40 mi
    (7, "Mon", easy_run(5)),
    (7, "Tue", hill_repeats_long(5)),
    (7, "Thu", tempo_run(7, 4)),
    (7, "Fri", easy_run(5)),
    (7, "Sat", long_run_mp_finish(16, 4)),
    # Week 8 — 42 mi
    (8, "Mon", easy_run(5)),
    (8, "Tue", interval_workout(4, 1200, 400)),
    (8, "Thu", tempo_run(8, 5)),
    (8, "Fri", easy_run(5)),
    (8, "Sat", long_run_mp_finish(17, 4)),
    # Week 9 — 32 mi (recovery)
    (9, "Mon", easy_run(4)),
    (9, "Tue", interval_workout(6, 800, 400)),
    (9, "Thu", tempo_run(6, 3)),
    (9, "Fri", easy_run(4)),
    (9, "Sat", long_run_easy(10)),
    # Week 10 — 47 mi
    (10, "Mon", easy_run(6)),
    (10, "Tue", hill_repeats_long(5)),
    (10, "Thu", tempo_run(9, 6)),
    (10, "Fri", easy_run(7)),
    (10, "Sat", long_run_mp_finish(18, 5)),
    # Week 11 — 50 mi (peak)
    (11, "Mon", easy_run(5)),
    (11, "Tue", mile_repeats(5)),
    (11, "Thu", tempo_run(9, 6)),
    (11, "Fri", easy_run(6)),
    (11, "Sat", long_run_easy(20)),
    # Week 12 — 42 mi
    (12, "Mon", easy_run(6)),
    (12, "Tue", hill_repeats_short(8)),
    (12, "Thu", tempo_run(8, 5)),
    (12, "Fri", easy_run(7)),
    (12, "Sat", long_run_mp_finish(14, 4)),
    # Week 13 — 51 mi (peak)
    (13, "Mon", easy_run(6)),
    (13, "Tue", hill_tempo(7)),
    (13, "Thu", tempo_run(10, 7)),
    (13, "Fri", easy_run(8)),
    (13, "Sat", long_run_easy(20)),
    # Week 14 — 48 mi
    (14, "Mon", easy_run(6)),
    (14, "Tue", interval_workout(6, 1000, 400)),
    (14, "Thu", tempo_run(9, 6)),
    (14, "Fri", easy_run(7)),
    (14, "Sat", long_run_mp_finish(18, 5)),
    # Week 15 — 38 mi (taper)
    (15, "Mon", easy_run(5)),
    (15, "Tue", interval_workout(4, 1200, 400)),
    (15, "Thu", tempo_run(7, 4)),
    (15, "Fri", easy_run(5)),
    (15, "Sat", long_run_mp_finish(14, 4)),
    # Week 16 — 31 mi (taper)
    (16, "Mon", easy_run(4)),
    (16, "Tue", mile_repeats(3)),
    (16, "Thu", tempo_run(6, 3)),
    (16, "Fri", easy_run(4)),
    (16, "Sat", long_run_easy(10)),
    # Week 17 — 26 mi (taper)
    (17, "Mon", easy_run(4)),
    (17, "Tue", hill_repeats_long(3)),
    (17, "Thu", tempo_run(5, 2)),
    (17, "Fri", easy_run(4)),
    (17, "Sat", long_run_easy(8)),
    # Week 18 — 19 mi (taper)
    (18, "Mon", easy_run(3)),
    (18, "Tue", interval_workout(4, 400, 400)),
    (18, "Thu", easy_strides(4)),
    (18, "Fri", easy_run(3)),
    (18, "Sat", easy_run(5)),
    # Week 19 — race week (MCM Sun Oct 25)
    (19, "Tue", easy_strides(3)),
    (19, "Thu", easy_run(3)),
    (19, "Sat", easy_run(2, label="Shakeout")),
]


def workout_date(week, day, start=PLAN_START):
    return start + timedelta(days=(week - 1) * 7 + DAY_OFFSET[day])


def build_plan(start=PLAN_START):
    """Returns ordered list of dicts:
    {name, week, day, date, payload, distance_mi}"""
    out = []
    for week, day, wk in PLAN:
        name = "W%d %s %s" % (week, day, wk.suffix)
        payload = make_workout(name, wk.steps)
        out.append({
            "name": name,
            "week": week,
            "day": day,
            "date": workout_date(week, day, start),
            "payload": payload,
            "distance_mi": total_distance_m(wk.steps) / MILE,
        })
    return out
