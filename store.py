"""timely's data layer — a local SQLite store.

This is the app's own memory, separate from Garmin (which is a cache of
*their* data). It holds synced history (runs, wellness), an event log of
every schedule change, and the genuinely proprietary part: your own
annotations (RPE, notes, shoes) that no platform has.

Python stdlib only. One file on disk; safe for the background service and
CLI to share (WAL mode, short transactions).
"""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("TIMELY_DB") or os.path.join(
    os.path.expanduser("~/Library/Application Support/MCMCoach"), "timely.db")

_lock = threading.Lock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  activity_id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  name TEXT,
  mi REAL,
  pace_sec INTEGER,
  raw TEXT,
  synced_at REAL
);
CREATE INDEX IF NOT EXISTS runs_date ON runs(date);
CREATE TABLE IF NOT EXISTS wellness(
  date TEXT PRIMARY KEY,
  rhr INTEGER,
  sleep_h REAL,
  bb INTEGER,
  synced_at REAL
);
CREATE TABLE IF NOT EXISTS schedule_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  action TEXT,
  ref TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS annotations(
  activity_id TEXT PRIMARY KEY,
  rpe INTEGER,
  note TEXT,
  shoes TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS raw_activities(
  activity_id TEXT PRIMARY KEY,
  json TEXT,
  synced_at REAL
);
CREATE TABLE IF NOT EXISTS run_details(
  activity_id TEXT PRIMARY KEY,
  json TEXT,
  synced_at REAL
);
CREATE TABLE IF NOT EXISTS weather_log(
  date TEXT PRIMARY KEY,
  json TEXT,
  synced_at REAL
);
CREATE TABLE IF NOT EXISTS external_metrics(
  source TEXT,
  date TEXT,
  json TEXT,
  synced_at REAL,
  PRIMARY KEY(source, date)
);
CREATE TABLE IF NOT EXISTS kv(
  k TEXT PRIMARY KEY,
  v TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS weekly_reviews(
  week INTEGER PRIMARY KEY,
  json TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS gear(
  name TEXT PRIMARY KEY,
  display TEXT,
  start_mi REAL DEFAULT 0,
  threshold_mi REAL DEFAULT 400,
  retired INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gcal_events(
  schedule_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  fingerprint TEXT,
  synced_at REAL
);
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    global _ready
    with _lock:
        if _ready:
            return
        with _conn() as c:
            c.executescript(SCHEMA)
            # gear v2: brand/model/is_default (additive migration; 'name' is
            # the id, 'display' is the nickname, threshold_mi = max mileage)
            for col in ("brand TEXT", "model TEXT", "is_default INTEGER DEFAULT 0"):
                try:
                    c.execute("ALTER TABLE gear ADD COLUMN " + col)
                except sqlite3.OperationalError:
                    pass
            # seed the rotation (no-op if already present)
            c.execute("INSERT OR IGNORE INTO gear"
                      "(name, display, start_mi, threshold_mi, retired, brand, model, is_default)"
                      " VALUES('asics gel nimbus 27','Nimbus 27',0,400,0,'Asics','Gel Nimbus 27',1)")
            c.execute("INSERT OR IGNORE INTO gear"
                      "(name, display, start_mi, threshold_mi, retired, brand, model, is_default)"
                      " VALUES('asics superblast','Superblast',0,400,0,'Asics','Superblast',0)")
            # one-time migrations: RPE 1-5 → 1-10, shoes strings → gear ids
            if c.execute("SELECT v FROM kv WHERE k='rpe_scale'").fetchone() is None:
                c.execute("UPDATE annotations SET rpe = MIN(10, rpe*2)"
                          " WHERE rpe IS NOT NULL")
                c.execute("UPDATE annotations SET shoes = lower(trim(shoes))"
                          " WHERE shoes IS NOT NULL")
                c.execute("INSERT OR REPLACE INTO kv VALUES('rpe_scale','10',?)",
                          (time.time(),))
        _ready = True


def upsert_runs(runs):
    """runs: list of dicts as served by /api/actuals."""
    init()
    now = time.time()
    with _lock, _conn() as c:
        for r in runs:
            if not r.get("activityId"):
                continue
            c.execute(
                "INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?)",
                (str(r["activityId"]), r["date"], r.get("name"), r.get("mi"),
                 r.get("paceSec"), json.dumps(r), now))


def get_runs():
    init()
    with _lock, _conn() as c:
        rows = c.execute("SELECT raw FROM runs ORDER BY date").fetchall()
    return [json.loads(row["raw"]) for row in rows]


def upsert_wellness(days):
    init()
    now = time.time()
    with _lock, _conn() as c:
        for d in days:
            if not d.get("date"):
                continue
            c.execute("INSERT OR REPLACE INTO wellness VALUES(?,?,?,?,?)",
                      (d["date"], d.get("rhr"), d.get("sleepH"), d.get("bb"), now))


def get_wellness(limit=7):
    init()
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT date, rhr, sleep_h, bb FROM wellness ORDER BY date DESC LIMIT ?",
            (limit,)).fetchall()
    return [{"date": r["date"], "rhr": r["rhr"], "sleepH": r["sleep_h"],
             "bb": r["bb"]} for r in rows]


def log_event(action, ref, detail=""):
    init()
    with _lock, _conn() as c:
        c.execute("INSERT INTO schedule_events(ts, action, ref, detail) VALUES(?,?,?,?)",
                  (time.time(), action, str(ref), detail))


def set_annotation(activity_id, rpe=None, note=None, shoes=None):
    """Merge-on-write: only provided fields change (so setting gear from
    /api/run/{id}/gear can't wipe an existing RPE/note). shoes stores the
    gear id (case-folded); unknown shoes auto-register in gear."""
    init()
    if rpe is not None:
        rpe = int(rpe)
        if not 1 <= rpe <= 10:
            raise ValueError("rpe must be 1-10")
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM annotations WHERE activity_id=?",
                        (str(activity_id),)).fetchone()
        cur = dict(row) if row else {"rpe": None, "note": "", "shoes": ""}
        if rpe is not None:
            cur["rpe"] = rpe
        if note is not None:
            cur["note"] = note[:500]
        if shoes is not None:
            key = shoes.lower().strip()[:60]
            cur["shoes"] = key
            if key:
                c.execute("INSERT OR IGNORE INTO gear"
                          "(name, display, start_mi, threshold_mi, retired,"
                          " brand, model, is_default)"
                          " VALUES(?,?,0,400,0,NULL,NULL,0)",
                          (key, shoes.strip()[:60]))
        c.execute("INSERT OR REPLACE INTO annotations VALUES(?,?,?,?,?)",
                  (str(activity_id), cur["rpe"], cur["note"], cur["shoes"],
                   time.time()))


def save_raw_activity(activity_id, payload):
    """Garmin's complete activity record, untouched. Cheap to keep, and
    every future feature (cadence trends, training effect, VO2max) mines it
    without another API design round-trip."""
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO raw_activities VALUES(?,?,?)",
                  (str(activity_id), json.dumps(payload), time.time()))


def save_run_detail(activity_id, detail):
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO run_details VALUES(?,?,?)",
                  (str(activity_id), json.dumps(detail), time.time()))


