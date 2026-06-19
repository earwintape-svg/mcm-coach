"""SuuntoPlus Guide builders — prototype.

Converts the workout-step tree produced by builders.py (Garmin Connect
schema: step()/repeat() dicts) into SuuntoPlus Guide JSON, per
https://apizone.suunto.com/suuntoplus-guide-description

This is a converter, not a new plan representation: plan.py and
builders.py are untouched. Feed it the same Workout(suffix, steps) that
build_plan() consumes for Garmin, plus a date, and it produces the
contents of guide.json (+ a matching icon.png) ready to zip and POST to
the SuuntoPlus Guide API.

Key mapping decisions:
  * pace.zone targets (workoutTargetTypeId 6) carry targetValueOne/Two in
    m/s -- the SAME units as Suunto's targetPace min/max (verified against
    the API doc's example: 4.166 m/s == 4:00/km). Direct passthrough, no
    conversion.
  * distance/time end-conditions become stepDistance/stepDuration
    transitions (auto-advance) -- matches this plan's "no lap press"
    philosophy (the Garmin Bug 7 equivalent here would be relying on
    manualLap).
  * RepeatGroupDTO -> Suunto RepeatStep (times=n, steps=[...]). Suunto
    disallows nested repeats; every repeat() in plan.py is a flat list of
    leaf steps, so this always holds (asserted in validate_guide).
  * NO_TARGET steps get a plain "pace" field (current pace, no gauge)
    instead of a target field.
  * activities=[1] is RUNNING (matches suuntool's activity-id table).
    SuuntoPlus Guides are confirmed for the Suunto 3/5/9 families.

Open items / unverified against the real API:
  * Full Activities id list beyond running (1) — only matters if you ever
    tag a non-running workout.
  * How 93 dated guides (one per plan.py entry) behave in the Suunto app's
    guide list / on-watch storage limit — Guides are a flat list with an
    optional single localDate, not a calendar like Garmin Connect's.
  * Per-rep notifications (one per leaf step, including inside repeats) may
    be too chatty for short intervals (e.g. 400m); intervals.icu instead
    notifies once per repeat *set*. Easy to change in _fields_step /
    _convert_steps if it's annoying on the watch.
  * Pushing a guide requires OAuth via the SuuntoPlus Guide Cloud API
    (partner/developer registration at apizone.suunto.com) — this module
    only builds the guide files; it does not upload them.
"""
import io
import json
import struct
import zlib
import zipfile

ACTIVITY_RUNNING = 1

# ----------------------------------------------------------- step builders


def _title(desc, limit=13):
    """Shorten a step description to fit a FieldsStep/notification title
    (<=13 chars). Best-effort: truncate on a word boundary."""
    if not desc:
        return None
    if len(desc) <= limit:
        return desc
    out = desc[:limit].rsplit(" ", 1)[0]
    return out or desc[:limit]


def _countdown_field(s):
    """stepDistanceCountdown / stepDurationCountdown from the step's
    endCondition — mirrors what the watch shows during a Garmin step."""
    cond = s["endCondition"]["conditionTypeKey"]
    if cond == "distance":
        return {"type": "stepDistanceCountdown",
                "value": float(s["endConditionValue"]), "title": "To go"}
    return {"type": "stepDurationCountdown",
            "value": float(s["endConditionValue"]), "title": "To go"}


def _transition(s):
    """Auto-advance condition from the step's endCondition. No manualLap —
    matches this plan's distance/time-driven steps."""
    cond = s["endCondition"]["conditionTypeKey"]
    if cond == "distance":
        condition = {"type": "stepDistance", "value": float(s["endConditionValue"])}
    else:
        condition = {"type": "stepDuration", "value": float(s["endConditionValue"])}
    return [{"condition": condition}]


def _target_field(s):
    """pace.zone -> targetPace (m/s, same units as Garmin's speed target).
    NO_TARGET -> plain pace field (current pace, no gauge)."""
    tgt = s.get("targetType") or {}
    if tgt.get("workoutTargetTypeKey") == "pace.zone":
        v1, v2 = s.get("targetValueOne"), s.get("targetValueTwo")
        if v1 is not None and v2 is not None:
            return {"type": "targetPace", "min": round(v1, 3),
                    "max": round(v2, 3), "title": "Pace"}
    return {"type": "pace", "title": "Pace"}


def _fields_step(s):
    """ExecutableStepDTO -> Suunto FieldsStep: countdown + pace/target
    fields, plus a short title and a step-start notification carrying the
    full description."""
    desc = (s.get("description") or "").strip()
    title = _title(desc)
    out = {
        "type": "fields",
        "fields": [_countdown_field(s), _target_field(s)],
        "transitions": _transition(s),
    }
    if title:
        out["title"] = title
    if desc:
        out["notification"] = {"title": (title or "Step")[:13], "text": desc[:54]}
    return out


def _convert_steps(steps):
    """Walk the Garmin step tree -> list of Suunto Guide Steps."""
    out = []
    for s in steps:
        if s.get("type") == "RepeatGroupDTO":
            out.append({
                "type": "repeat",
                "times": int(s["numberOfIterations"]),
                "steps": [_fields_step(c) for c in s["workoutSteps"]],
            })
        else:
            out.append(_fields_step(s))
    return out

# ----------------------------------------------------------------- guide


