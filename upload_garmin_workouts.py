#!/usr/bin/env python3
"""Garmin structured workout uploader — MCM 2026 plan.

Python 3.9 / garminconnect 0.2.8 compatible. Uses raw JSON via
client.connectapi() (typed models and upload_workout don't exist in 0.2.8).

Commands:
  upload   Idempotent sync: uploads missing workouts, skips existing,
           schedules dates. --force deletes and re-uploads everything.
  verify   Compare Garmin Connect against the local plan (--deep fetches
           each workout and checks structure).
  delete   Delete all plan workouts from Connect (backs up JSON first).
  export   Write every workout payload to JSON files (no network).
  golden   Diff a manually-created Connect workout's structure against ours.
  smoke    Upload one workout, wait while you check the watch, delete it.

Auth: GARMIN_EMAIL / GARMIN_PASSWORD env vars, else interactive prompt.
Tokens persisted to ~/.garmin_tokens for subsequent runs.
"""
import argparse
import getpass
import json
import os
import re
import sys
import time
from datetime import date, datetime

from plan import build_plan, PLAN_START
from builders import total_distance_m, MILE

TOKEN_DIR = os.path.expanduser("~/.garmin_tokens")
# Anchored: matches only "W<1-19> <plan day> ..." — never "Weekly Run",
# "Workout A", "Wednesday Hills", or other user-created workouts.
PLAN_NAME_RE = re.compile(r"^W(1[0-9]|[1-9]) (Mon|Tue|Thu|Fri|Sat) ")


def is_plan_name(name):
    """True only for OUR workout names. Runna also names workouts
    'W10 Sat Long Run - ...' — the ' - ' separator excludes them so
    sync/delete never touches Runna's workouts."""
    return bool(PLAN_NAME_RE.match(name)) and " - " not in name
RETRYABLE = {429, 500, 502, 503, 504}
MIN_INTERVAL = 0.5  # seconds between API calls — stay under rate limits
_last_call = [0.0]


# ------------------------------------------------------------------- auth

def get_client():
    from garminconnect import Garmin
    # 1. Try resuming persisted tokens (API varies across versions).
    try:
        client = Garmin()
        client.login(TOKEN_DIR)
        log("AUTH resumed session from %s" % TOKEN_DIR)
        return client
    except Exception:
        pass
    # 2. Fresh login.
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ")
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")
    client = Garmin(email, password)
    client.login()
    try:
        client.garth.dump(TOKEN_DIR)
        log("AUTH tokens saved to %s" % TOKEN_DIR)
    except Exception as e:
        log("AUTH warning: could not persist tokens (%s)" % e)
    return client


# ------------------------------------------------------- API with retries

def _http_status(exc):
    for attr in ("status", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None) or getattr(getattr(exc, "error", None), "response", None)
    code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\b(4\d\d|5\d\d)\b", str(exc))
    return int(m.group(1)) if m else None


def api(client, path, method="GET", payload=None, retries=4):
    """connectapi() with throttling + exponential backoff on 429/5xx."""
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            _last_call[0] = time.time()
            if payload is not None:
                return client.connectapi(path, method=method, json=payload)
            return client.connectapi(path, method=method)
        except Exception as e:
            status = _http_status(e)
            if status in RETRYABLE and attempt < retries - 1:
                backoff = (2 ** attempt) * 5
                log("RETRY %s %s (HTTP %s, waiting %ss)" % (method, path, status, backoff))
                time.sleep(backoff)
                continue
            raise


def list_remote(client):
    res = api(client, "/workout-service/workouts?start=0&limit=999") or []
    return {w["workoutName"]: w for w in res if "workoutName" in w}


def remote_plan_workouts(client):
    return {n: w for n, w in list_remote(client).items() if is_plan_name(n)}


# ---------------------------------------------------------------- logging

def log(msg):
    print(msg, flush=True)


def progress(i, n, msg):
    log("[%d/%d] %s" % (i, n, msg))


# --------------------------------------------------------------- commands

