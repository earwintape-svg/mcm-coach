# timely — Engineering Review & Task Brief (rev. 4)

**From:** Eng Manager (review prep)
**For:** the **infra engineer** (backend / infra / data). Front-end work lives in `FRONTEND_REDESIGN_TASKS.md` (the **product engineer**).
**Updated:** 2026-06-19 — T0–T7 reviewed and approved; next batch + role split below.

---

## ▶ NEXT FOCUS (rev. 4 — infra engineer, read this first)

**Status: T0–T7 are done and APPROVED.** Reviewed and verified by the EM: the OAuth secret never entered git history; the refactor went in as clean, ticket-tagged commits; CI is pinned to 3.9; Pydantic models correctly use `Optional`/`Union` (never `X | Y`); all **six** `X | None` landmines defused; web-layer auth + 422 validation tests added (`tests/test_api.py`, using `TestClient` without the lifespan context — nice catch); versioned schema migrations with a legacy-DB upgrade test. **I ran the full suite: 77 passed.** Strong work — including finding the four extra landmine sites and self-correcting the `git add -A` slip in `ship.sh`.

**T8 (XSS) has moved off your plate.** It's the product engineer's now, folded into the redesign — they're rewriting `app.js` anyway. **Do not touch `app.js` / `ui.html`.**

**Your next batch, in order:**

1. **Stand up the multi-agent pipeline first — it unblocks everyone.** Implement the worktree/branch model in `AGENTS.md`: protect `main`, require PR + green CI to merge. You own `.github/`. Until this exists you and the product engineer both commit to `main` and collide — the `index.lock` contention is already happening.
2. **G1 + G2 — backups that actually protect the data.** `backup()` currently overwrites a single file (a corruption silently destroys your only copy). Add rolling timestamped snapshots (retain ~14 daily / 8 weekly), ensure ≥1 copy lands off-machine, and add a `restore` / `verify-backup` command that loads the latest backup into a temp DB, runs `PRAGMA integrity_check`, and asserts row counts — scheduled monthly with a phone ping on failure.
3. **G5 — pre-commit with secret scanning.** `gitleaks` / `detect-secrets` + the formatter/linter, so the `client_secret.json` class of mistake is blocked by tooling, not vigilance.
4. **G7 — mypy in CI (pinned to 3.9).** Would have caught the `X | None` sites for free; catches the next one.
5. **Backlog:** G3 (confirm FileVault; encrypt the off-machine backup copy), G4 (one-command full data export), T11 (structured logging).

**Folder reorg — my ruling on the `product/` + `core/` question you raised:**
- `product/` (docs zone) — **approved.** Move reference docs there at a clean checkpoint.
- `core/` (moving code into a package dir) — **hold, not now.** `lan.sh sync_app()` deploys by copying flat files and `pyproject.toml` lists `py-modules` at the root; relocating code breaks the deploy. You were right to ask before moving. Leave code flat until `lan.sh` is reworked to install a wheel — separate ticket, not this cleanup.
- **Timing:** don't run the physical moves while commits are in flight (we're seeing `index.lock` contention now). Do it on a quiet tree, on its own branch.

**Keep doing:** surgical, staged commits; never `git add -A`.

---

## How to use this document
Each ticket: **Goal → Why → Files → Steps → Done when → Push back if.** Ground rules:
1. **This brief can be wrong.** Re-read named files before executing. If reality contradicts a ticket, **stop and report**, don't force it.
2. **Don't fight concurrency.** See T0/T1 for the (now-understood) git lock situation.
3. One ticket = one logical commit.
4. **Never weaken the Garmin schema invariants** in `builders.py` (no `estimatedDurationInSecs`; `conditionTypeId` 3 = distance; target values at step level). Load-bearing, learned the hard way.
5. **Verify in the real environment.** Prod is **Python 3.9.6** — not whatever the sandbox reports (see RC-4).

Priority: **P0** = before the review / next deploy. **P1** = this cycle. **P2** = backlog.

---

