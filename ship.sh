#!/bin/bash
# Ship the MCM Coach: test → rebuild demo → commit → push.
# Usage:  ./ship.sh "what changed"
set -e
cd "$(dirname "$0")"

MSG="${1:-update}"

echo "▶ tests"
python3 -m pytest -q

echo "▶ rebuild demo"
python3 make_demo.py

echo "▶ commit + push"
# Named files only -- never `git add -A` (AGENTS.md hard rule: the working
# tree accumulates stray artifacts -- .fuse_hidden*, *.egg-info/, backups --
# that a blanket add would happily commit). This list went stale once
# already (silently, for a week, during the FastAPI migration) by omission
# rather than by this rule -- the fix is updating it when the top-level
# layout changes, not switching to -A.
git add main.py coach.py plan.py builders.py upload_garmin_workouts.py \
        store.py intervalsicu_read.py setup_gcal.py make_demo.py \
        src/ tests/ docs/ ui.html app.js \
        requirements.txt pyproject.toml .gitignore .env.example \
        README.md ARCHITECTURE.md \
        ship.sh lan.sh .github/
git commit -m "$MSG" || echo "(nothing to commit)"
git push

# restart the background server (re-syncs code copy) so the phone app updates
if launchctl print "gui/$(id -u)/com.earwin.mcmcoach" >/dev/null 2>&1; then
  ./lan.sh restart
  echo "▶ coach server restarted"
fi

echo "✅ shipped — live demo updates in ~2 min: https://earwintape-svg.github.io/mcm-coach/"