def cmd_upload(args):
    plan = build_plan(start=args.start_date)
    if args.single:
        plan = [p for p in plan if args.single.lower() in p["name"].lower()]
        if not plan:
            sys.exit("No workout matches --single %r" % args.single)

    if args.dry_run:
        for i, p in enumerate(plan, 1):
            progress(i, len(plan), "DRY-RUN %-28s %s  %.1f mi" %
                     (p["name"], p["date"], p["distance_mi"]))
        log("Dry run: %d workouts, %.0f total miles. Nothing uploaded." %
            (len(plan), sum(p["distance_mi"] for p in plan)))
        return

    client = get_client()
    remote = remote_plan_workouts(client)

    if args.force and remote:
        _backup(remote, "pre_force_backup")
        for name, w in remote.items():
            api(client, "/workout-service/workout/%s" % w["workoutId"], method="DELETE")
            log("DELETE %s" % name)
        remote = {}

    started, n = time.time(), len(plan)
    uploaded = skipped = 0
    for i, p in enumerate(plan, 1):
        if p["name"] in remote:
            progress(i, n, "SKIP   %s (already on Connect)" % p["name"])
            skipped += 1
            continue
        res = api(client, "/workout-service/workout", method="POST", payload=p["payload"])
        wid = (res or {}).get("workoutId")
        progress(i, n, "UPLOAD %s ... OK (id=%s)" % (p["name"], wid))
        uploaded += 1
        if args.schedule and wid:
            api(client, "/workout-service/schedule/%s" % wid, method="POST",
                payload={"date": p["date"].isoformat()})
            progress(i, n, "SCHEDULE %s -> %s" % (p["name"], p["date"]))
        done = uploaded
        if done:
            eta = (time.time() - started) / done * (n - i)
            if i < n:
                log("        ETA %dm%02ds" % (eta // 60, eta % 60))

    # Orphans: plan-pattern workouts on Connect that aren't in the local plan.
    local_names = {p["name"] for p in plan}
    orphans = [w for nm, w in remote.items() if nm not in local_names]
    if orphans and not args.single:
        log("\n%d orphaned plan workout(s) on Connect (not in local plan):" % len(orphans))
        for w in orphans:
            log("  - %s" % w["workoutName"])
        if args.yes or input("Delete them? [y/N] ").lower() == "y":
            for w in orphans:
                api(client, "/workout-service/workout/%s" % w["workoutId"], method="DELETE")
                log("DELETE %s" % w["workoutName"])

    log("\nDone: %d uploaded, %d skipped." % (uploaded, skipped))
    _verify(client, plan, deep=False)


def cmd_verify(args):
    plan = build_plan(start=args.start_date)
    client = get_client()
    _verify(client, plan, deep=args.deep)


def _verify(client, plan, deep):
    remote = remote_plan_workouts(client)
    local_names = {p["name"] for p in plan}
    missing = sorted(local_names - set(remote))
    extra = sorted(set(remote) - local_names)
    log("VERIFY: %d expected, %d on Connect, %d missing, %d unexpected."
        % (len(plan), len(remote), len(missing), len(extra)))
    for n in missing:
        log("  MISSING %s" % n)
    for n in extra:
        log("  EXTRA   %s" % n)
    if not deep:
        return not missing and not extra

    ok = True
    by_name = {p["name"]: p for p in plan}
    for i, (name, w) in enumerate(sorted(remote.items()), 1):
        full = api(client, "/workout-service/workout/%s" % w["workoutId"])
        problems = _deep_check(full, by_name.get(name))
        if problems:
            ok = False
            for pr in problems:
                progress(i, len(remote), "FAIL %s: %s" % (name, pr))
        else:
            progress(i, len(remote), "OK   %s" % name)
    return ok


def _walk_steps(steps):
    for s in steps or []:
        yield s
        if s.get("type") == "RepeatGroupDTO":
            for c in _walk_steps(s.get("workoutSteps")):
                yield c


def _deep_check(remote_wo, local):
    """NOTE: Garmin adds estimatedDurationInSecs server-side to every stored
    workout — that's fine. Bug 1 was about US sending it in the POST payload.
    What actually matters here: no step degraded to lap.button (Bug 7)."""
    problems = []
    if local is None:
        return ["no local counterpart"]
    for s in _walk_steps(remote_wo.get("workoutSegments", [{}])[0].get("workoutSteps")):
        ec = s.get("endCondition") or {}
        if ec.get("conditionTypeId") == 1 or ec.get("conditionTypeKey") == "lap.button":
            problems.append("step %s is lap.button (Bug 7!)" % s.get("stepOrder"))
    try:
        r_steps = remote_wo["workoutSegments"][0]["workoutSteps"]
        l_steps = local["payload"]["workoutSegments"][0]["workoutSteps"]
    except (KeyError, IndexError):
        return problems + ["bad segment structure"]
    if len(r_steps) != len(l_steps):
        problems.append("top-level step count %d != %d" % (len(r_steps), len(l_steps)))
    r_dist, l_dist = total_distance_m(r_steps), total_distance_m(l_steps)
    if abs(r_dist - l_dist) > 1.0:
        problems.append("distance %.0fm != %.0fm" % (r_dist, l_dist))
    return problems


def cmd_delete(args):
    client = get_client()
    remote = remote_plan_workouts(client)
    if not remote:
        log("No plan workouts on Connect.")
        return
    log("Will delete %d workouts:" % len(remote))
    for n in sorted(remote):
        log("  - %s" % n)
    _backup(remote, "pre_delete_backup")
    if not args.yes and input("Type 'delete' to confirm: ") != "delete":
        log("Aborted.")
        return
    for i, (name, w) in enumerate(sorted(remote.items()), 1):
        api(client, "/workout-service/workout/%s" % w["workoutId"], method="DELETE")
        progress(i, len(remote), "DELETE %s" % name)


def _backup(remote, prefix):
    path = "%s_%s.json" % (prefix, datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(path, "w") as f:
        json.dump(list(remote.values()), f, indent=2)
    log("Backup written: %s" % path)


def cmd_export(args):
    plan = build_plan(start=args.start_date)
    outdir = args.out or "workout_json"
    os.makedirs(outdir, exist_ok=True)
    for p in plan:
        fname = re.sub(r"[^A-Za-z0-9._-]+", "_", p["name"]) + ".json"
        with open(os.path.join(outdir, fname), "w") as f:
            json.dump(p["payload"], f, indent=2)
    log("Exported %d workout JSON files to %s/" % (len(plan), outdir))


def cmd_golden(args):
    """Compare a hand-made Connect workout's field structure with ours."""
    client = get_client()
    golden = api(client, "/workout-service/workout/%s" % args.id)
    sample = build_plan()[6]["payload"]  # a quality workout with repeats
    diffs = []
    _diff_keys(golden, sample, "", diffs)
    if not diffs:
        log("GOLDEN: no structural differences found.")
    for d in diffs:
        log("GOLDEN diff: %s" % d)


def _diff_keys(golden, ours, path, out, max_diffs=60):
    """Report fields Garmin uses that we don't send (extra server-side
    fields like ownerId/workoutId are expected and flagged as info only)."""
    if len(out) >= max_diffs:
        return
    if isinstance(golden, dict) and isinstance(ours, dict):
        for k in golden:
            p = "%s.%s" % (path, k) if path else k
            if k not in ours:
                out.append("missing in generated: %s" % p)
            else:
                _diff_keys(golden[k], ours[k], p, out)
        for k in ours:
            if k not in golden:
                out.append("extra in generated: %s.%s" % (path, k) if path else k)
    elif isinstance(golden, list) and isinstance(ours, list) and golden and ours:
        _diff_keys(golden[0], ours[0], path + "[0]", out)


def cmd_list(args):
    """Print every workout on Connect with its id (find Runna ids here)."""
    client = get_client()
    for name, w in sorted(list_remote(client).items()):
        log("%-14s %s" % (w.get("workoutId"), name))


def cmd_fetch(args):
    """Download one workout's JSON exactly as Garmin stored it."""
    client = get_client()
    data = api(client, "/workout-service/workout/%s" % args.id)
    out = args.out or ("workout_%s.json" % args.id)
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
    log("Saved %s (%s)" % (out, (data or {}).get("workoutName")))


def cmd_smoke(args):
    plan = build_plan(start=args.start_date)
    target = args.single or "W1 Mon"
    match = next((p for p in plan if target.lower() in p["name"].lower()), None)
    if not match:
        sys.exit("No workout matches %r" % target)
    payload = json.loads(json.dumps(match["payload"]))
    payload["workoutName"] = "SMOKE TEST — " + match["name"]
    client = get_client()
    # Clean up leftovers from earlier smoke runs.
    for name, w in list_remote(client).items():
        if name.startswith("SMOKE TEST"):
            api(client, "/workout-service/workout/%s" % w["workoutId"], method="DELETE")
            log("Removed old smoke workout: %s" % name)
    res = api(client, "/workout-service/workout", method="POST", payload=payload)
    wid = (res or {}).get("workoutId")
    log("Uploaded %r (id=%s)." % (payload["workoutName"], wid))
    log("Sync your watch, check the workout displays distance + pace correctly.")
    input("Press Enter to delete the smoke-test workout... ")
    api(client, "/workout-service/workout/%s" % wid, method="DELETE")
    log("Deleted. Smoke test complete.")


# ------------------------------------------------------------------- main

def parse_date(s):
    d = date.fromisoformat(s)
    if d.weekday() != 0:
        raise argparse.ArgumentTypeError("start date must be a Monday")
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--start-date", type=parse_date, default=PLAN_START,
                       help="plan start Monday (default %s)" % PLAN_START)

    p = sub.add_parser("upload", help="idempotent sync to Garmin Connect")
    common(p)
    p.add_argument("--dry-run", action="store_true", help="print, upload nothing")
    p.add_argument("--single", help="only workouts whose name contains this")
    p.add_argument("--force", action="store_true",
                   help="delete existing plan workouts first, re-upload all")
    p.add_argument("--no-schedule", dest="schedule", action="store_false",
                   help="don't schedule workouts on the calendar")
    p.add_argument("--yes", action="store_true", help="no confirmation prompts")
    p.set_defaults(func=cmd_upload, schedule=True)

    p = sub.add_parser("verify", help="compare Connect against local plan")
    common(p)
    p.add_argument("--deep", action="store_true",
                   help="fetch each workout and check structure/distance")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("delete", help="delete all plan workouts (with backup)")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("export", help="write workout JSON files locally")
    common(p)
    p.add_argument("--out", help="output directory (default workout_json/)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("list", help="list all Connect workouts with ids")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("fetch", help="download one workout's stored JSON")
    p.add_argument("--id", required=True, help="Connect workoutId")
    p.add_argument("--out", help="output file (default workout_<id>.json)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("golden", help="diff a hand-made workout vs generated")
    p.add_argument("--id", required=True, help="Connect workoutId to fetch")
    p.set_defaults(func=cmd_golden)

    p = sub.add_parser("smoke", help="upload 1 workout, verify on watch, delete")
    common(p)
    p.add_argument("--single", help="name fragment (default 'W1 Mon')")
    p.set_defaults(func=cmd_smoke)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
