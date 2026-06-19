"""List or delete intervals.icu calendar events via the Open API.

Useful for removing duplicate/test workout entries that can't be deleted
through the web UI.

Auth: same as upload_intervalsicu.py -- set INTERVALS_API_KEY (Settings ->
Developer Settings) as an environment variable, never paste it in chat.

Usage:
    export INTERVALS_API_KEY="..."

    # List WORKOUT events in a date range (inclusive):
    python3 manage_intervalsicu_events.py list 2026-06-14 2026-06-22

    # Delete one event by id (the "id" column from the list output):
    python3 manage_intervalsicu_events.py delete 12345678

Each listed event shows its date, id, external_id (events from
generate_intervalsicu_plan.py / upload_intervalsicu.py have external_id
"timely-w<week>-<day>"; manually-created test entries have external_id "-"),
and name -- use that to spot which ones are the leftover manual test entries
before deleting.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://intervals.icu/api/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _request(method, path, api_key):
    url = API_BASE + path
    auth = base64.b64encode(("API_KEY:%s" % api_key).encode("ascii")).decode("ascii")
    req = urllib.request.Request(url, method=method)
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


def list_events(athlete_id, api_key, oldest, newest):
    status, result = _request(
        "GET",
        "/athlete/%s/events?oldest=%s&newest=%s&category=WORKOUT" % (athlete_id, oldest, newest),
        api_key)
    if status >= 300:
        sys.exit("HTTP %d: %s" % (status, result))
    if not result:
        print("No events found.")
        return
    print("%-12s %-12s %-22s %s" % ("date", "id", "external_id", "name"))
    for e in result:
        print("%-12s %-12s %-22s %s" % (
            (e.get("start_date_local") or "")[:10],
            e.get("id"),
            e.get("external_id") or "-",
            e.get("name")))


def delete_event(athlete_id, api_key, event_id):
    status, result = _request("DELETE", "/athlete/%s/events/%s" % (athlete_id, event_id), api_key)
    if status >= 300:
        sys.exit("HTTP %d: %s" % (status, result))
    print("Deleted event %s" % event_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--athlete-id", default=os.environ.get("INTERVALS_ATHLETE_ID", "0"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list WORKOUT events in a date range")
    p_list.add_argument("oldest", help="YYYY-MM-DD")
    p_list.add_argument("newest", help="YYYY-MM-DD")

    p_del = sub.add_parser("delete", help="delete one event by id")
    p_del.add_argument("event_id")

    args = parser.parse_args()

    api_key = os.environ.get("INTERVALS_API_KEY")
    if not api_key:
        sys.exit("Set INTERVALS_API_KEY (Settings -> Developer Settings in intervals.icu).")

    if args.cmd == "list":
        list_events(args.athlete_id, api_key, args.oldest, args.newest)
    else:
        delete_event(args.athlete_id, api_key, args.event_id)


if __name__ == "__main__":
    main()
