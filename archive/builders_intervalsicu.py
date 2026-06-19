"""intervals.icu workout builders -- prototype.

Converts the workout-step tree produced by builders.py (Garmin Connect
schema: step()/repeat() dicts) into intervals.icu's plain-text workout
description syntax, plus a calendar-event dict ready for the bulk upsert
endpoint:

    POST https://intervals.icu/api/v1/athlete/0/events/bulk?upsert=true

This is a converter, not a new plan representation: plan.py and builders.py
are untouched. Feed it the same Workout(suffix, steps) that build_plan()
consumes for Garmin, plus a date, and it produces an event dict (and the
description text inside it) ready to POST.

Why intervals.icu and not Suunto directly: Suunto's own Cloud/Guide API
requires partner-program registration ("not for personal use" per their
FAQ). intervals.icu is free, has a self-serve personal API key (Settings ->
Developer), and -- because *they* are a registered Suunto partner -- can
push your planned workouts to a connected Suunto watch automatically as
SuuntoPlus Guides (Settings -> "Upload planned workouts"). So: this plan ->
intervals.icu calendar -> (intervals.icu's existing Suunto integration) ->
watch. Same trick also re-feeds Garmin via intervals.icu if ever useful.

Workout text syntax (per intervals.icu's Workout Builder Syntax Quick
Guide, forum.intervals.icu/t/workout-builder-syntax-quick-guide/123701):
  * One line per step: "- [cue text] [duration or distance] [target]"
  * Duration: "30s", "5m", "1h2m30s" -- NOTE "m" means MINUTES, not meters.
  * Distance: "800mtr", "2km", "1.5mi" -- meters use "mtr", never "m".
  * Absolute pace target, range form: "<faster>/mi-<slower>/mi Pace"
    (smaller mm:ss = faster pace; matches their "3:00/100m-4:00/100m Pace"
    example, which is fast-to-slow).
  * Repeat blocks: a standalone "Nx" line, blank line before and after,
    followed by the repeated steps. No nested repeats (none of our
    repeat() calls nest, so this always holds).
  * NO_TARGET steps: just cue text + duration/distance, no target token.

Key mapping decisions:
  * pace.zone targets (targetValueOne=slower m/s, targetValueTwo=faster
    m/s, per builders.py's speed_target) -> "<faster pace>/mi-<slower
    pace>/mi Pace", converting m/s to mm:ss per mile (MILE = 1609.34 m,
    same constant as builders.py).
  * distance_m always emitted as "<round(meters)>mtr" -- exact, no
    mile-rounding artifacts, and "mtr" is accepted regardless of whether
    the original figure was specified in miles or meters.
  * seconds emitted as "<secs>s" (all of this plan's time-based steps are
    under a minute, so no "m"/minutes ambiguity in practice).
  * step "description" (e.g. "Warmup", "On pace, controlled") becomes the
    step's cue text, same role as the SuuntoPlus notification text in
    builders_suunto.py.
  * sport type -> "Run" (intervals.icu's Strava-style type string).

Open items / unverified against the real API:
  * Whether intervals.icu auto-computes moving_time / icu_training_load
    from "description", or whether those should be supplied explicitly.
    Forum examples show both; omitted here and left to the server.
  * Exact auth header format for the personal API key ("Authorization:
    ApiKey API_KEY:<key>", per intervals.icu's Open API docs) -- this
    module only builds payloads, it does not call the API.
  * Whether intervals.icu's Suunto sync ("Upload planned workouts") only
    looks ~1 week ahead, which would mean re-running the upload
    periodically rather than a single 93-event bulk push far in advance.
  * Cue text containing em dashes / long descriptions -- assumed fine as
    free text but not yet verified against the real parser.

Confirmed against the real API (2026-06-14):
  * A description consisting ONLY of step lines (starting with "- ") makes
    intervals.icu's Suunto export produce an empty "guide.steps" array,
    which Suunto's API then rejects with 400 "Invalid 'guide.steps':
    collection has less items than the allowed minimum (1)". This matches
    a long-standing bug reported on the intervals.icu forum (2022 and
    2024 threads, "guide.steps ... less than the allowed minimum (1)").
  * Fix: prepend a free-text title paragraph (the workout name) before the
    first step line, separated by a blank line -- matching intervals.icu's
    own documented working example. build_event() now does this
    automatically. Verified to fix the Suunto upload for a 6x800 workout
    with pace.zone targets and a repeat block.
"""
import json

from builders import MILE

SPORT_RUN = "Run"

# ------------------------------------------------------------- conversions


def _seconds_str(secs):
    """seconds -> intervals.icu duration token, e.g. 60 -> '60s'."""
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append("%dh" % h)
    if m:
        parts.append("%dm" % m)
    if s or not parts:
        parts.append("%ds" % s)
    return "".join(parts)


def _meters_str(meters):
    """meters -> intervals.icu distance token, e.g. 800.0 -> '800mtr'."""
    return "%dmtr" % round(meters)


