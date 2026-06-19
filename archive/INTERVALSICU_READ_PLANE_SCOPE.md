# Scoping: replace Garmin Connect read-plane with intervals.icu

Context: you're now wearing the Suunto watch for actual training, not just
receiving planned workouts via the intervals.icu→Suunto bridge. `coach.py`'s
*read* side (run analysis, wellness, readiness, VDOT) currently pulls
exclusively from Garmin Connect via the unofficial `garminconnect`/garth API.
Once runs stop landing on Garmin, that side goes stale. This scopes a
replacement built on intervals.icu's Open API, which already ingests
activities from Suunto.

## Why this is tractable

`store.py` was designed schema-on-read: `raw_activities`/`run_details` store
opaque JSON blobs keyed by activity id, `external_metrics` is keyed by
`(source, date)`. Nothing in the schema assumes Garmin. The plan is to write
one new adapter module that returns data in the **same shapes** the frontend
and `coach.py`'s existing logic (`_weekly_of`, VDOT, chart rendering) already
expect, then swap the call sites. If the shapes match, the ~1,800 lines of
frontend JS need zero changes.

## intervals.icu endpoints to use (confirmed via API docs/forum)

- `GET /api/v1/athlete/0/activities?oldest=YYYY-MM-DD&newest=YYYY-MM-DD` —
  activity summaries (type, distance, moving time, avg/max HR, avg pace,
  `stream_types`). Replaces the Garmin activity-list call in
  `fetch_actuals()`.
- `GET /api/v1/activity/{id}` — single activity detail (the fuller summary
  fields: avg HR, max HR, cadence, elevation gain).
- `GET /api/v1/activity/{id}/streams.json?types=heartrate,latlng,distance,time,altitude,pace`
  — second-by-second series for the pace/HR charts and route map. Replaces
  the `/activity-service/.../details` call in `fetch_run_detail()`.
- `GET /api/v1/athlete/0/wellness?oldest=&newest=` — daily wellness: resting
  HR, HRV, sleep, plus computed `ctl`/`atl`/`rampRate` (form = ctl − atl).
  Replaces the Garmin `usersummary-service` call in `fetch_wellness()`.
- Auth: same `API_KEY:<key>` basic auth + browser User-Agent already working
  in `upload_intervalsicu.py` — one shared `_request()` helper can serve
  both the write scripts and this new read module.

## Feature-by-feature mapping

**Average HR, HR zones, pace/HR charts (`fetch_run_detail`)** — direct
replacement via `/activity/{id}` + `/activity/{id}/streams.json`. Same data,
different source. Low risk, assuming Suunto activities actually land in
intervals.icu with HR streams attached (depends on your Suunto→intervals.icu
sync including the FIT file, not just a summary — worth a quick test run to
confirm).

**Route / GPS map** — `latlng` stream from the same endpoint. Same as above.

**Laps/splits** — *unconfirmed*. Garmin has a dedicated splits endpoint;
intervals.icu's equivalent (`/activity/{id}/intervals` or laps embedded in
the activity object) needs to be checked once a real Suunto activity exists
to test against. Worst case, laps can be derived from the `distance`+`time`
streams directly.

**Weekly mileage (`_weekly_of`) and VDOT/fitness projection** — no change to
the math (`_vdot_of`/`_predict_secs` only need `mi` + `paceSec` per run).
Only the input list changes source. Low risk.

**Run-synced push notification** — currently a 10-min poll of Garmin's
activity list. Becomes a poll of
`/api/v1/athlete/0/activities?oldest=<today>`. Note: the *latency* changes —
it depends on how fast Suunto pushes the activity to intervals.icu, which may
be slower than Garmin's near-real-time sync. Worth setting expectations
(could be minutes to an hour, not "10 minutes after you stop your watch").

**Readiness flags — RHR & sleep** — `/athlete/0/wellness` exposes
`restingHR` (defined by Suunto's integration as lowest HR during sleep) and
sleep fields, **but this requires opting in**: Settings → the Suunto box on
intervals.icu → click the wellness-items list and select what to import
(resting HR / sleep / HRV / etc.), then optionally "Download Old Data" for
history. Not on by default. Known data-quality caveats from the forum: some
Suunto models had sleep-duration/date-alignment bugs (improved but not
guaranteed fixed for every model).

**Readiness flags — Body Battery** — no identical equivalent, but better
than "none": intervals.icu's Suunto wellness sync can include **HRV**, and
Suunto's own "Recovery/HRV" feature (on newer models — Race, Ocean, 9 Peak
Pro; *not* on cheaper models like the Suunto 5 Peak) computes a 7-day HRV
average vs. a 60-day baseline, which is the same underlying approach
HRV-based readiness tools (Whoop/Oura) use. Options, in order of promise:
  (a) once HRV is flowing into `/athlete/0/wellness`, base the readiness
      flag on HRV-vs-baseline (a real recovery signal, arguably better than
      Body Battery's black-box score);
  (b) fall back to intervals.icu's `form` (ctl − atl, training-stress
      balance) — a different concept (load vs. capacity), so thresholds/
      wording in `coach.py`'s readiness messages would need rewriting;
  (c) drop the signal if your watch model doesn't track HRV.

**Action item before the verification run**: turn on Suunto wellness sync
in intervals.icu settings now, so a day or two of wellness data exists to
inspect alongside the run's activity data.

## New code

1. `intervalsicu_read.py` — new module: thin client (reuse the auth/UA
   pattern from `upload_intervalsicu.py`) plus three functions returning
   data already shaped like today's internal dicts:
   - `fetch_runs(oldest, newest)` → list of `{activityId, date, mi, paceSec, name}`
   - `fetch_wellness_days(n=7)` → list of `{date, rhr, sleepH, bb}` (bb
     likely always `None`, or repurposed for `form`)
   - `fetch_run_detail(activity_id)` → `{summary, laps, series, route}`
2. `coach.py` — swap the bodies of `fetch_actuals()`, `fetch_wellness()`,
   `fetch_run_detail()` to call the new module instead of `client()`/`api()`.
   Keep the existing store fallback ("if down, serve from local store")
   unchanged — that pattern is source-agnostic already.
3. `store.py` — no schema changes needed. `raw_activities`/`run_details`
   keys become intervals.icu activity ids (still strings); fine since
   they're opaque keys.
4. Readiness logic in `coach.py` (~line 877 area) — rework once the Body
   Battery decision is made.

## Effort estimate

Comparable in size to the original Garmin integration in `fetch_*`, but
smaller in *scope* because VDOT, weekly aggregation, scheduling, and all
frontend chart/rendering code are reused unchanged — this is purely a data
adapter swap. Realistic estimate: a few focused sessions, gated on the
verification step below (can't finalize field mappings without a real
Suunto activity in intervals.icu to inspect).

## Before writing code: verification step

Go for one run with the Suunto watch, let it sync to the Suunto app, confirm
it appears as an activity in intervals.icu, then call:

```
python3 manage_intervalsicu_events.py list  # (events, not activities — see below)
```

— actually for activities we'd add a small `list-activities` helper to
inspect the real JSON shape (`GET /api/v1/athlete/0/activities`) and the
`streams.json` response for that activity, and the `/athlete/0/wellness`
response for that day. That real payload settles the open questions above
(laps endpoint, HR stream presence, whether wellness/RHR/sleep actually
arrives from Suunto) before any `fetch_*` rewrite begins.