def build_guide(name, steps, *, local_date=None, external_id=None,
                description=None, short_description=None,
                owner="timely", url="https://github.com/"):
    """Build the contents of guide.json from a (suffix, steps) tree — the
    same tree builders.py hands to make_workout() for Garmin.

    local_date: a datetime.date — guide["localDate"] (yyyy-MM-dd), the date
                 this workout is most relevant (i.e. the plan.py schedule
                 date).
    external_id: stable id for re-sync / dedup (e.g. "timely-w4-tue").
    """
    desc = (description or name).strip()
    short = (short_description or name).strip()
    guide = {
        "type": "sequence",
        "name": (name[:60] or "Workout"),
        "description": (desc[:256] or "Workout"),
        "shortDescription": (short[:23] or "Workout"),
        "owner": owner[:64],
        "url": url,
        "activities": [ACTIVITY_RUNNING],
        "usage": "workout",
        "steps": _convert_steps(steps),
    }
    if local_date is not None:
        guide["localDate"] = local_date.isoformat()
    if external_id is not None:
        guide["externalId"] = str(external_id)[:64]
    return guide

# ------------------------------------------------------------------ icon

_ICON_CACHE = {}


def guide_icon_png(size=300):
    """SxS PNG icon for the guide ZIP. The API requires 300x300; this is
    coach.py's pure-stdlib chevron renderer (_icon_png), scaled up. No
    Pillow, no asset files, can't go missing."""
    if size in _ICON_CACHE:
        return _ICON_CACHE[size]
    S = size
    R = S / 18.0
    BG, MINT, CORAL = (16, 20, 24), (93, 202, 165), (240, 153, 123)
    scale = S / 180.0
    mint_segs = [tuple(c * scale for c in seg) for seg in
                  [(56, 56, 93, 90), (93, 90, 56, 124)]]
    coral_segs = [tuple(c * scale for c in seg) for seg in
                   [(101, 68, 129, 90), (129, 90, 101, 113)]]

    def dist(px, py, segs):
        best = 1e9
        for ax, ay, bx, by in segs:
            vx, vy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
            dx, dy = px - (ax + t * vx), py - (ay + t * vy)
            best = min(best, (dx * dx + dy * dy) ** 0.5)
        return best

    def mix(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    rows = []
    for y in range(S):
        row = bytearray()
        for x in range(S):
            c = BG
            d1, d2 = dist(x, y, mint_segs), dist(x, y, coral_segs)
            d, col = (d2, CORAL) if d2 <= d1 else (d1, MINT)
            if d <= R + 1:
                c = mix(c, col, max(0.0, min(1.0, R + 1 - d)))
            row += bytes(c)
        rows.append(bytes(row))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + r for r in rows)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    _ICON_CACHE[size] = png
    return png

# ----------------------------------------------------------------- zip


def build_guide_zip(name, steps, **kwargs):
    """Return (zip_bytes, guide_dict) — the ZIP is guide.json + icon.png,
    ready to POST to the SuuntoPlus Guide Cloud API."""
    guide = build_guide(name, steps, **kwargs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("guide.json", json.dumps(guide, indent=2))
        zf.writestr("icon.png", guide_icon_png())
    return buf.getvalue(), guide

# --------------------------------------------------------- field-limit check

# (field name -> (min, max) char length), per the Guide object reference.
_LEN_LIMITS = {
    "name": (1, 60), "description": (1, 256), "shortDescription": (1, 23),
    "owner": (1, 64), "url": (1, 256), "externalId": (1, 64),
}


def validate_guide(guide):
    """Best-effort check against documented SuuntoPlus Guide limits.
    Returns a list of human-readable problems (empty if none found)."""
    problems = []
    for key, (lo, hi) in _LEN_LIMITS.items():
        val = guide.get(key)
        if val is None:
            continue
        if not (lo <= len(val) <= hi):
            problems.append("%s: length %d not in %d..%d (%r)" % (key, len(val), lo, hi, val))

    steps = guide.get("steps", [])
    if not (1 <= len(steps) <= 1000):
        problems.append("top-level steps: %d not in 1..1000" % len(steps))

    def walk_step(s, depth):
        if s["type"] == "repeat":
            if depth > 0:
                problems.append("nested repeat step found (not allowed)")
            if not (1 <= s["times"] <= 100):
                problems.append("repeat times %s out of 1..100" % s["times"])
            if not (1 <= len(s["steps"]) <= 1000):
                problems.append("repeat child step count %d not in 1..1000" % len(s["steps"]))
            for c in s["steps"]:
                walk_step(c, depth + 1)
        else:
            title = s.get("title")
            if title is not None and not (0 < len(title) <= 13):
                problems.append("step title length %d > 13 (%r)" % (len(title), title))
            notif = s.get("notification")
            if notif:
                t = notif.get("title")
                if t is not None and len(t) > 13:
                    problems.append("notification title >13 chars: %r" % t)
                txt = notif.get("text")
                if txt is not None and not (1 <= len(txt) <= 54):
                    problems.append("notification text length %d not in 1..54" % len(txt))
            for f in s.get("fields", []):
                if f["type"] == "text" and not (1 <= len(f.get("value", "")) <= 54):
                    problems.append("text field length out of range: %r" % f.get("value"))

    for s in steps:
        walk_step(s, 0)
    return problems


def assert_serializable(payload):
    json.dumps(payload)
    return True
