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
  etc.). `backup()` runs every 5 min (not nightly — the brief's
  description was stale; see RC-5-style note: re-read code, not the doc,
  when they disagree) and, since G1 (2026-06-19), keeps rolling
  timestamped daily/weekly snapshots in the (Drive-synced, so off-machine)
  `BACKUP_DIR` rather than overwriting one file — a corrupt write used to
  silently destroy the only copy. `verify_backup()` (G2) loads the latest
  snapshot into a throwaway temp DB, runs `PRAGMA integrity_check`, and
  sanity-checks row counts against the live DB; `restore_drill_loop` runs
  it monthly (cadence tracked in `kv`, survives restarts) and `python3
  main.py verify-backup` runs it on demand. Migrations are versioned
  (`schema_version` + an ordered `MIGRATIONS` list, T7, resolved) — see
  `tests/test_store_migrations.py`.
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
- **Background threads**: DB backup (5-min); run watcher (10-min poll →
  "Run synced" push when a new activity lands); restore drill (G2, hourly
  tick, gated to run monthly — see Data plane above). All three run
  through
  `_run_resilient_loop` (`src/services/notify.py`, G10, 2026-06-19): a bad
  iteration is still tolerated (the loop never dies), but now every
  exception is logged with a full traceback, the first failure of a streak
  triggers a phone push (rate-limited to once/hour for the same streak), a
  recovery triggers one more push, and a once-daily heartbeat line confirms
  the thread is alive even when nothing is failing. Logging itself is
  `src/services/applog.py` (T11): a rotating file at
  `~/Library/Logs/timely.log` (5MB×3), separate from launchd's raw stdout
  capture (`~/Library/Logs/mcmcoach.log`, unstructured, unrotated, set in
  `lan.sh`). `main.py` also gained an HTTP middleware logging
  method/path/status/latency per request (info, or warning at ≥400) —
  before this, an error report from the phone had nothing server-side to
  correlate it against.
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
  expanded the explicit list to cover the new tree. Deliberately **not**
  `git add -A`: this directory accumulates sandbox/FUSE artifacts
  (`.fuse_hidden*`, `*.egg-info/`) that should never be staged, and a
  blanket add is exactly how the `client_secret.json` near-miss in the
  retrospective below would have happened. See `AGENTS.md`.
- **`AGENTS.md` + `.github/CODEOWNERS`** (2026-06-19) — repo governance for
  the multiple agents now working this codebase concurrently (infra
  engineer / product engineer / UX designer). OPEN zones (`product/`,
  `design/`) are docs/design, no review gate; everything else is
  RESTRICTED — must keep `pytest -q` green, focused commits, never weaken
  the Garmin schema invariants. `hooks/pre-commit` (`core.hooksPath=hooks`)
  blocks direct commits to `main`; the workflow is branch → push → PR →
  CI-gated merge. The reference docs (`ROADMAP.md`, the task briefs) moved
  to `product/` as part of this; a design critique doc lives in `design/`.
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
4. **Frontend XSS — resolved (T8, 2026-06-19).** `PAGE` was extracted into
   `ui.html`/`app.js` (no longer a Python string) and the real interpolation
   gaps found this round (calendar grid, week report, vacation preview, all
   `e.message` toasts) are now routed through `escapeHTML()`; see
   `tests/test_frontend_escaping.py`. Still true: no JS test *harness*
   beyond that targeted smoke test — broader frontend testing would need
   jsdom or a real browser runner, not attempted here.
5. **CI — resolved (T4).** `.github/workflows/ci.yml` runs `pytest -q` +
   an import smoke test on Python 3.9 on every push/PR. Residual gap:
   branch protection requiring it to pass before merge is a GitHub repo
   setting, not something committable from this environment — needs a
   human to flip on (see also the `main`-protection pre-commit hook under
   "Publish + ops," which is a local/client-side guard, not a server-side
   one; it doesn't substitute for branch protection).
6. **Autoship trust boundary.** Anything that can write `.ship_request`
   into the (Drive-synced!) folder triggers a deploy. Bounded — only
   ship.sh runs and the file is used solely as a commit message — but a
   compromised Drive account = code-push capability.
7. **API request validation — resolved (T5).** All 8 mutating FastAPI
   routes now take typed Pydantic models (`src/api/schemas.py`) instead of
   a bare `body: dict`; invalid payloads get a 422 instead of an
   unhandled `KeyError`/`TypeError` deep in business logic. Per the Python
   3.9 note in "Serve plane," every model field uses `Optional[X]`/
   `Union[X, Y]`, never `X | Y`.
8. **Migrations — resolved (T7).** `store.py` now has a `schema_version`
   row in `kv` and an ordered `MIGRATIONS` list applied when
   `version < i`, with legacy-state detection so a pre-existing DB that
   went through the old ad-hoc `try: ALTER … except` logic isn't
   double-migrated. See `tests/test_store_migrations.py`.
9. **Assorted smaller debts**: timezone-naive dates — audited 2026-06-19
   (T11): `date.today()`/`datetime.now()` are used naively in 11 files
   (`schedule.py`, `trends.py`, `gcal.py`, `actuals.py`, `notify.py`,
   `coaching.py`, `weather.py`, `fitness.py`, `wellness.py`, `plan_svc.py`,
   `routes.py`), always against the Mac's system clock, never normalized
   to/from UTC. For a single-user app where the Mac lives wherever the
   runner currently is, "today" tracking the system clock is the *correct*
   behavior, not a bug — the risk is narrower than "wrong timezone": a
   midnight-boundary mismatch between a UTC activity timestamp from
   intervals.icu/Garmin and a local `date.today()` comparison, most likely
   right after a timezone change (race travel) or DST transition. Worth
   fixing if a "missing today's run" report ever shows up around such a
   transition; not worth a speculative rewrite across 11 files today. Left
   as documented, not changed, per this ticket's "audit" scope.
   Python 3.9 from CommandLineTools (an Xcode update can
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
new since this doc was first written and easy to miss. T0–T8 and T10 are
done (CI, XSS, Pydantic request models, versioned migrations, the 3.9 pin
— see the weakness register above for what each closed). Current
priority, per the 2026-06-19 EM review (`product/ENGINEERING_REVIEW_TASKS.md`
rev.4): G1+G2 (versioned, verified backups) first — "protect the data" is
the stated top goal and a corrupted single-file backup is the biggest
single-point-of-failure left; then G5 (pre-commit secret scanning) and G7
(mypy in CI); T11/G3/G4 and the rest of G6–G12 round out the backlog.
`product/` now holds `ROADMAP.md` and the task briefs (moved there
2026-06-19, see `AGENTS.md` for the OPEN/RESTRICTED zone split that
motivated it) — `PRDS.md`/`WRITEUP.md` stay archived at `archive/` as
historical record, superseded by `product/ROADMAP.md` and this file.
