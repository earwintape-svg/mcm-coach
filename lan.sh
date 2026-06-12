#!/bin/bash
# MCM Coach server manager — make "run the LAN" a thing of the past.
#
#   ./lan.sh install     install + start the always-on background server
#                        (survives reboots, restarts if it crashes)
#   ./lan.sh url         print the phone URL (with key)
#   ./lan.sh status      is it running? + last log lines
#   ./lan.sh restart     bounce it (e.g. after shipping new code)
#   ./lan.sh uninstall   remove the background service
set -e
cd "$(dirname "$0")"

LABEL="com.earwin.mcmcoach"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/mcmcoach.log"
PY="$(command -v python3 || echo /usr/bin/python3)"
# macOS blocks background services from reading ~/Documents (TCC privacy),
# so the server runs from a synced copy in Application Support instead.
APPDIR="$HOME/Library/Application Support/MCMCoach"

sync_app() {
  mkdir -p "$APPDIR"
  cp coach.py plan.py builders.py upload_garmin_workouts.py store.py "$APPDIR/"
}

make_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>coach.py</string>
    <string>--lan</string>
    <string>--no-browser</string>
  </array>
  <key>WorkingDirectory</key><string>$APPDIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string>
  <key>TIMELY_BACKUP_DIR</key><string>$PWD</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
EOF
}

phone_url() {
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<mac-ip>')"
  KEY="$(cat "$HOME/.mcm_coach_key" 2>/dev/null || echo '<run install first>')"
  echo "Phone (same Wi-Fi):  http://$IP:8765/?key=$KEY"
  TSIP="$(command -v tailscale >/dev/null && tailscale ip -4 2>/dev/null | head -1 || true)"
  if [ -n "$TSIP" ]; then
    echo "Anywhere (Tailscale): http://$TSIP:8765/?key=$KEY"
  fi
  return 0
}

case "${1:-status}" in
  install)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    sleep 2   # let launchd finish tearing down before re-bootstrapping
    sync_app
    : > "$LOG" 2>/dev/null || true   # fresh log each install
    make_plist
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    sleep 2
    echo "✅ installed — the coach server now runs in the background, always."
    phone_url
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed."
    ;;
  notify-on)
    NPLIST="$HOME/Library/LaunchAgents/$LABEL.notify.plist"
    launchctl bootout "gui/$(id -u)/$LABEL.notify" 2>/dev/null || true
    sync_app
    cat > "$NPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL.notify</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>coach.py</string><string>notify</string></array>
  <key>WorkingDirectory</key><string>$APPDIR</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
  </array>
</dict></plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$NPLIST"
    WPLIST="$HOME/Library/LaunchAgents/$LABEL.weekly.plist"
    launchctl bootout "gui/$(id -u)/$LABEL.weekly" 2>/dev/null || true
    cat > "$WPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL.weekly</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>coach.py</string><string>notify</string><string>--weekly</string></array>
  <key>WorkingDirectory</key><string>$APPDIR</string>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
</dict></plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$WPLIST"
    echo "✅ notifications on: 7:30am briefing, 6:30pm nudge, Sunday 6pm week-in-review."
    ;;
  notify-off)
    launchctl bootout "gui/$(id -u)/$LABEL.notify" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/$LABEL.weekly" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$LABEL.notify.plist" "$HOME/Library/LaunchAgents/$LABEL.weekly.plist"
    echo "notifications off."
    ;;
  restart)
    sync_app
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    echo "restarted with latest code."
    ;;
  url)
    phone_url
    ;;
  status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "● launchd job loaded"
    else
      echo "○ not installed — ./lan.sh install"
    fi
    if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
      echo "● server listening on :8765"
    else
      echo "✗ NOT listening on :8765 — see log below"
    fi
    phone_url
    echo "--- recent log ---"
    tail -15 "$LOG" 2>/dev/null || echo "(no log yet)"
    ;;
  watch)
    # Leave this running in a Terminal tab: Claude drops a .ship_request
    # file with a commit message, and this ships it — tests, demo, push,
    # server restart — no copy/paste. Only ship.sh ever runs; the file
    # content is used solely as the commit message.
    echo "📡 autoship: watching for .ship_request (Ctrl+C to stop)"
    while true; do
      if [ -f ".ship_request" ]; then
        MSG="$(head -c 200 .ship_request)"
        rm -f .ship_request
        echo "──▶ ship requested: $MSG"
        ./ship.sh "$MSG" || echo "(ship failed — see above)"
        echo "──▶ watching again…"
      fi
      sleep 3
    done
    ;;
  *)
    echo "usage: ./lan.sh {install|url|status|restart|uninstall|notify-on|notify-off|watch}"
    ;;
esac