def _mps_to_pace_str(mps):
    """m/s -> 'm:ss' pace per mile."""
    secs_per_mile = MILE / mps
    m = int(secs_per_mile // 60)
    s = int(round(secs_per_mile - m * 60))
    if s == 60:
        m += 1
        s = 0
    return "%d:%02d" % (m, s)

# ------------------------------------------------------------ step builders


def _amount_token(s):
    cond = s["endCondition"]["conditionTypeKey"]
    if cond == "distance":
        return _meters_str(s["endConditionValue"])
    return _seconds_str(s["endConditionValue"])


def _target_token(s):
    """pace.zone -> '<faster>/mi-<slower>/mi Pace'. NO_TARGET -> None."""
    tgt = s.get("targetType") or {}
    if tgt.get("workoutTargetTypeKey") == "pace.zone":
        v1, v2 = s.get("targetValueOne"), s.get("targetValueTwo")
        if v1 is not None and v2 is not None:
            faster, slower = _mps_to_pace_str(v2), _mps_to_pace_str(v1)
            return "%s/mi-%s/mi Pace" % (faster, slower)
    return None


def _step_line(s):
    """ExecutableStepDTO -> one '- ...' line of workout text."""
    desc = (s.get("description") or "").strip()
    amount = _amount_token(s)
    target = _target_token(s)
    parts = []
    if desc:
        parts.append(desc)
    parts.append(amount)
    if target:
        parts.append(target)
    return "- " + " ".join(parts)


def _convert_steps(steps):
    """Walk the Garmin step tree -> list of text blocks (each block is a
    list of lines; blocks are joined with blank lines)."""
    blocks = []
    for s in steps:
        if s.get("type") == "RepeatGroupDTO":
            n = int(s["numberOfIterations"])
            lines = ["%dx" % n]
            for c in s["workoutSteps"]:
                lines.append(_step_line(c))
            blocks.append(lines)
        else:
            blocks.append([_step_line(s)])
    return blocks


def build_description(steps):
    """Full workout text for the "description" field. Blocks (including
    repeat blocks) are separated by a blank line, per the syntax guide's
    'blank line before and after every repeat block' rule."""
    blocks = _convert_steps(steps)
    return "\n\n".join("\n".join(b) for b in blocks)

# ----------------------------------------------------------------- event


def build_event(name, steps, *, start_date_local, external_id=None,
                description=None, sport_type=SPORT_RUN):
    """Build one calendar-event dict for the /events/bulk?upsert=true
    endpoint.

    start_date_local: 'YYYY-MM-DDT00:00:00' string, or a datetime.date
                       (converted to midnight local).
    external_id: stable id for upsert/dedup (e.g. "timely-w4-tue").
    description: extra free text prepended to the generated workout text
                  (e.g. a longer human description); optional.
    """
    if hasattr(start_date_local, "isoformat") and not isinstance(start_date_local, str):
        start_date_local = start_date_local.isoformat() + "T00:00:00"

    text = build_description(steps)

    # intervals.icu's Suunto export needs a free-text title paragraph before
    # the first "- " step line, or it emits an empty guide.steps array and
    # Suunto rejects the upload with a 400. Use the extra `description` text
    # if given, else fall back to the workout name itself.
    title = (description.strip() if description else name.strip()) or "Workout"
    text = title + "\n\n" + text

    event = {
        "category": "WORKOUT",
        "start_date_local": start_date_local,
        "type": sport_type,
        "name": (name[:100] or "Workout"),
        "description": text,
    }
    if external_id is not None:
        event["external_id"] = str(external_id)[:64]
    return event


def build_bulk_payload(events):
    """events: list of build_event() dicts -> the JSON body for
    POST /api/v1/athlete/0/events/bulk?upsert=true."""
    return events

# --------------------------------------------------------------- validation


def validate_event(event):
    """Best-effort sanity checks. Returns a list of human-readable
    problems (empty if none found)."""
    problems = []
    if not event.get("name"):
        problems.append("missing name")
    elif len(event["name"]) > 100:
        problems.append("name too long: %d chars" % len(event["name"]))
    if not event.get("description"):
        problems.append("empty description")
    if event.get("category") != "WORKOUT":
        problems.append("category is not WORKOUT")
    if "start_date_local" not in event:
        problems.append("missing start_date_local")

    # Repeat-block blank-line rule: every "Nx" line should be preceded and
    # followed by a blank line (i.e. sit in its own paragraph).
    lines = event.get("description", "").split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.endswith("x") and stripped[:-1].isdigit():
            if i > 0 and lines[i - 1].strip() != "":
                problems.append("repeat line %r not preceded by blank line" % stripped)
            if i + 1 < len(lines) and lines[i + 1].strip() == "":
                problems.append("repeat line %r followed by blank line (steps missing?)" % stripped)
    return problems


def assert_serializable(payload):
    json.dumps(payload)
    return True
