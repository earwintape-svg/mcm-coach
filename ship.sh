#!/bin/bash
# Ship the MCM Coach: test → rebuild demo → commit → push.
# Usage:  ./ship.sh "what changed"
set -e
cd "$(dirname "$0")"

MSG="${1:-update}"

echo "▶ tests"
python3 test_upload_garmin.py | tail -1

echo "▶ rebuild demo"
python3 make_demo.py

echo "▶ commit + push"
git add builders.py plan.py upload_garmin_workouts.py test_upload_garmin.py \
        coach.py make_demo.py docs/ README.md .gitignore ship.sh lan.sh
git commit -m "$MSG" || echo "(nothing to commit)"
git push

# restart the background server (re-syncs code copy) so the phone app updates
if launchctl print "gui/$(id -u)/com.earwin.mcmcoach" >/dev/null 2>&1; then
  ./lan.sh restart
  echo "▶ coach server restarted"
fi

echo "✅ shipped — live demo updates in ~2 min: https://earwintape-svg.github.io/mcm-coach/"
