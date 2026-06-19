# timely — Front-End Redesign: Action Items

**For:** the product engineer
**Source of truth:** `design/timely-design-critique.html` (the UX teardown) — **read it first.** This file turns that critique into an executable, prioritized plan and defines your lane.
**Scope:** mobile web PWA only. Every pattern here ships in mobile Safari today (backdrop-blur glass, soft-shadow cards, sticky headers, bottom sheets, CSS transitions, Vibration API for haptics). **Do not** add a framework, a build step, or a native dependency.

---

## Your lane (read before touching anything)
- **You own:** `app.js`, `ui.html`, and any new front-end asset files. These are yours for the duration of the redesign.
- **You do NOT touch:** `src/**`, `*.py`, `tests/**`, `pyproject.toml`, `.github/**`. That's the infra engineer's lane.
- **Work on your own branch in your own worktree** (`feat/redesign`) — see `AGENTS.md` › "Parallel agents." Never commit on `main`. Integrate via PR; CI must be green.
- **The API is a contract, not yours to change.** Code against the existing `/api/*` endpoints (list below). If a screen needs a backend change, **write a ticket in `product/`** and flag it — do not edit `src/`.

### The API surface you build against (do not change these shapes)
GET: `/api/data`, `/api/actuals`, `/api/wellness`, `/api/weather`, `/api/fitness_form`, `/api/fitness`, `/api/gear`, `/api/coach`, `/api/trends`, `/api/prs`, `/api/suggest_move`, `/api/other_activities`, `/api/review`, `/api/calendar_status`, `/api/run/{id}`
POST: `/api/move`, `/api/shift_range`, `/api/unschedule`, `/api/import`, `/api/gear`, `/api/run/{id}/gear`, `/api/coach/apply`, `/api/sync_calendar`, `/api/annotate`

> **Shared seam — coordinate, don't surprise:** the infra engineer is adding request validation (Pydantic) that will make bad payloads return **HTTP 422**, and is reworking error responses. The "humane errors" work below depends on the error *shape*. Agree the error envelope with them once (see P1.1) rather than each guessing.

---

## Priority — ship these three first (from the critique's own "if I could only ship three")

### P1.1 — Fix the save error + the rating moment (Screen 03). *Highest trust-per-hour.*
**Why:** The app currently shows `Save failed: [object Object]` to the runner at the exact moment they give you data — and may lose their note. This is the single biggest trust leak.
**Do:**
- Normalize every server error to a human string **at the fetch boundary** (one helper, used everywhere). `[object Object]` must be impossible by construction. Show "Couldn't save — tap to retry"; **preserve the user's input**; add an offline queue so a flaky connection never drops a log.
- Replace the 1–10 slider with **five large tappable effort segments** (Recovery → Max), selected-state + haptic tick (Vibration API). Coarse input, coarse control.
- Open the run sheet **to the rating moment** (analysis one swipe deeper), and celebrate the run first ("Nice — 5.0 mi · 9:26/mi · right on target") before asking for input.
- **Fold in eng ticket T8 here:** the fetch-boundary + render pass is also where user-text escaping lives. Add a central `escapeHTML()` and apply it to every interpolation of user content (notes, gear names, activity names). A note containing `<img src=x onerror=…>` must render as inert text. (T8 moves out of the engineering brief and becomes yours, since you're rewriting this code anyway.)

### P1.2 — Promote one hero on Today (Screen 01).
**Why:** Today is a stack of ~10 equal-weight bordered panels; "what's my run, am I ready?" is the 4th–5th card down.
**Do:** Make today's session a full-width elevated hero that owns the top. Demote readiness (RHR/sleep/Body Battery/form) to a glanceable strip. Unify "days to MCM" + "week mileage" into one quiet row (each shown **once** — kill the countdown/stat-tile duplication). One primary action ("Send to watch"). Collapse the `timely · Run on time · …` wordmark band into a slim glass bar that surfaces today's readiness state.

### P1.3 — Introduce the design system tokens (critique items A, B, C).
**Why:** The cheapest way to make the whole app feel designed instead of assembled; every screen inherits it.
**Do:**
- **A — Depth scale:** retire "every section = bordered box." Three elevation levels: base content, raised cards (soft shadow, no border), floating glass (blur, for headers/sheets).
- **B — Color with a job:** mint = your action / on-track; coral = coach voice; workout-type colors = small dots only; amber/red = warnings only. Apply everywhere.
- **C — Type scale:** six steps (Display, Title, Headline, Body, Caption, Micro). Replace the ~12 ad-hoc inline font sizes; ban inline sizes.

---

## Then — system + remaining screens

### P2.1 — Kill the per-tab hero (item D). One slim shared glass header; stats live where relevant (week progress on Today/Plan), not triplicated as a branding band on every tab.
### P2.2 — One effort component everywhere (item F). The same tactile RPE control on Today and in the run sheet — one component, with haptics + spring on select. Remove the duplicate band-button vs slider designs.
### P2.3 — Plan tab (Screen 02). List is the star: glass header with only the List/Month toggle; Vacation/Sync/refresh collapse to a "···" sheet; legend becomes contextual dots. Each week gets a header card with a mileage progress ring; planned-vs-actual becomes a clear status pill per row (not 12px on the far right). Reframe Month as a compact heat-strip overview.
### P2.4 — Activities tab (Screen 04). Lead with the fitness trajectory **as a picture** (VDOT/projection sparkline), headline number large, caveats behind a tap. Give PRs identity (hero the proudest, medal accent, recency badges, fix the `--faint` contrast fail). Gear gets a real mileage ring with a color shift near retirement. Keep on-target % and avg RPE front-and-center.

---

## Definition of done (every change)
- Renders correctly at **360–390px** (mobile Safari); thumb-reachable primary actions ≥ 44pt.
- No raw error object can reach the user; user input survives a failed save.
- Existing API calls still work unchanged; no backend files modified.
- The Python suite + CI still pass (you didn't break the served assets); add a front-end smoke check if one exists.
- Committed on `feat/redesign`, merged via PR with CI green.

## Don't
- Add frameworks/build steps/native deps. Change API request or response shapes. Edit `src/**` or tests. `git add -A`. Commit on `main`. Touch `archive/`.
