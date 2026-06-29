# timely — Redesign Build Plan (product engineer)

**For:** the **product engineer** (front-end).
**Supersedes:** `product/PR_PLAN.md` (rev. 2) — that doc assumed one full-stack dev and a formal PR gate; both are reconciled here. You can delete PR_PLAN.md.
**Sources:** `Timely_Product_Feasibility_Brief.docx`, `design/timely-design-critique.html`, `product/FRONTEND_REDESIGN_TASKS.md` (design-level "what/why" per screen — read it for detail; this doc is the sequenced build).

---

## Decisions — locked, build to these

| # | Decision | Resolution |
|---|----------|------------|
| A | Finish time = THE hero metric | **Yes.** Leads on Today and Activities. |
| B | Projection trust | **Rolling 14-run average + a tight range** (e.g. "3:22–3:24"); raw per-run VDOT stays visible on tap. **This is backend math (PR-5, fast-follow).** In v1, the hero shows the *existing* live projection as a clean point estimate — labeled honestly, no range yet. |
| C | Timelapse history | Recompute + store. Snapshot capture (PR-0) is infra, starting now. |
| D | Readiness proactivity | Recommend + explain, conservative thresholds. **Deferred to fast-follow (PR-6).** v1 readiness sheets are informational only. |
| E | Missing data | Show "—" + a one-line reason ("no sync in 6h"). Never blank-crash, never fabricate. |
| F | Notifications | Already exist (`notify.py`). No work. |
| G | Health-data sensitivity | No concern (personal app/machine). |

---

## Your lane & the split (you have a teammate now)

- **You (product engineer):** front-end only — `app.js`, `ui.html`, new FE assets. Build against the **existing `/api/*` contract**; do not edit `src/**` or `store.py`. If a screen needs a new/changed endpoint, file it for the infra engineer — don't build it yourself.
- **Infra engineer:** the backend PRs — **PR-0** (projection-snapshot write, starting now so data accrues for the timelapse) and, in fast-follow, the backend halves of **PR-5/PR-6**.
- **Shared seam:** PR-1's error-envelope shape depends on the infra engineer's Pydantic 422 work — agree the error JSON once before you merge PR-1 (not before you start it).

## Workflow (trunk-based, per AGENTS.md §6 — no formal PR)

The infra engineer has finished its backlog, so there's **no concurrent editing — work directly on `main`** (trunk-based). Skip the worktree. (Only if infra becomes active again: `git worktree add ../timely-redesign feat/redesign`.)

`pre-commit` (secret-scan + ruff + mypy) and `pre-push` (tests) are your gates. Before each PR, **tag `pre-prN`** so a single `git checkout <tag> -- app.js ui.html` rolls back. Before PR-3 specifically, also copy `ui.html`→`ui-backup.html` and `app.js`→`app-backup.js` (this redesign ships mid-training-cycle; a <30s restore matters).

---

## v1 — build these, in this order

### PR-1 · Save-error fix + unified tactile RPE  *(critique P1.1 — highest trust-per-hour)*
- One fetch-boundary helper that normalizes any server error to a human string — `[object Object]` impossible by construction. **Note the API returns two error shapes:** `{"detail": "..."}` (a string) for raised 4xx errors, but `{"detail": [{"loc":…, "msg":…}, …]}` (an array of objects) for FastAPI 422 validation failures — the array is the actual source of `[object Object]`. The helper must collapse **both** to a readable line (for 422, surface the first `msg`). Show "Couldn't save — tap to retry," **preserve the note + RPE on failure**, retry once after ~3s (minimal offline queue).
- Replace the 1–10 slider *and* the Today band-buttons with **one** 5-segment tactile RPE component (Vibration API haptic on select), used in both the Today card and the run-log sheet.
- Apply `escapeHTML()` (T8) to every interpolation this PR touches.
- **Acceptance:** forced 500/422 never renders an object; a note survives a killed network (single write, not duplicate — test reconnect); same RPE component in both places; `<img src=x onerror=…>` renders inert.
- **Risk:** medium — it's the path every run-log depends on. Depends on nothing (agree error shape with infra before merge).

### PR-3 · Today hero + design tokens + readiness sheets  *(critique P1.2/P1.3/P2.1; decisions A, E — the centerpiece)*
- **Tokens (A/B/C):** depth scale (base / raised card / floating glass), color semantics (mint = action/on-track, coral = coach voice, workout colors = dots only), 6-step type scale. Extract them from building this screen; ban inline font-sizes.
- **Today hero:** collapse the ~10-panel stack into one elevated full-width session card + a slim readiness strip (RHR/Sleep/Form/Battery tiles) + **projected finish as the lead number** (v1 = existing live point estimate; rolling-avg+range comes with PR-5) + one unified countdown/week-mileage row, **each stat shown once**. Wordmark band → slim glass header. One primary CTA ("Send to watch").
- **Readiness sheets:** tapping a tile opens value + 7-day baseline + trend sparkline + one plain-English line. Informational only in v1 (nudges = PR-6). Missing data → "—" + reason (decision E).
- **Acceptance:** "what's my run, am I ready?" answered with no scroll on 375px; no stat appears twice; one CTA; every tile taps through; missing data degrades gracefully; no inline font-sizes outside the tokens. Tag `pre-pr3` + back up the two files before merge.
- **Risk:** medium-high (biggest visual change). Keep the diff isolated to Today-tab render functions. Depends on PR-1.

