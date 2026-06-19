"""Prototype: generate SuuntoPlus Guide files for a sample of workouts from
plan.py, via builders_suunto.py.

Read-only against the real plan; no network calls, no Suunto credentials
needed. Each sample workout produces:
  - <slug>.guide.json   -- the guide.json contents, pretty-printed
  - <slug>.zip          -- guide.json + icon.png, ready to POST to the
                            SuuntoPlus Guide Cloud API (once OAuth is set up)

Usage:
    python3 make_suunto_guides.py [out_dir]   # default out_dir: suunto_prototype
"""
import json
import os
import sys

from plan import PLAN, workout_date
from builders_suunto import build_guide, build_guide_zip, validate_guide

# A handful of representative workouts: easy run (NO_TARGET), interval
# (repeat + pace.zone target), tempo, hills (two repeat blocks), long run,
# MP-finish long run, and easy+strides.
SAMPLES = {
    (1, "Mon"),   # easy_run(4)
    (1, "Tue"),   # easy_strides(5)
    (3, "Tue"),   # hill_repeats_short(8)
    (3, "Thu"),   # tempo_run(5, 2)
    (4, "Tue"),   # interval_workout(6, 800, 400)
    (7, "Sat"),   # long_run_mp_finish(16, 4)
    (11, "Sat"),  # long_run_easy(20)
}


def main(out_dir="suunto_prototype"):
    os.makedirs(out_dir, exist_ok=True)
    print("%-30s %-8s %s" % ("workout", "steps", "validation"))
    for week, day, wk in PLAN:
        if (week, day) not in SAMPLES:
            continue
        name = "W%d %s %s" % (week, day, wk.suffix)
        d = workout_date(week, day)
        ext_id = "timely-w%d-%s" % (week, day.lower())
        kwargs = dict(
            local_date=d,
            external_id=ext_id,
            description="%s — Week %d %s. From timely's MCM 2026 plan."
                         % (wk.suffix, week, day),
            short_description="MCM26 W%d" % week,
        )
        guide = build_guide(name, wk.steps, **kwargs)
        problems = validate_guide(guide)
        zip_bytes, _ = build_guide_zip(name, wk.steps, **kwargs)

        slug = name.replace(" ", "_")
        with open(os.path.join(out_dir, slug + ".guide.json"), "w") as f:
            json.dump(guide, f, indent=2)
        with open(os.path.join(out_dir, slug + ".zip"), "wb") as f:
            f.write(zip_bytes)

        status = "OK" if not problems else "ISSUES: " + "; ".join(problems)
        print("%-30s %-8d %s" % (name, len(guide["steps"]), status))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "suunto_prototype")
