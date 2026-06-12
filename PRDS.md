# timely — PRD pack (Now + Next tiers)

Companion to ROADMAP.md. Each PRD: problem → stories → requirements →
data → design spec (mobile/desktop) → acceptance criteria. ERD changes and
the responsive engineering standard are at the end.

---

## PRD 1 — Home Screen app icon

**Problem.** iOS uses a page screenshot as the icon for Add-to-Home-Screen
apps unless an `apple-touch-icon` PNG is served. Our icon looks broken;
every competitor's looks like a product.

**Stories.** As a user I see the chevron mark on my Home Screen so the app
feels like an app.

**Requirements.**
- Serve `/apple-touch-icon.png` (180×180) from coach.py: the negative-split
  chevrons on Asphalt #101418, 36px corner-radius safe (iOS masks its own).
- Generate the PNG at build time with stdlib only (zlib-crafted PNG from the
  SVG geometry — no Pillow dependency) or check in a pre-rendered asset.
- `<link rel="apple-touch-icon">` in PAGE; demo gets the same.

**Acceptance.** Delete + re-add to Home Screen → chevron icon, both on the
LAN URL and the Tailscale URL. Lighthouse PWA icon audit passes.

---

## PRD 2 — Sunday "Week in review"

**Problem.** The week ends silently. Every habit-forming product has a
weekly moment (Spotify Wrapped, Strava weekly recap); ours is a report card
you must remember to open.

**Stories.**
- As a runner, Sunday 6pm I get a push: my week in one sentence.
- Tapping into the app shows a review card I can screenshot.

**Requirements.**
- New notify variant `coach.py notify --weekly` scheduled Sun 18:00 via the
  existing notify LaunchAgent (add a third StartCalendarInterval; weekday
  guard in code).
- Message format: "Week N: X of Y mi · A/B runs · on target C× · VDOT V ↗/↘".
  Sources: store.runs, plan weekly targets, fitness endpoint, annotations.
- In-app: review card pinned at top of Today on Sundays/Mondays — same
  stats plus best run of the week and one rule-based coaching line
  ("easy days drifted 20s fast — protect them").
- Persist each review to `weekly_reviews` so history accrues (and the AI
  coach later reads them).

**Edge cases.** Zero-run week → supportive copy, never shame. Race week →
taper-aware copy.

**Acceptance.** Sunday push arrives on phone via ntfy; card renders with
real week-1 data; review row exists in DB.

---

## PRD 3 — Shoe mileage tracker

**Problem.** Shoes die at ~400mi and dead shoes cause injuries. We already
collect shoes per run; the data is unread.

**Stories.**
- As a runner I see miles per shoe and get warned before they're spent.
- I can rename/retire a shoe.

**Requirements.**
- Aggregate: sum run.mi grouped by annotations.shoes (case-folded).
- New `gear` table for metadata: display name, starting miles (for shoes
  not new at first log), retired flag, threshold (default 400).
- Activities tab: "Gear" section — one row per active shoe: name, total mi,
  progress bar; amber ≥350, red ≥450; tap → rename / set starting miles /
  retire.
- Today card: if today's planned workout is quality and the last-used shoe
  is red, one-line nudge ("the Pegasus are at 462mi — race shoes?").

**Acceptance.** Logging shoes on 2 runs shows aggregated row; thresholds
color correctly; retired shoes drop out of nudges but keep history.

---

## PRD 4 — Trends panel

**Problem.** The store holds 19 weeks of wellness + pace and shows none of
it over time. Fitness should be visible, not inferable.

**Stories.** As a runner I glance at two sparklines: resting HR drifting
down (adaptation) or up (overtraining/illness), and easy-run pace drifting
down at equal effort (fitness).

**Requirements.**
- `/api/trends`: RHR daily series (30d) from wellness; easy-pace series =
  runs matched to no-target workouts, weekly median paceSec; VDOT weekly
  best (already in /api/fitness).
- Today tab, below report card: "Trends" panel, two sparklines (SVG, same
  lineSvg helper), current value + 30-day delta chip (▼3 bpm = green).
- Empty state until ≥7 days of data: "trends unlock after week 1."

**Acceptance.** With seeded wellness rows the sparklines render on both
breakpoints; deltas compute against the trailing window, colors encode
good/bad direction per metric (RHR down = good; pace down = good).

---

## PRD 5 — Weekly auto-review (adaptive plan, rules tier)

**Problem.** Plans assume perfect weeks. Reality needs a Sunday decision:
keep next week, or adjust. Today that's manual via per-workout Replan.

**Requirements.**
- Sunday review computes completion: % planned miles run, quality sessions
  hit/missed.
