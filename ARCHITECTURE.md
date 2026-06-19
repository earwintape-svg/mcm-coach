# timely — architecture handoff

Audience: an engineering manager inheriting this system cold. What it is,
how it works, why it's built this way, and where it will bite you.

## The one-paragraph version

timely is a single-user marathon-training platform: a Python CLI pushes a
19-week structured workout plan to a Garmin watch via Garmin Connect's
(unofficial) API, and an always-on local web app — served from the user's
Mac, reachable from his phone anywhere via Tailscale — acts as the coach:
calendar with drag-to-reschedule, daily readiness briefings, Strava-grade
run analysis, a local SQLite store accruing proprietary data (RPE, notes,
gear), and push notifications to the phone. As of the FastAPI migration
(2026-06), the server is `main.py` + `src/` on FastAPI/uvicorn, with
intervals.icu as the primary read source for completed runs and a Google
Calendar mirror layer on top (see "Data plane" and "Publish + ops" below)
— dependencies are no longer "one"; see `requirements.txt`. The frontend
(`ui.html` + `app.js`) is hand-rolled vanilla JS, no framework, no build
step. A static demo with synthetic data is generated *from the production
frontend* and published on GitHub Pages.

## Components

### Build plane (plan → watch)
- **`builders.py`** — a small DSL producing Garmin's workout JSON. All
  schema landmines are quarantined here, learned empirically: Garmin
  trusts `conditionTypeId` and ignores the key string (1=lap.button,
  3=distance — the spec we started from was wrong); target values live at
  step level, not inside targetType; `estimatedDurationInSecs` must never
  be sent. Tests pin all of it.
- **`plan.py`** — the 93-workout plan as data, one line per workout.
  Weekly mileage, dates, and pace targets derive from it.
- **`upload_garmin_workouts.py`** — CLI: token auth (persisted by garth in
  `~/.garmin_tokens`), idempotent sync (skip-existing; `--force` replaces),
  retry with backoff + global ~0.5s throttle, calendar scheduling, backups
  before destructive ops, and verification tools (`verify --deep`,
  `golden` diff vs a known-good workout, `smoke` single-workout watch
  test, `fetch` raw JSON). Deletion safety: anchored name regex plus a
  `" - "` exclusion so third-party (Runna) workouts can't be touched.

### Data plane
- **`store.py`** — SQLite (WAL) at `~/Library/Application
  Support/MCMCoach/timely.db`. Two join hubs: `activity_id` (runs,
  raw_activities, run_details, annotations, best-efforts-to-be) and `date`
  (wellness, weather_log, external_metrics, plan schedule). Plus `gear`
  (joined through annotations.shoes), `weekly_reviews`, `schedule_events`
  (audit log of every move/skip), `gcal_events` (scheduleId ↔ Google
  Calendar eventId + body fingerprint, see Publish + ops), and `kv`
  (schedule cache). Philosophy: relational columns for what we query, raw
  JSON blobs for everything a third party controls (schema-on-read); new
  sources get new tables keyed by one of the two hubs, never new columns.
  `/api/import` is a generic inbox (Apple Health via Health Auto Export,
  etc.). Nightly `backup()` copies the DB into the (Drive-synced) project
  folder. Migrations today are ad-hoc (`try: ALTER … except` plus a `kv`
  flag) — idempotent by accident, not by design; T7 on the engineering
  backlog is a versioned `schema_version` + ordered migration list.
- **`intervalsicu_read.py`** — read-only intervals.icu client. **This is
  now the primary source for completed-run data**, ahead of Garmin:
  `src/services/actuals.py`'s `fetch_actuals()` precedence is
  **intervals.icu → Garmin Connect REST (fallback) → local store (last
  resort, marked `stale`)**. Garmin Connect remains the *only* write path
  — it's still the sole way a structured workout reaches the watch; this
  module never writes anything. (An earlier draft of this module's
  docstring said activities sync in from a Suunto watch — that was a
  shelved alternate-device prototype, see `archive/builders_suunto.py`.
  The runtime device, throughout, is a Garmin Forerunner 255.)

### Serve plane (`main.py` + `src/`, FastAPI/uvicorn)
- **Server**: FastAPI app (`main.py`) + `src/api/routes.py` for HTTP
  handlers, `src/services/*.py` for one module per concern (schedule,
  actuals, fitness, trends, coaching, wellness, weather, notify, cache,
  garmin client, gcal sync, plan summary). `coach.py` is now a 16-line
  backward-compatible shim (`from main import main`) so launchd/lan.sh and
  the notify cron jobs keep working unchanged. JSON endpoints: data/
  actuals/wellness/weather/fitness/trends/review/gear/run/{id}, mutations
  move/shift_range/unschedule/annotate/gear/import/coach-apply/run-gear/
  sync-calendar. Auth: localhost trusted; LAN/Tailscale requires a
  persistent key (`~/.mcm_coach_key`) compared with `hmac.compare_digest`;
  security headers; 64KB body cap. **Known gap**: all 8 mutating routes
  take `body: dict` and index into it manually rather than Pydantic
  request models — FastAPI's validation benefit isn't being used (T5 on
  the backlog). Production is **Python 3.9.6**; the FastAPI routes
  correctly use `Optional[X]`/`Union[X, Y]` rather than `X | Y` because
  FastAPI does introspect route signatures via `get_type_hints()` at
  startup, which would crash on 3.9 with the newer union syntax. Two
  non-route helper functions (`src/services/trends.py:build_week_review`,
  `src/services/schedule.py:next_clean_slot`) do use `X | None` and only
  survive because `from __future__ import annotations` defers evaluation
  *and* nothing calls `get_type_hints()` on them — latent, not actively
  dangerous, but worth cleaning up (T6) so it stops being luck.
