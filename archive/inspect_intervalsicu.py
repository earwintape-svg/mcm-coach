"""Quick read-only inspection of intervals.icu wellness + activities data.

Use this to check what's already syncing from Suunto before/after a run --
no changes are made to your account.

Auth: same as the other scripts -- set INTERVALS_API_KEY (Settings ->
Developer Settings) as an environment variable, never paste it in chat.

Usage:
    export INTERVALS_API_KEY="..."

    # Last 7 days of wellness data (resting HR, sleep, HRV, etc.):
    python3 inspect_intervalsicu.py wellness

    # Recent activities (last 14 days by default):
    python3 inspect_intervalsicu.py activities

    # One activity's available stream types + a peek at the data:
    python3 inspect_intervalsicu.py activity <activity_id>

    # Watch mode: leave this running in a terminal tab. Claude can then
    # write a one-line request file (".intervals_request" -- contains one
    # of "wellness", "activities", or "activity <id>") into this folder and
    # read back ".intervals_response.txt", without you needing to copy any
    # output by hand. Only those three whitelisted commands are ever run --
    # the request file's content is never executed as a shell command.
    python3 inspect_intervalsicu.py watch
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

REQUEST_FILE = ".intervals_request"
RESPONSE_FILE = ".intervals_response.txt"

API_BASE = "https://intervals.icu/api/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _request(path, api_key):
    url = API_BASE + path
    auth = base64.b64encode(("API_KEY:%s" % api_key).encode("ascii")).decode("ascii")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic %s" % auth)
    req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def cmd_wellness(api_key, athlete_id, days):
    oldest = (date.today() - timedelta(days=days)).isoformat()
    newest = date.today().isoformat()
    status, result = _request(
        "/athlete/%s/wellness?oldest=%s&newest=%s" % (athlete_id, oldest, newest),
        api_key)
    if status >= 300:
        sys.exit("HTTP %d: %s" % (status, result))
    if not result:
        print("No wellness data found for %s..%s" % (oldest, newest))
        return
    for d in result:
        print(json.dumps(d, indent=2))


def cmd_activities(api_key, athlete_id, days):
    oldest = (date.today() - timedelta(days=days)).isoformat()
    newest = date.today().isoformat()
    status, result = _request(
        "/athlete/%s/activities?oldest=%s&newest=%s" % (athlete_id, oldest, newest),
        api_key)
    if status >= 300:
        sys.exit("HTTP %d: %s" % (status, result))
    if not result:
        print("No activities found for %s..%s" % (oldest, newest))
        return
    for a in result:
        print("%-12s %-10s id=%-14s %-20s dist=%sm time=%ss avg_hr=%s streams=%s" % (
            (a.get("start_date_local") or "")[:10],
            a.get("type"),
            a.get("id"),
            (a.get("name") or "")[:20],
            a.get("distance"),
            a.get("moving_time"),
            a.get("icu_average_hr") or a.get("average_heartrate"),
            a.get("stream_types")))


def cmd_activity(api_key, athlete_id, activity_id):
    status, result = _request("/activity/%s" % activity_id, api_key)
    if status >= 300:
        sys.exit("HTTP %d: %s" % (status, result))
    print(json.dumps(result, indent=2))
    print()
    status, streams = _request(
        "/activity/%s/streams.json?types=heartrate,latlng,distance,time,pace,altitude"
        % activity_id, api_key)
    if status >= 300:
        print("streams HTTP %d: %s" % (status, streams))
        return
    print("stream keys:", list(streams.keys()) if isinstance(streams, dict) else type(streams))
    if isinstance(streams, list):
        for s in streams:
            data = s.get("data") or []
            print("  %-12s n=%d sample=%s" % (s.get("type"), len(data), data[:5]))


def _run_command(line, api_key, athlete_id):
    """Run one whitelisted command, return its printed output as a string.
    `line` is plain text from a request file -- never passed to a shell."""
    args = line.split()
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        if not args:
            print("empty request")
        elif args[0] == "wellness":
            days = int(args[1]) if len(args) > 1 else 7
            cmd_wellness(api_key, athlete_id, days)
        elif args[0] == "activities":
            days = int(args[1]) if len(args) > 1 else 14
            cmd_activities(api_key, athlete_id, days)
        elif args[0] == "activity" and len(args) > 1:
            cmd_activity(api_key, athlete_id, args[1])
        else:
            print("unknown command: %r (expected wellness | activities | activity <id>)" % line)
    except SystemExit as e:
        print("error: %s" % e)
    except Exception as e:
        print("error: %s: %s" % (type(e).__name__, e))
    finally:
        sys.stdout = old_stdout
    return buf.getvalue()


def cmd_watch(api_key, athlete_id, poll_secs=2):
    print("Watching for %s in %s (Ctrl-C to stop)..." % (REQUEST_FILE, os.getcwd()))
    while True:
        if os.path.exists(REQUEST_FILE):
            try:
                with open(REQUEST_FILE) as f:
                    line = f.read().strip()
                os.remove(REQUEST_FILE)
            except OSError:
                time.sleep(poll_secs)
                continue
            print("[%s] running: %r" % (time.strftime("%H:%M:%S"), line))
            output = _run_command(line, api_key, athlete_id)
            with open(RESPONSE_FILE, "w") as f:
                f.write(output)
            print("[%s] wrote %s" % (time.strftime("%H:%M:%S"), RESPONSE_FILE))
        time.sleep(poll_secs)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    api_key = os.environ.get("INTERVALS_API_KEY")
    if not api_key:
        sys.exit("Set INTERVALS_API_KEY (Settings -> Developer Settings in intervals.icu).")
    athlete_id = os.environ.get("INTERVALS_ATHLETE_ID", "0")

    cmd = sys.argv[1]
    if cmd == "wellness":
        cmd_wellness(api_key, athlete_id, days=7)
    elif cmd == "activities":
        cmd_activities(api_key, athlete_id, days=14)
    elif cmd == "activity":
        if len(sys.argv) < 3:
            sys.exit("usage: inspect_intervalsicu.py activity <activity_id>")
        cmd_activity(api_key, athlete_id, sys.argv[2])
    elif cmd == "watch":
        cmd_watch(api_key, athlete_id)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