- Rules: ≥85% complete → "as planned". 60–85% → propose absorbing misses
  (no changes) with one-tap confirm. <60% or 2+ quality missed → propose
  repeating the week's structure at −10% volume (generate moves via the
  vacation engine against next week's dates), preview before apply.
- All proposals are previews — nothing mutates without a tap (same pattern
  as vacation mode). Every applied change logs to schedule_events.

**Acceptance.** Simulated 50%-complete week generates a coherent repeat-week
proposal with zero hard-day adjacencies; applying it moves/unschedules
correctly on Garmin.

---

## PRD 6 — Training-load guard (ACWR)

**Problem.** Ramp warnings currently use *planned* miles; injuries come
from *actual* load (including unplanned runs).

**Requirements.**
- ACWR = acute (7-day actual mi) / chronic (28-day daily-avg × 7).
  Computed from store.runs; needs ≥3 weeks of history before showing.
- Plan tab, under the mileage chart: ACWR line with shaded 0.8–1.3 band.
- Banner on Today only when ACWR > 1.4: "load is 45% above your month —
  the next hard day is the risky one" (amber; never blocks).

**Acceptance.** Synthetic run history producing ACWR 1.5 triggers banner;
0.9–1.3 stays silent.

---

## PRD 7 — Best efforts (PRs)

**Problem.** A breakthrough mile inside a tempo run currently looks like
any other lap.

**Requirements.**
- On run-detail fetch (already cached), compute best rolling 1k/1mi/5k from
  the distance/pace series; compare to `best_efforts` table; insert when
  beaten (keep full history, one row per record).
- Run sheet: quiet gold line when a PR happened ("fastest mile of the
  build — 7:31"). Activities: small ★ on PR runs. No confetti.

**Acceptance.** Two synthetic runs where the second beats the first's best
mile → second run shows PR line, table holds both rows, first run unstarred.

---

## PRD 8 — Data export

**Problem.** "Your data is yours" should be a button, not a promise.

**Requirements.** `/api/export` streams a zip: runs.csv, laps.csv,
wellness.csv, annotations.csv, weekly_reviews.csv. Button in Activities
footer. Works offline (store-only). Filenames dated.

**Acceptance.** Export opens in Numbers/Sheets with sane headers; row
counts match DB.

---

## ERD — schema additions (v2)

Existing hubs unchanged (activity_id ↔ runs/raw/details/annotations;
date ↔ wellness/weather/external). New:

```
gear(
  shoe_key TEXT PRIMARY KEY,      -- case-folded shoes string from annotations
  display TEXT, start_mi REAL DEFAULT 0,
  threshold_mi REAL DEFAULT 400, retired INTEGER DEFAULT 0)

best_efforts(
  distance_key TEXT,              -- '1k' | '1mi' | '5k'
  activity_id TEXT,               -- FK → runs.activity_id
  sec INTEGER, date TEXT,
  PRIMARY KEY(distance_key, activity_id))

weekly_reviews(
  week INTEGER PRIMARY KEY,       -- plan week 1–19
  json TEXT, created_at REAL)
```

Join rules hold: gear joins runs *through* annotations.shoes; best_efforts
carries activity_id; weekly_reviews keys on plan week (derivable from any
date). No table ever widens for a new feature; new sources still land in
external_metrics.

---

## Responsive engineering standard

Principles (in priority order), replacing "one 700px media query" as we go:

1. **Mobile-first CSS.** Base styles are the phone layout; `@media
   (min-width:…)` adds, never subtracts. Our current desktop-first block
   gets inverted opportunistically when touched.
2. **Content-driven breakpoints, few of them.** One major breakpoint
   (~720px) plus grid auto-flow. Breakpoints exist where the *content*
   breaks, not at device names.
3. **CSS Grid + `repeat(auto-fit, minmax(Xpx, 1fr))`** for every card row
   (stats, gear, trends) — zero media queries needed for column count.
4. **Container queries** (`@container`) for components that appear in
   different-width contexts (trend cards in Today vs a future dashboard) —
   broadly supported since 2023; the modern correct tool.
5. **Fluid type and spacing with `clamp()`** — e.g. hero title
   `clamp(19px, 4vw, 26px)` — kills half the font-size overrides.
6. **Touch vs pointer affordances via `@media (hover:hover)` and
   `(pointer:coarse)`**, not width: drag-and-drop enables on pointer:fine;
   tap-to-move on coarse — width is a proxy, capability is the truth.
7. **Safe areas and dynamic viewport**: `env(safe-area-inset-*)` (already
   in) and `dvh` instead of `vh` for full-height surfaces so iOS toolbars
   don't cause jumps.
8. **`prefers-reduced-motion`** guard on all new animation (chip moves,
   count-ups).
9. **Performance budget**: one HTML file, zero blocking requests, SVG-only
   graphics — the reason the app opens in <100ms stays a feature.
