# timely — product roadmap

Benchmarked against Strava, Runna, Garmin Connect, and TrainingPeaks.
Filter for every item: does it change a runner's decision, or make a
daily-touch surface feel effortless? (No streak-shaming, no vanity metrics.)

## Now — ship before/during week 1 (small, high-polish)

**1. Real Home Screen app icon.** The phone icon is currently a page
screenshot — the single cheapest slickness win. Generate a PNG of the
chevron mark, serve as `apple-touch-icon`. *Effort: S.*

**2. Sunday "Week in review" push.** Spotify-Wrapped energy, weekly:
"Week 1: 26.0 of 26 mi · 5/5 runs · on target 4 times · VDOT 47.2 ↗".
One scheduled notify variant reading from the store. The habit-forming
surface every competitor has and we don't. *Effort: S.*

**3. Shoe mileage tracker.** We already collect shoes per run. Sum miles
per shoe, show in Activities, amber warning at 350mi, red at 450 —
injury prevention disguised as a feature. Strava charges for this.
*Effort: S.*

**4. Trends panel (Today tab, below report card).** Two sparklines we
already have data for: RHR 30-day trend (the overtraining early-warning)
and avg easy-run pace trend (fitness you can *see*). *Effort: M.*

## Next — weeks 2–6 (the brain)

**5. Weekly auto-review (rule-based).** Sunday evening: compare planned vs
ran; if a week was <70% complete, propose next week stays as-planned vs
absorbing; if 2+ quality days missed, suggest stepping the week back.
This is Runna's "adaptive plans" — rules cover 80% of it. *Effort: M.*

**6. Training-load guard (ACWR).** Acute:chronic mileage ratio chart with
the 0.8–1.3 safe band. We warn on planned ramps; this warns on *actual*
load including the runs you added. The injury-prevention metric with real
evidence behind it. *Effort: M.*

**7. Best efforts / PRs.** Detect fastest 1k/1mi/5k/10k from stored laps
and series; celebrate quietly in the run sheet ("fastest mile of the
build"). Strava's stickiest feature, minus the leaderboard. *Effort: M.*

**8. Data export.** One button: all runs + annotations + wellness as CSV.
The data-ownership story made tangible — also useful for the writeup.
*Effort: S.*

## Later — September/October (the payoff)

**9. AI coach.** Provider-agnostic endpoint (Claude Haiku / DeepSeek /
local Ollama via one env var). Context: plan + last 14 days of runs,
annotations, wellness. Surfaces: free-form Q&A ("exhausted, tempo today —
what do I do?") and the weekly review written in plain English. Build
once ~3 weeks of RPE annotations exist. *Effort: M, plus prompt tuning.*

**10. Race-day pacing card.** MCM-specific: goal splits with the course's
hills factored (slower 1–8, bank nothing, negative split after Hains
Point), printable/screenshot-able. The whole 19 weeks converge here.
*Effort: M.*

**11. Taper + race-week mode.** Days 10→0: the app's tone shifts — less
data, more reassurance, carb timing, gear checklist. Apps ignore the
psychological taper; it's all runners think about. *Effort: S–M.*

## Deliberately not building

Social/leaderboards (it's a coach, not a feed) · streaks (injury-bait) ·
calorie tracking (vanity) · map tiles (privacy) · native iOS app (the PWA
earns it only if push-from-cloud ever matters more than $0/month).

## Slickness debt (roll into any release)

Pull-to-refresh on the PWA · chip-move animation (150ms ease) · count-up
numbers on stats · cached last-view for instant opens · iPad layout check.
