"""Upload a bulk_events.json (from generate_intervalsicu_plan.py) to
intervals.icu via the Open API:

    POST https://intervals.icu/api/v1/athlete/{id}/events/bulk?upsert=true

Auth: HTTP Basic, username "API_KEY", password = your personal intervals.icu
API key (Settings -> Developer Settings -> generate). Set it as an
environment variable -- never pass it on the command line or paste it into
chat:

    export INTERVALS_API_KEY="paste your key here"

athlete id "0" means "the authenticated athlete" and is the default. Override
with --athlete-id or INTERVALS_ATHLETE_ID if you ever need a specific id.

upsert=true means events are matched/replaced by "external_id" (we set this
to "timely-w<week>-<day>" in builders_intervalsicu.py), so re-running this
script after editing plan.py is safe -- it updates existing calendar entries
instead of duplicating them.

Usage:
    export INTERVALS_API_KEY="..."
    python3 upload_intervalsicu.py [bulk_events.json]   # default: intervalsicu_plan/bulk_events.json

    # Preview without sending anything:
    python3 upload_intervalsicu.py --dry-run
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://intervals.icu/api/v1"


def post_bulk_events(athlete_id, api_key, events, upsert=True):
    url = "%s/athlete/%s/events/bulk?upsert=%s" % (
        API_BASE, athlete_id, "true" if upsert else "false")
    body = json.dumps(events).encode("utf-8")
    auth = base64.b64encode(("API_KEY:%s" % api_key).encode("ascii")).decode("ascii")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Basic %s" % auth)
    # Cloudflare (in front of intervals.icu) blocks the default
    # "Python-urllib/x.y" User-Agent with a 403 / "error code: 1010".
    # A normal-looking UA avoids that.
    req.add_header("User-Agent",
                   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

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


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bulk_file", nargs="?",
                         default="intervalsicu_plan/bulk_events.json")
    parser.add_argument("--athlete-id",
                         default=os.environ.get("INTERVALS_ATHLETE_ID", "0"),
                         help="default: 0 (the authenticated athlete) or $INTERVALS_ATHLETE_ID")
    parser.add_argument("--chunk-size", type=int, default=50,
                         help="events per POST (default 50)")
    parser.add_argument("--dry-run", action="store_true",
                         help="load and validate locally; don't call the API")
    args = parser.parse_args()

    with open(args.bulk_file) as f:
        events = json.load(f)
    print("Loaded %d events from %s" % (len(events), args.bulk_file))

    if args.dry_run:
        print("Dry run -- not sending. First event:")
        print(json.dumps(events[0], indent=2))
        return

    api_key = os.environ.get("INTERVALS_API_KEY")
    if not api_key:
        sys.exit("Set INTERVALS_API_KEY (Settings -> Developer Settings in intervals.icu).")

    for i in range(0, len(events), args.chunk_size):
        chunk = events[i:i + args.chunk_size]
        status, result = post_bulk_events(args.athlete_id, api_key, chunk)
        print("Events %d-%d -> HTTP %d" % (i, i + len(chunk) - 1, status))
        if status >= 300:
            print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)
            sys.exit(1)

    print("Done. Check your intervals.icu calendar, and your Suunto watch "
          "after its next sync (Settings -> 'Upload planned workouts' must "
          "be enabled).")


if __name__ == "__main__":
    main()