### PR-4 · Plan tab — list-first, week hero, day-row detail  *(critique P2.3)*
- List = default and only fully-editable view. Control bar → List/Month toggle only; Vacation/Sync/refresh into a "···" sheet. Active week gets a header card + mileage progress bar. Each day row gets a legible status pill (replacing the 12px far-right check) and taps into the existing workout-detail sheet. Month → compact heat-strip, not an editing surface.
- Don't rewrite List's planned-vs-actual logic ("the soul of the app") — only its chrome. Document the Garmin pace-only limitation inline in the send confirmation.
- **Acceptance:** Month never truncates; status pill legible without zoom; drag-to-move still works on pointer:fine; tap-to-move unaffected on touch. Depends on PR-3 (tokens).
- **Risk:** low-medium (mostly CSS reorg of a correct data model).

### PR-4a · Activities tab + shared header cleanup  *(critique P2.4/P2.1/P2.2)*
- **Activities:** lead with a fitness-trajectory card (VDOT/projection sparkline, big headline, caveats behind a tap); hero the proudest PR + recency badges + fix the `--faint` contrast fail; gear gets a real mileage ring with color shift near retirement (check ROADMAP item 3 didn't already ship this); keep on-target % and avg RPE prominent.
- **Cleanup:** one slim shared glass header across all three tabs (kill the per-tab wordmark/stat repetition); grep out and delete the old slider/band-button markup so PR-1's RPE component is the only effort UI left.
- **Acceptance:** PR tiles distinguish best-mile vs best-5K on thin data; zero old per-tab hero markup; one header component; `app.js` line count drops. Depends on PR-3 + PR-4.
- **Risk:** low (additive + deletion).

---

## Not in v1

- **PR-0 (infra, starting now):** projection-snapshot write — not your lane, but it's why the timelapse will have exact data later.
- **PR-5 (fast-follow):** finish-time timelapse + the rolling-14 average + range. Backend helper + new GET endpoint (infra) + your scrubbable chart. The `design/timely-redesign-demo.html` chart is the spec.
- **PR-6 (fast-follow):** proactive readiness nudges (decision D). Backend rules (infra) + your dismissible coaching card.

Ship v1, train on it for a week, then pull these in based on what actually felt missing.

---

## Definition of done (every PR)
375–390px clean, primary actions ≥44pt; no raw error object reaches the user; input survives a failed save; existing API shapes unchanged unless the PR says otherwise; `pytest -q` green and `python -c "import main"` on 3.9; no Garmin schema invariant weakened; `pre-prN` tag created before merge.

## Don't
Add frameworks/build steps/native deps; change API request/response shapes; edit `src/**` or `store.py`; `git add -A`; touch `archive/`.

---

## Build checklist (tick as you go)

**Setup**
- [ ] `git worktree add ../timely-redesign feat/redesign`
- [ ] Confirm `pre-commit` + `pre-push` run (`git config core.hooksPath` → `hooks`)
- [ ] Agree the error-envelope JSON shape with the infra engineer (needed for PR-1)

**PR-1 — save-error fix + tactile RPE**
- [ ] Fetch-boundary helper normalizes every server error to a human string
- [ ] Failed save preserves the note + RPE; retry once after ~3s, then "tap to retry"
- [ ] One 5-segment tactile RPE component (haptic) replaces the slider *and* the band-buttons
- [ ] Same RPE component renders in Today card + run-log sheet
- [ ] `escapeHTML()` applied to every interpolation this PR touches
- [ ] Verify: forced 500/422 shows no object; killed-network note survives as a single write; `<img src=x onerror=…>` renders inert
- [ ] `pre-pr1` tag → merge to `main` on green

**PR-3 — Today hero + tokens + readiness sheets** *(back up `ui.html`/`app.js` first)*
- [ ] Tokens: depth scale, color semantics (mint/coral/dots), 6-step type scale; no inline font-sizes
- [ ] Hero: one full-width session card + readiness strip + projected-finish lead number (live point estimate) + single countdown/mileage row (each stat once) + slim glass header + one CTA
- [ ] Readiness tiles tap into sheet (value + 7-day baseline + sparkline + plain-English line); informational only
- [ ] Missing data → "—" + reason (never blank/fabricated)
- [ ] Verify on 375px: "what's my run, am I ready?" with no scroll; no stat twice; every tile taps through
- [ ] `pre-pr3` tag → merge on green

**PR-4 — Plan tab (list-first)**
- [ ] Control bar → List/Month toggle only; Vacation/Sync/refresh into "···" sheet
- [ ] Active week header card + mileage progress bar; each day row → legible status pill → workout-detail sheet
- [ ] Month reframed as compact heat-strip; List logic untouched (chrome only)
- [ ] Verify: Month never truncates; pill legible without zoom; drag (pointer:fine) + tap-to-move both still work
- [ ] `pre-pr4` tag → merge on green

**PR-4a — Activities + shared header**
- [ ] Fitness-trajectory card leads (sparkline + big headline + caveats on tap); proudest PR hero'd + recency badges; fix `--faint` contrast; gear mileage ring with retirement color shift
- [ ] One shared glass header across all three tabs; delete old per-tab wordmark/stat markup
- [ ] Grep out and delete the old slider/band-button markup (PR-1's component is the only effort UI)
- [ ] Verify: PR tiles distinguish best-mile vs best-5K; zero old hero markup; `app.js` line count drops
- [ ] `pre-pr4a` tag → merge on green

**After v1 lands**
- [ ] Train on it ~1 week, then decide whether to pull in PR-5 (timelapse + rolling-avg/range) and PR-6 (readiness nudges) with the infra engineer
