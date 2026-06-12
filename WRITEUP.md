# I built my own Runna with AI — and found an undocumented Garmin bug doing it

**TL;DR:** I'm training for the Marine Corps Marathon (goal: sub-3:25). Instead of paying for a training app, I built one with AI as my pair programmer: 93 structured workouts uploaded to my Garmin watch, plus a personal coaching dashboard — drag-and-drop calendar, readiness signals, weather-aware briefings, and Runna-style run analysis — that I can open from my phone anywhere. Zero frameworks, one dependency, free hosting. [Live demo](https://earwintape-svg.github.io/mcm-coach/) · [Code](https://github.com/earwintape-svg/mcm-coach)

## The problem

Garmin Connect lets you build structured workouts one at a time, by hand, in a clunky web UI. My 19-week plan has 93 workouts — intervals, tempos, hill repeats, long runs with marathon-pace finishes. Apps like Runna solve this for ~$20/month, but I wanted full control over my plan and my data. So: build it.

## What got built

**The uploader.** A Python CLI that generates all 93 workouts as structured JSON — warmups, repeat blocks, pace bands, distance-based steps — and pushes them to Garmin Connect's API, scheduled on the right calendar dates. The plan itself is data, not code: one line per workout, so editing my training is editing a table. A 4,500-assertion test suite validates every payload offline before anything touches Garmin.

**The coach.** A local web app (Python stdlib only — no Flask, no React, no npm) with three surfaces: **Today** (week strip, workout card with estimated duration, live weather with heat-adjusted pace guidance, recovery signals), **Plan** (a month calendar where I drag workouts between days — synced to Garmin instantly, with undo — plus vacation mode that shifts whole date ranges with a preview), and **Activities** (every run vs. the plan: distance, pace verdicts, and a tap-in detail view with route trace, laps-vs-target bars, splits, and HR curves).

**The infrastructure.** The server runs as a macOS background service that survives reboots. A Tailscale tunnel makes it reachable from my phone anywhere — encrypted, nothing exposed to the public internet. One script (`./ship.sh`) runs tests, rebuilds the public demo, pushes to GitHub, and restarts the server. The portfolio demo is generated *from the real app's code* with a synthetic athlete, so it can never drift from the product — and never leaks my data or location.

## The bug that made it interesting

First upload: every workout displayed **"Run Until Lap Press"** instead of "Run 4.00 mi." The payloads looked correct. The API accepted them without complaint.

The debugging method is the part I'm proud of:

1. **Isolate** — a smoke-test command that uploads exactly one workout, checks it on the real watch, and deletes it.
2. **Get ground truth** — pull the *stored* JSON of a workout made by Runna (which displays correctly) straight from Garmin's API.
3. **Diff** — compare field by field against what we sent.

The diff showed it instantly: Runna sends `"conditionTypeId": 3` for distance. We sent `1` — because the reference table we'd been working from said 1 = distance. It doesn't. **1 = lap button.** Garmin's API trusts the numeric ID and silently ignores the human-readable key sitting right next to it, so `{"conditionTypeId": 1, "conditionTypeKey": "distance"}` is accepted, stored, and rendered as... lap press. No error, no warning. A regression test now pins the correct IDs forever.

Lesson: when an API misbehaves silently, stop reasoning about what you sent — go read what the server *stored*, ideally next to a known-good example.

## What AI was actually like as a collaborator

The honest version: AI wrote essentially all of the code, and that was the least interesting part. The leverage was in the loop — I described intent ("easy runs should show *no* target, like Runna does"), tested on real hardware, reported what I saw, and the AI turned observations into hypotheses, instrumentation, and fixes. It also pushed back: arguing me out of map tiles (privacy leak in a public demo), out of vanity metrics (calories, daily VO2max noise), and into boring-but-right things like timing-safe key comparison, input validation, and a `.gitignore` before the first push.

What it couldn't do: look at my watch. Every real bug was caught by a human holding hardware, and fixed by a machine reading diffs. That division of labor felt like the actual future of this stuff.

## The stack, because people ask

Python 3.9 (the one that ships with macOS), `garminconnect` + `garth` for the API session, and otherwise the standard library — including the web server. The frontend is one HTML page with hand-rolled SVG charts. The public demo is a static build on GitHub Pages. Total external dependencies: one. Total monthly cost: $0.

## What's next

Weather and readiness are in; VDOT fitness projection ("your 6×800 today implies a 3:21 marathon") is the next feature. And on October 25, the only metric that matters: 26.2 miles, hopefully in under 3 hours and 25 minutes.

---

*Built in a day of conversations. Not a professional developer — just stubborn about my training plan.*