def get_run_detail(activity_id):
    init()
    with _lock, _conn() as c:
        row = c.execute("SELECT json FROM run_details WHERE activity_id=?",
                        (str(activity_id),)).fetchone()
    return json.loads(row["json"]) if row else None


def save_weather(day, wx):
    """Today's conditions, kept — so 'pace vs heat' is answerable in October."""
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO weather_log VALUES(?,?,?)",
                  (day, json.dumps(wx), time.time()))


def save_external(source, day, payload):
    """Generic inbox for any other source (Apple Health via Health Auto
    Export / iOS Shortcuts, a smart scale, anything that can POST JSON).
    One row per source per day; new sources never require schema changes."""
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO external_metrics VALUES(?,?,?,?)",
                  (source[:40], day, json.dumps(payload), time.time()))


def get_annotations():
    init()
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM annotations").fetchall()
    return {r["activity_id"]: {"rpe": r["rpe"], "note": r["note"],
                               "shoes": r["shoes"]} for r in rows}


def gear_summary():
    """Miles per shoe: annotations.shoes joined to runs, merged with gear
    metadata (starting miles, threshold, retired)."""
    init()
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT lower(trim(a.shoes)) k, min(a.shoes) disp, sum(r.mi) mi,"
            " count(*) n, max(r.date) last"
            " FROM annotations a JOIN runs r ON r.activity_id=a.activity_id"
            " WHERE trim(a.shoes)!='' GROUP BY k").fetchall()
        meta = {g["name"]: dict(g) for g in c.execute("SELECT * FROM gear")}
    def shape(k, g, mi, n, last):
        nick = g.get("display") or k
        return {"key": k, "nickname": nick, "display": nick,
                "brand": g.get("brand"), "model": g.get("model"),
                "mi": round((g.get("start_mi") or 0) + (mi or 0), 1),
                "runs": n, "last": last,
                "threshold": g.get("threshold_mi") or 400,
                "retired": bool(g.get("retired")),
                "isDefault": bool(g.get("is_default"))}
    out, seen = [], set()
    for r in rows:
        seen.add(r["k"])
        out.append(shape(r["k"], meta.get(r["k"], {}), r["mi"], r["n"], r["last"]))
    for k, g in meta.items():           # gear registered but not yet run in
        if k not in seen:
            out.append(shape(k, g, 0, 0, None))
    out.sort(key=lambda g: (g["retired"], not g["isDefault"], -g["mi"]))
    return out