## Resolved context (was: open questions)
- **RC-1 Device:** Forerunner 255, current. The "Suunto" line in `intervalsicu_read.py` is dead cruft from a shelved prototype (`archive/builders_suunto.py`). Workouts still push via Garmin Connect. → one-line docstring fix (folded into T3).
- **RC-2 Data sources:** Confirmed precedence in `actuals.py`: **intervals.icu (primary read) → Garmin Connect REST (fallback) → local SQLite (last resort).** Garmin remains the **only** path to push structured workouts to the watch; intervals.icu is read-only. Google Calendar (`gcal.py`) is an independent mirror layer on top — it replaces nothing.
- **RC-3 Calendar:** gcal mirrors the Garmin-sourced schedule (and, after tonight, completed runs) into a human-readable calendar. Keep; just document.
- **RC-4 Python:** **Production = 3.9.6, full stop.** Sandbox 3.10 is irrelevant. Consequence: `X | None` annotations only survive because `from __future__ import annotations` defers them and nothing calls `get_type_hints()` on those functions. Two real latent landmines: `src/services/trends.py:59`, `src/services/schedule.py:86`. FastAPI routes correctly use `Optional[...]` because FastAPI *does* introspect route signatures. This shapes T5 and T6.
- **RC-5 Lock:** `.git/index.lock` is ~stale/orphaned (no contention on clean `git status` calls), not an active commit. Safe to remove **after** confirming no git GUI/IDE is mid-operation — human's call to verify.
- **RC-6 Upload tests:** `test_upload_garmin.py` was **deliberately moved** to `archive/`, not lost. Open item: were its assertions ported into the new suite (44 tests, passing)? → diff check in T2.

---

## P0 — Before the review

### T0 — Stop the secret from ever being committed (DO THIS FIRST)
**Goal:** `client_secret.json` (your real Google OAuth client secret) is sitting untracked in the repo root and is **not** in `.gitignore`. A single `git add -A` commits it.
**Why:** Leaking an OAuth client secret into git history (especially if `origin` is ever public/shared) means rotating credentials and scrubbing history — expensive and embarrassing. This must be closed before any of the git work in T1.
**Files:** `.gitignore`.
**Steps:** Add `client_secret.json` (and any `client_secret*.json`) to `.gitignore`. Confirm `git status` no longer lists it. Then proceed to T1.
**Done when:** `git check-ignore client_secret.json` returns the path.
**Push back if:** the file is already tracked in history (`git log --all -- client_secret.json` is non-empty) — then escalate: rotation + history scrub, not just an ignore rule.

### T1 — Get the refactor under version control (safely)
**Goal:** Commit the FastAPI refactor (`main.py`, `src/`, `tests/`, `requirements.txt`, intervals/gcal work) in reviewable chunks. HEAD is a week behind reality.
**Why:** The most valuable, most recent work has no checkpoint — one bad delete and it's gone.
**Files:** whole tree; `git status` first.
**Steps:**
1. Confirm T0 is done so the secret can't be swept in.
2. Confirm no git GUI/IDE is mid-operation, then clear the stale lock if present (RC-5).
3. Stage in logical commits: (a) `.gitignore` + `requirements.txt` + packaging, (b) `src/` package, (c) `tests/`, (d) intervals.icu/gcal, (e) doc updates, (f) confirmed-dead file removals only after `grep -rn` shows no imports.
4. Commit (b) with a message explaining the stdlib→FastAPI migration and layout.
**Done when:** `git status` clean except ignored files; `git ls-files` includes `main.py`, `src/`, `tests/`, `requirements.txt`; `client_secret.json` is **not** listed.
**Push back if:** the concurrent agent is mid-operation, or a "dead" file is still imported anywhere.