- **Caching**: plan summary memoized per process; schedule served from the
  kv store stale-while-revalidate (instant loads, background refresh);
  run details cached forever (immutable, versioned for schema changes);
  weather 30min; wellness 30min with store fallback when Garmin is down.
- **Background threads**: daily DB backup; run watcher (10-min poll →
  "Run synced" push when a new activity lands). Both are daemon threads
  with no crash visibility today — an exception in either dies silently
  (G10 on the hardening backlog).
- **Notifications**: macOS `osascript` + phone push via ntfy.sh (secret
  topic in `~/.timely_ntfy`). Scheduled by LaunchAgents: 7:30 briefing,
  18:30 log-nudge, Sunday 18:00 week-in-review.
- **Intelligence (rule-based, no ML)**: Daniels VDOT fitness + marathon
  projection; vacation planner and missed-run replanner (skip easy,
  relocate quality, never two hard days adjacent); weekly review lines;
  heat guidance from Open-Meteo; readiness flags from RHR baseline/sleep/
  Body Battery.
- **Frontend**: `ui.html` + `app.js` (the former `coach.PAGE`
  Python-string embedding has been extracted out — this was weakness #4
  below and is now done). Vanilla JS (~50KB), hand-rolled SVG charts, no
  framework, no build step, loads in <100ms. Three tabs (Today/Plan/
  Activities), drag-and-drop on pointer:fine, tap-to-move on touch, bottom
  tab bar on mobile, run analysis with scrub-synced charts + route dot +
  split/range selection + pace-colored route + HR zones. The
  apple-touch-icon is a PNG rasterized at runtime in pure stdlib (now in
  `main.py`). XSS audit (T8, 2026-06-19): an `escapeHTML()` helper already
  existed and covered most user-text interpolation (notes, gear/shoe
  names), but the calendar grid and week-report views — the most-viewed,
  most third-party-exposed surfaces — interpolated Garmin/Runna workout
  titles raw, including inside an HTML attribute. Fixed; see
  `tests/test_frontend_escaping.py`. Three sites pass a title through a
  single-quoted JS string inside an `onclick` attribute instead — that
  context needs quote-stripping, not HTML-escaping, since the browser
  HTML-decodes the attribute before the inline handler is JS-parsed.

### Publish + ops
- **`make_demo.py`** — builds `docs/index.html` + `docs/apple-touch-icon.png`
  from `main._asset()`/`main._icon_png()` plus a fetch shim over synthetic
  data; GitHub Pages serves it (https://earwintape-svg.github.io/mcm-coach/).
  The demo cannot drift from the product because it *is* the product's
  frontend. (This broke silently during the FastAPI migration — it used to
  pull `plan_summary`/`_asset`/`_icon_png` off `coach`, which is now a
  16-line shim with none of those; fixed to import from `main` and
  `src.services.plan_svc` instead.)
- **`src/services/gcal.py` + `setup_gcal.py`** — independent mirror layer:
  pushes the Garmin-sourced schedule to a dedicated "MCM Marathon Plan"
  Google Calendar via outbound API calls (push-based, so the LAN-only
  server never needs to be publicly reachable). Planned workouts render
  Runna-style (emoji title, distance, pace-by-segment description); once
  a day's actual run is found via the `actuals.py` precedence above, that
  day's event upgrades automatically to a timed event with a
  Summary/Description/Laps breakdown. `setup_gcal.py` is the one-time
  interactive OAuth consent flow (writes `~/.gcal_token.json`, outside the
  repo). This is additive, not a replacement: Garmin Connect still owns
  the watch push, intervals.icu still owns the actuals read.
- **`lan.sh`** — service manager: LaunchAgent running the server from App
  Support (a copy — macOS TCC forbids launchd reading ~/Documents),
  re-synced on every install/restart (now copies `main.py`, `src/`,
  `intervalsicu_read.py`, `requirements.txt` in addition to the original
  file set, and pip-installs the new deps); status/url/notify-on/
  notify-off; `watch` = autoship loop (a `.ship_request` file containing
  only a commit message triggers `ship.sh`).
- **`ship.sh`** — tests → demo rebuild → git commit/push → server restart.
  Also broke silently during the migration: it ran the retired
  `test_upload_garmin.py` instead of `pytest`, and `git add`-ed an
  explicit file list that didn't mention `main.py`, `src/`, `tests/`, or
  any of the intervals.icu/gcal files — fixed to run `pytest -q` and
  `git add -A` (safe now that `.gitignore` actually covers secrets/
  generated/db files).
- **Networking**: LAN via key URL; anywhere via Tailscale (WireGuard);
  Mac wakes itself daily at 7:25 (`pmset repeat`).

## Honest weakness register

1. **The Garmin API is unofficial.** The entire write path (workout
   upload, scheduling) and the sync read path ride an undocumented
   consumer API via garth. Garmin can change or block it without notice.
   Mitigations: reads degrade to the local store; all Garmin knowledge is
   concentrated in two files; `golden`/`fetch` exist to re-diff reality.
   Residual risk: high and irreducible without Garmin's official program.
2. **Single machine, single point of failure.** The Mac is the cloud. If
   it's asleep, dead, or off Wi-Fi: no app, no pushes, no sync (the watch
   plan keeps working — workouts are already on Garmin). DB backup is a
   daily copy to a Drive-synced folder; the DB itself is unencrypted
   health data at rest on disk.
