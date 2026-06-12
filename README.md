# Garmin Structured Workout Uploader — v2

MCM 2026 plan (19 weeks, 93 workouts) → Garmin Connect → Forerunner 255.
Python 3.9 / garminconnect 0.2.8 compatible, no other dependencies.

## What changed from v1

1. **Plan is data, not code.** All 93 workouts live in `plan.py` as one line each (`(4, "Tue", interval_workout(6, 800, 400))`). Edit distances there; builders and uploader are untouched.
2. **Idempotent sync.** `upload` skips workouts already on Connect by name and only uploads what's missing. Safe to re-run after a partial failure. `--force` restores the old delete-everything behavior.
3. **Safer delete filter.** Anchored regex `^W(1[0-9]|[1-9]) (Mon|Tue|Thu|Fri|Sat) ` — can't match "Weekly Run", "Wednesday Hills", "W20 ...", etc.
4. **Real retry/backoff.** Reads the actual HTTP status off the exception (multiple attribute paths + regex fallback), retries 429/5xx with exponential backoff, and throttles all calls to ≥0.5s apart so 429s rarely happen at all.
5. **Backups before destruction.** `delete` and `upload --force` write all remote workout JSON to a timestamped backup file first; `delete` requires typing `delete` to confirm.
6. **Verification built in.** Every upload ends with a verify pass; `verify --deep` fetches each workout back and checks step counts, total distance, and that `estimatedDurationInSecs` didn't sneak in.
7. **Golden-file diff.** `golden --id <workoutId>` structurally diffs a hand-made Connect workout against generated JSON.
8. **Proper CLI** with `--dry-run`, `--single`, `--start-date`, `--no-schedule`, plus `export` and a guided `smoke` test.
9. **Step descriptions** ("Tempo — comfortably hard", "Jog down recovery") shown on the watch's step-transition screen.

## Usage

```bash
pip3 install "garminconnect>=0.2.8"

python3 upload_garmin_workouts.py upload --dry-run     # preview, no network
python3 upload_garmin_workouts.py smoke                # 1 workout → watch → delete
python3 upload_garmin_workouts.py upload               # sync all 93 + schedule
python3 upload_garmin_workouts.py verify --deep        # fetch back and validate
python3 upload_garmin_workouts.py export --out json/   # dump payloads
python3 upload_garmin_workouts.py delete               # backup, confirm, delete
python3 upload_garmin_workouts.py golden --id 1234567  # schema diff vs hand-made
```

Auth: set `GARMIN_EMAIL` / `GARMIN_PASSWORD` or answer the prompt once; tokens persist in `~/.garmin_tokens`.

## Coach dashboard

```bash
python3 coach.py     # opens http://127.0.0.1:8765
```

Local web app (stdlib only, same Garmin tokens): 19-week calendar read live from your Connect schedule, move any workout to a new date, shift a whole week for vacations, weekly planned-vs-actual mileage chart, race countdown. Moves sync to Garmin immediately — sync the watch after. Caveat: `upload --force` re-schedules everything back to plan.py dates, wiping dashboard moves.

## Portfolio demo (no Garmin account needed)

```bash
python3 make_demo.py     # builds demo/index.html — one static file
```

The full UI on a synthetic athlete (mid-week-6: on-target runs, one missed day, one slow tempo, a short-sleep readiness banner). Drag-and-drop, vacation mode, and the report card all work — every API call is intercepted by a fetch shim over embedded sample data. Deploy `demo/` to GitHub Pages / Vercel / Netlify. Generated from the real app's UI, so it can't drift.

## Security

- Tokens live in `~/.garmin_tokens` (outside the repo); password entry uses `getpass`, never logged. The `.gitignore` blocks tokens, backups, and fetched workout JSON from commits.
- `--lan` mode requires a per-session secret (`?key=…` printed in Terminal); localhost is always allowed. Without the key, other devices on your Wi-Fi get 403.
- This app uses Garmin's unofficial consumer API via your own login — fine for personal use, not a basis for hosting other people's accounts. A multi-user product would need Garmin's official Connect Developer Program (OAuth) and a real backend.

## Tests

```bash
python3 test_upload_garmin.py    # 16 tests, ~3,300 assertions, no network
```

Covers all spec categories: bug regressions (estimatedDurationInSecs, HR targets), API contract, step ordering, speed/distance sanity, weekly volume vs. plan targets, schedule dates (starts Mon 2026-06-15, ends Sat 2026-10-24, no Wed/Sun), delete-filter safety, and deep inspections of W1/W4/W7 sample workouts.

## Notes & open items

- **Plan reconstruction:** quality workouts match the spec's weekly table exactly; easy-run distances were reconstructed to hit each week's mileage (tests enforce ±10%). Adjust any line in `plan.py` — tests will keep you honest.
- **Bug 7 (the real "Run Until Lap Press" cause, found by diffing Runna's stored JSON):** Garmin honors `conditionTypeId` and ignores the key string. The spec's table said 1=distance — actually **1=lap.button, 2=time, 3=distance, 7=iterations**. Every v1 upload (including the original script's 93 workouts still on the account) has this bug. Pace targets use `workoutTargetTypeId` 6 (`pace.zone`). The sync filter also now excludes Runna's names (`"W10 Sat Long Run - ..."`) via the `" - "` separator.
- **Bug 6 (found via smoke test):** the spec's payload nested `targetValueOne/Two` inside `targetType` — Garmin silently drops the step config and the watch shows "Run Until Lap Press". Fixed: target values at step level, plus `preferredEndConditionUnit`, `stepId`/`childStepId`, and repeat-child linking, matching Connect's stored schema. `test_bug6_garmin_step_schema` guards this. Use `list` and `fetch --id` to compare against any Runna workout's stored JSON.
- **Recovery jogs are NO_TARGET** for a clean watch display (spec didn't specify; matches the easy-run philosophy). Give them a pace in `interval_workout()` if you'd rather be coached.
- **Zero-padded names (W01…W19)** would fix Connect's alphabetical sort (W10 before W2) but change the delete filter and tests — deliberately not done; flip `"W%d %s %s"` to `"W%02d %s %s"` plus the regex if wanted.