### T2 — Confirm the destructive-path tests actually survived
**Goal:** Verify the upload/delete safety assertions from `archive/test_upload_garmin.py` exist in the live suite; port any that don't.
**Why:** The code that mutates a **live Garmin account** must stay covered. The file move was deliberate (RC-6), but it's unconfirmed whether its ~3,300 assertions were carried into `tests/`.
**Files:** `archive/test_upload_garmin.py`, `tests/`, `builders.py`, `plan.py`, `upload_garmin_workouts.py`.
**Steps:** Diff the archived assertions against current `tests/`. Re-home any missing coverage (adapted to `build_plan()`, `total_distance_m`, `PLAN_NAME_RE`), keeping the no-network guarantee. Ensure the Bug-1/2/6/7 regressions and the delete-filter safety cases (must not match "Weekly Run", Runna's `" - "` names) are present.
**Done when:** `pytest -q` green; injecting `estimatedDurationInSecs` into a workout fails the suite.
**Push back if:** the coverage was intentionally dropped for a reason you can find documented — flag rather than re-adding blindly.

### T3 — Make the docs match the code
**Goal:** Reconcile `README.md` / `ARCHITECTURE.md` with reality and fix the Suunto docstring.
**Why:** Docs still claim "one dependency / stdlib only," omit intervals.icu + gcal, and carry a misleading Suunto comment. A reviewer who hits the contradiction stops trusting the docs.
**Files:** `README.md`, `ARCHITECTURE.md`, `intervalsicu_read.py` (docstring).
**Steps:** Update dependency/architecture sections (FastAPI, uvicorn, google-auth, intervals.icu). Document the RC-2 precedence and the gcal mirror layer. Add a one-line "why we left stdlib." Fix the `intervalsicu_read.py` docstring so the Suunto reference doesn't mislead.
**Done when:** No claim in either doc is contradicted by `requirements.txt` or the tree.

### T4 — Add CI + packaging
**Goal:** GitHub Actions running `pytest` + an import smoke test on push/PR; a `pyproject.toml`.
**Why:** No automated gate today; nothing forces tests before a push. Cheapest credible "professionalized" signal. Packaging also removes the `sys.path.insert` hack in `conftest.py`.
**Files:** new `.github/workflows/ci.yml`, new `pyproject.toml`, `tests/conftest.py`.
**Steps:** `pyproject.toml` with metadata, deps, `requires-python = ">=3.9,<3.10"` (RC-4), `[tool.pytest.ini_options]`. CI pinned to **3.9**; `pip install -e .`; `python -c "import main"`; `pytest -q`. Note for the human: enable branch protection so `main` requires green.
**Done when:** `pip install -e .` then `pytest` works without the path hack; workflow valid.

---

## P1 — This cycle

### T5 — Type the API (and respect the 3.9 trap)
**Goal:** Replace `body: dict` in all 8 mutating routes (`api_move`, `api_shift_range`, `api_unschedule`, `api_import`, `api_gear_post`, `api_run_gear`, `api_coach_apply`, `api_annotate`) with Pydantic request models.
**Why:** Two-for-one. You're paying FastAPI's cost without its validation benefit — *and* (RC-4) the untyped `dict` currently sidesteps a real 3.9 crash, because Pydantic never introspects it. Done wrong, this fix introduces the bug it's meant to prevent.
**Files:** `src/api/routes.py`, new `src/api/schemas.py`.
**Steps:** Define models; move manual `int()/str()` coercion and bounds checks (e.g. ±90-day shift) into field types/validators. **Critical: use `Optional[X]` / `Union[X, Y]` in every model — never `X | Y`** (3.9 evaluates these at class-definition time and will crash). Let FastAPI return 422 on bad input.
**Done when:** No handler takes a bare `dict`; invalid payloads return 422; `python -c "import src.api.schemas"` succeeds **on 3.9**; one valid + one invalid test per endpoint.
**Push back if:** a payload is genuinely free-form (`/api/import` metrics) — model the envelope, keep the inner blob `dict`, say so.

### T6 — Lock the runtime to 3.9.6 and defuse the landmines
**Goal:** Make 3.9 the single, enforced target and remove the `X | None` time bombs.
**Why:** `trends.py:59` and `schedule.py:86` only work by luck. Pinning + fixing makes the luck unnecessary.
**Files:** `src/services/trends.py`, `src/services/schedule.py`, `requirements.txt`, `pyproject.toml`, module headers claiming a version.
**Steps:** Convert the two `X | None` annotations to `Optional[X]`. Set `requires-python` to 3.9. Add dependency upper bounds (`fastapi>=0.111,<1.0`, etc.). (See also T-G7: mypy would have caught these.)
**Done when:** `grep -rn ' | None\| | str\| | int' src/ main.py` finds nothing outside string-deferred contexts; CI runs 3.9.

### T7 — Real schema migrations
**Goal:** Replace the ad-hoc `try: ALTER … except` + `kv['rpe_scale']` flag in `store.init()` with a versioned, ordered migration list.
**Why:** Migrations are idempotent-by-accident; the next one is increasingly risky on the DB holding your irreplaceable data.
**Files:** `store.py`.
**Steps:** Add a `schema_version`; ordered migration callables applied when `version < n`; move gear-v2 and RPE 1→10 into it. Keep additive philosophy.
**Done when:** Fresh DB and an old-schema DB both init correctly in tests; re-running `init()` is a no-op.
**Push back if:** a migration would lossily touch health data — flag first.

### T8 — Frontend XSS (verify, then escape)
**Goal:** Walk `app.js` (1,381 lines, not yet read this session), confirm the unescaped-interpolation claim, then add a central `escapeHTML()` at every user-text interpolation.
**Why:** Self-XSS today, real XSS the moment anyone else logs in. But the finding is **unverified** — confirm before changing.
**Files:** `app.js`, new frontend smoke test.
**Steps:** Audit `innerHTML`/template uses of user-controlled strings (notes, gear names, activity names). Add the helper; sweep. Add a smoke test (note containing `<img src=x onerror=…>` must render as text).
**Done when:** Payload renders inert; smoke test green in CI.

---

## P2 — Backlog
- **T9 Secret provenance:** after T0, run `git log --all -- client_secret.json .intervals_key`; rotate anything with a history hit. Push back to the human before rotating.
- **T10 Document precedence:** RC-2/RC-3 are now known — fold the data-source order and gcal layer into `ARCHITECTURE.md` as a checkpoint.
- **T11 Ops polish:** structured logging (request id, latency); timezone audit (server-local naive dates noted in the doc).

---

# Gold-standard hardening for this stage
*Stage = single-user, self-hosted always-on service on one Mac, accumulating irreplaceable personal data, riding an unofficial Garmin API. These are the practices a well-run team would consider table-stakes here — deliberately NOT enterprise overkill (no SSO/RBAC/multi-region).*

## Goal: protect my data (highest value — this is your stated priority)
- **G1 — 3-2-1, versioned backups.** Today's nightly `backup()` overwrites a single `timely-backup.db` in a Drive-synced folder. The flaw: a corrupted DB silently overwrites your only backup. Keep **rolling daily + weekly snapshots** (timestamped, retention e.g. 14 daily / 8 weekly) and ensure ≥1 copy lives **off the Mac**. *Quick win, biggest payoff.*
- **G2 — Verified restore drills.** A backup you've never restored isn't a backup. Add a `restore` / `verify-backup` command that loads the latest backup into a temp DB, runs `PRAGMA integrity_check`, and asserts row counts are sane. Schedule it monthly with a phone ping on failure (you already have ntfy).
- **G3 — Encryption at rest.** The DB is unencrypted health data on disk (the architecture doc admits this). Pragmatic standard at this stage: confirm **FileVault** is on, and encrypt the off-machine backup copy (`age`/`gpg`) since it leaves the encrypted disk.
- **G4 — One-command full export.** A `export-all` to plain JSON/CSV (runs, annotations, gear, wellness, reviews). Insurance against the app itself dying and a hedge against the unofficial-API risk — your proprietary data should never be trapped in a format only this app reads.

## Goal: easy + safe to update
- **G5 — Pre-commit hooks with secret scanning.** `pre-commit` running `gitleaks`/`detect-secrets` would have caught T0 automatically. Add formatter + linter here too so they run before every commit, not just in CI.
- **G6 — Auto-formatter + linter (`ruff` + `black`/`ruff format`).** Makes changes mechanical and diffs clean; removes style bikeshedding from reviews.
- **G7 — Static type checking (`mypy`) in CI.** Would have flagged the `X | None` landmines (RC-4/T6) and the `body: dict` blind spots before they shipped. High ROI given how many of tonight's findings are type-related. Pin it to 3.9 so it sees what prod sees.
- **G8 — Dependency lockfile (`pip-tools` → `requirements.lock`).** Reproducible installs; you can rebuild the exact environment after a Mac wipe instead of resolving fresh.
- **G9 — Task runner (`Makefile`/`justfile`).** `make run / test / backup / restore / deploy` — lowers the cost of every routine action and documents them in one place.

## Goal: well-built / catch problems fast
- **G10 — Crash visibility for background threads.** The backup loop and run-watcher run as daemon threads; an exception there dies silently. Wrap each so failures hit a **rotating log file** *and* a phone push, plus a daily "still alive" heartbeat. Right now the app could be half-dead and you'd find out late.
- **G11 — Garmin API contract canary.** The whole write path rides an undocumented API. Schedule your existing `golden`/`verify --deep` to run periodically and alert you the day Garmin changes their schema — instead of discovering it when an upload silently corrupts your calendar.
- **G12 — `/healthz` + uptime check.** A trivial health endpoint plus the existing launchd, with an alert if the service is unreachable. Closes the "is the always-on server actually on?" gap.

**If you only do four of these:** G1 (versioned off-machine backups), G2 (verified restore), G5 (pre-commit secret scanning), G7 (mypy in CI). Those four cover the two stated goals — protect data, update safely — and would have pre-empted tonight's two scariest findings (the uncommitted week of work and the exposed OAuth secret).

---

## Global definition of done (every ticket)
- Atomic commit, clear message. `pytest -q` green; `python -c "import main"` succeeds **on 3.9**. No Garmin invariant weakened. If a ticket's premise was wrong, leave a note instead of forcing it.

## Do NOT without explicit human sign-off
- Remove `.git/index.lock` before confirming no GUI/IDE git op is running.
- `--force` any git operation.
- Run `upload_garmin_workouts.py upload/delete/--force` against the live account.
- Rotate/move real secrets, or drop/rewrite tables holding the proprietary dataset.