def set_gear(key, display=None, start_mi=None, threshold_mi=None, retired=None,
             brand=None, model=None, is_default=None):
    init()
    key = key.lower().strip()[:60]
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM gear WHERE name=?", (key,)).fetchone()
        cur = dict(row) if row else {"display": None, "start_mi": 0,
                                     "threshold_mi": 400, "retired": 0,
                                     "brand": None, "model": None, "is_default": 0}
        if display is not None:
            cur["display"] = display[:60]
        if start_mi is not None:
            cur["start_mi"] = max(0.0, float(start_mi))
        if threshold_mi is not None:
            cur["threshold_mi"] = max(50.0, float(threshold_mi))
        if retired is not None:
            cur["retired"] = 1 if retired else 0
        if brand is not None:
            cur["brand"] = brand[:40]
        if model is not None:
            cur["model"] = model[:60]
        if is_default is not None:
            if is_default:
                c.execute("UPDATE gear SET is_default=0")   # single default
            cur["is_default"] = 1 if is_default else 0
        c.execute("INSERT OR REPLACE INTO gear"
                  "(name, display, start_mi, threshold_mi, retired,"
                  " brand, model, is_default) VALUES(?,?,?,?,?,?,?,?)",
                  (key, cur["display"], cur["start_mi"], cur["threshold_mi"],
                   cur["retired"], cur["brand"], cur["model"], cur["is_default"]))


def save_review(week, review):
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO weekly_reviews VALUES(?,?,?)",
                  (int(week), json.dumps(review), time.time()))


def get_review(week):
    init()
    with _lock, _conn() as c:
        row = c.execute("SELECT json FROM weekly_reviews WHERE week=?",
                        (int(week),)).fetchone()
    return json.loads(row["json"]) if row else None


def set_kv(k, v):
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES(?,?,?)",
                  (k, json.dumps(v), time.time()))


def get_kv(k):
    """Returns (value, age_seconds) or (None, None)."""
    init()
    with _lock, _conn() as c:
        row = c.execute("SELECT v, updated_at FROM kv WHERE k=?", (k,)).fetchone()
    if row is None:
        return None, None
    return json.loads(row["v"]), time.time() - row["updated_at"]


def get_gcal_map():
    """scheduleId (str) -> {eventId, fingerprint} for every synced workout."""
    init()
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM gcal_events").fetchall()
    return {r["schedule_id"]: {"eventId": r["event_id"],
                                "fingerprint": r["fingerprint"]} for r in rows}


def set_gcal_event(schedule_id, event_id, fingerprint):
    init()
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO gcal_events VALUES(?,?,?,?)",
                  (str(schedule_id), event_id, fingerprint, time.time()))


def delete_gcal_event(schedule_id):
    init()
    with _lock, _conn() as c:
        c.execute("DELETE FROM gcal_events WHERE schedule_id=?", (str(schedule_id),))


def backup(dest_dir):
    """Online backup of the whole DB (safe while in use) into dest_dir.
    The proprietary dataset should never live on exactly one disk."""
    init()
    dest = os.path.join(dest_dir, "timely-backup.db")
    with _lock:
        src = _conn()
        try:
            dst = sqlite3.connect(dest)
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
    return dest
