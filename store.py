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
    init()
    if rpe is not None:
        rpe = int(rpe)
        if not 1 <= rpe <= 5:
            raise ValueError("rpe must be 1-5")
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO annotations VALUES(?,?,?,?,?)",
                  (str(activity_id), rpe, (note or "")[:500], (shoes or "")[:100],
                   time.time()))


def get_annotations():
    init()
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM annotations").fetchall()
    return {r["activity_id"]: {"rpe": r["rpe"], "note": r["note"],
                               "shoes": r["shoes"]} for r in rows}