3. **No TLS from the server itself.** LAN traffic is plain HTTP guarded by
   a bearer key in the URL (which lands in browser history). Tailscale
   wraps remote traffic in WireGuard, which is the real transport
   security; bare-LAN use is honest-but-plaintext. (`tailscale serve`
   could add real HTTPS later.)
4. **Frontend with no JS tests.** `PAGE` has since been extracted into
   `ui.html` (no longer embedded as a Python string — that part of this
   item is resolved), but there's still no JS test harness; verification
   is "it loads + manual click-through." Known latent issue, not
   re-verified this round: user-entered notes are interpolated into HTML
   unescaped — self-XSS only (single user, own data), but it's the first
   thing to fix if anyone else ever logs in (T8 on the backlog).
5. **No CI.** Tests run locally via ship.sh; nothing prevents a push from
   a different machine skipping them. A 10-line GitHub Action would close
   this (T4 on the backlog).
6. **Autoship trust boundary.** Anything that can write `.ship_request`
   into the (Drive-synced!) folder triggers a deploy. Bounded — only
   ship.sh runs and the file is used solely as a commit message — but a
   compromised Drive account = code-push capability.
7. **API routes skip request validation.** All 8 mutating FastAPI routes
   take `body: dict` and index in manually instead of Pydantic models —
   paying FastAPI's dependency cost without its main benefit (T5).
   Replacing this must keep using `Optional[X]`/`Union[X, Y]`, never
   `X | Y` — see the Python 3.9 note in "Serve plane" above.
8. **Migrations are ad-hoc.** `store.init()`'s `try: ALTER … except` + a
   `kv` flag works but is idempotent by accident (T7).
9. **Assorted smaller debts**: timezone-naive dates (server-local
   assumptions); Python 3.9 from CommandLineTools (an Xcode update can
   move it; production is pinned at 3.9.6 — verify in the real
   environment, not whatever a sandbox/CI runner reports); coarse
   per-process DB lock (fine for 2 processes, not 20); VDOT from training
   runs is a floor, presented as such but easy to misread; single-user
   assumptions hardcoded throughout (race date, zones, location for
   weather); the intervals.icu/Garmin/store actuals precedence and the
   gcal mirror layer existed for a while without being documented here —
   if you add a third data source or sync target, update this file in the
   same commit, not "eventually."

## Retrospective: a week of work sat uncommitted

During the stdlib→FastAPI migration, `HEAD` stayed on an old commit for
about a week while `main.py`, `src/`, `tests/`, and the intervals.icu/gcal
integration were built and verified (tests green, server running) but
never checked in. A 2026-06-19 review caught it, along with a real Google
OAuth `client_secret.json` sitting untracked and uncovered by `.gitignore`
— one `git add -A` away from a credential leak. Both are fixed (commit
early; `.gitignore` now covers `client_secret*.json`), but the lesson is
structural, not a one-off: **working-but-uncommitted is not a checkpoint.**
Commit at the end of every session that leaves the tree better than it
found it, even mid-refactor, even if the commit message says "wip."

## If you take over tomorrow

Day 1: run `./lan.sh status`, `python3 -m pytest -q`, read `builders.py`
top comments and `store.py` SCHEMA — that's 80% of the mental model. Then
read `src/services/actuals.py` for the intervals.icu/Garmin/store
precedence and `src/services/gcal.py` for the Calendar mirror — both are
new since this doc was first written and easy to miss. First engineering
investments, in rough order: GitHub Actions CI on push (T4); an
HTML-escape helper applied at every interpolation of user text (T8);
Pydantic request models on the mutating routes, keeping `Optional[X]`
syntax for Python 3.9 (T5). After that, `ROADMAP.md` is the current
product-truth doc — `PRDS.md` (the original spec) and `WRITEUP.md` are
archived at `archive/` as historical record, superseded by ROADMAP.md and
this file.
