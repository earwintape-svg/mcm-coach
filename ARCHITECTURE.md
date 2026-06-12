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
gear), and push notifications to the phone. Total external Python
dependencies: **one** (`garminconnect`, which brings `garth` for OAuth).
Everything else — web server, charts, database, PNG icon rendering — is
stdlib and hand-rolled. A static demo with synthetic data is generated
*from the production frontend* and published on GitHub Pages.

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
  (audit log of every move/skip), and `kv` (schedule cache). Philosophy:
  relational columns for what we query, raw JSON blobs for everything a
  third party controls (schema-on-read); new sources get new tables keyed
  by one of the two hubs, never new columns. `/api/import` is a generic
  inbox (Apple Health via Health Auto Export, etc.). Nightly `backup()`
  copies the DB into the (Drive-synced) project folder.

### Serve plane (`coach.py`, ~2k lines)
- **Server**: stdlib `ThreadingHTTPServer`. JSON endpoints: data/actuals/
  wellness/weather/fitness/trends/review/gear/run/{id}, mutations move/
  shift_range/unschedule/annotate/gear/import. Auth: localhost trusted;
  LAN/Tailscale requires a persistent key (`~/.mcm_coach_key`) compared
  with `hmac.compare_digest`; security headers; 64KB body cap; client
  input validated as ints/ISO dates before touching Garmin paths.
- **Caching**: plan summary memoized per process; schedule served from the
  kv store stale-while-revalidate (instant loads, background refresh);
  run details cached forever (immutable, versioned for schema changes);
  weather 30min; wellness 30min with store fallback when Garmin is down.
- **Background threads**: daily DB backup; run watcher (10-min poll →
  "Run synced" push when a new activity lands).
- **Notifications**: macOS `osascript` + phone push via ntfy.sh (secret
  topic in `~/.timely_ntfy`). Scheduled by LaunchAgents: 7:30 briefing,
  18:30 log-nudge, Sunday 18:00 week-in-review.
- **Intelligence (rule-based, no ML)**: Daniels VDOT fitness + marathon
  projection; vacation planner and missed-run replanner (skip easy,
  relocate quality, never two hard days adjacent); weekly review lines;
  heat guidance from Open-Meteo; readiness flags from RHR baseline/sleep/
  Body Battery.
- **Frontend**: one HTML page embedded as a Python string (`PAGE`).
  Vanilla JS (~50KB), hand-rolled SVG charts, no framework, no build step,
  loads in <100ms. Three tabs (Today/Plan/Activities), drag-and-drop on
  pointer:fine, tap-to-move on touch, bottom tab bar on mobile, run
  analysis with scrub-synced charts + route dot + split/range selection +
  pace-colored route + HR zones. The apple-touch-icon is a PNG rasterized
  at runtime in pure stdlib.

### Publish + ops
- **`make_demo.py`** — builds `docs/index.html` from `coach.PAGE` plus a
  fetch shim over synthetic data; GitHub Pages serves it. The demo cannot
  drift from the product because it *is* the product's frontend.
- **`lan.sh`** — service manager: LaunchAgent running the server from App
  Support (a copy — macOS TCC forbids launchd reading ~/Documents),
  re-synced on every install/restart; status/url/notify-on/notify-off;
  `watch` = autoship loop (a `.ship_request` file containing only a commit
  message triggers `ship.sh`).
- **`ship.sh`** — tests → demo rebuild → git commit/push → server restart.
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
4. **Frontend monolith with no JS tests.** ~1,800 lines of JS inside a
   Python string; verification is "it compiles + marker greps," and the
   whole UI re-renders via innerHTML on every state change. Known latent
   issue: user-entered notes are interpolated into HTML unescaped —
   self-XSS only (single user, own data), but it's the first thing to fix
   if anyone else ever logs in. The string-embedding also caused two
   regressions already (marker drift); splitting PAGE into `ui.html` is
   the queued refactor.
5. **No CI.** Tests run locally via ship.sh; nothing prevents a push from
   a different machine skipping them. A 10-line GitHub Action would close
   this.
6. **Autoship trust boundary.** Anything that can write `.ship_request`
   into the (Drive-synced!) folder triggers a deploy. Bounded — only
   ship.sh runs and the file is used solely as a commit message — but a
   compromised Drive account = code-push capability.
7. **Assorted smaller debts**: timezone-naive dates (server-local
   assumptions); Python 3.9 from CommandLineTools (an Xcode update can
   move it); coarse per-process DB lock (fine for 2 processes, not 20);
   VDOT from training runs is a floor, presented as such but easy to
   misread; single-user assumptions hardcoded throughout (race date,
   zones, location for weather).

## If you take over tomorrow

Day 1: run `./lan.sh status`, `python3 test_upload_garmin.py`, read
`builders.py` top comments and `store.py` SCHEMA — that's 80% of the
mental model. First three engineering investments, in order: GitHub
Actions CI on push; an HTML-escape helper applied at every interpolation
of user text; extract `PAGE` to `ui.html`. After that, the roadmap
(ROADMAP.md / PRDS.md) is the product truth.
