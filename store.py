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
import tempfile
import threading
import time
from datetime import date

from cryptography.fernet import Fernet, InvalidToken

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


# ---------------------------------------------------------------------------
# Versioned migrations (T7). Each entry runs at most once per database,
# in order, gated by a schema_version counter stored in kv. Previously this
# was a `try: ALTER ... except OperationalError: pass` plus a single ad-hoc
# kv flag ('rpe_scale') -- idempotent by accident (ALTER on an existing
# column silently no-ops; re-running the RPE rescale would NOT have been
# safe, which is exactly why it was flag-gated in the first place). Add new
# migrations by appending a function here -- never edit a past one, even to
# "improve" it, since its job is to describe what already-deployed
# databases need, not what the schema should look like today.
# ---------------------------------------------------------------------------

def _migrate_001_gear_v2(c):
    """Add brand/model/is_default to gear (additive; 'name' is the id,
    'display' is the nickname, threshold_mi is the max mileage)."""
    for col in ("brand TEXT", "model TEXT", "is_default INTEGER DEFAULT 0"):
        try:
            c.execute("ALTER TABLE gear ADD COLUMN " + col)
        except sqlite3.OperationalError:
            pass  # column already exists -- fine, this migration is also
                   # reachable from a pre-schema_version database (see
                   # _current_version's legacy detection below)


def _migrate_002_rpe_scale_and_shoes_normalize(c):
    """RPE 1-5 -> 1-10 scale; shoes free text -> lowercased/trimmed gear
    keys. NOT safely re-runnable on its own (doubling an already-doubled
    RPE corrupts it) -- that's the whole reason this needs version gating
    rather than the old "if flag absent" check working by luck."""
    c.execute("UPDATE annotations SET rpe = MIN(10, rpe*2) WHERE rpe IS NOT NULL")
    c.execute("UPDATE annotations SET shoes = lower(trim(shoes)) WHERE shoes IS NOT NULL")


MIGRATIONS = [_migrate_001_gear_v2, _migrate_002_rpe_scale_and_shoes_normalize]


def _current_version(c):
    """Returns the schema_version already applied, seeding it on first run.

    Databases created before this migration system existed never wrote a
    schema_version row, so a fresh read would look like version 0 and
    re-run every migration -- safe for #1 (idempotent ALTER) but NOT for
    #2 (would re-double an already-converted RPE). Detect their actual
    state from the data itself instead of assuming "no row = nothing
    applied"."""
    row = c.execute("SELECT v FROM kv WHERE k='schema_version'").fetchone()
    if row is not None:
        return int(json.loads(row["v"]))
    legacy_rpe_done = c.execute(
        "SELECT 1 FROM kv WHERE k='rpe_scale'").fetchone() is not None
    gear_cols = {r["name"] for r in c.execute("PRAGMA table_info(gear)")}
    legacy_gear_done = {"brand", "model", "is_default"} <= gear_cols
    version = 2 if legacy_rpe_done else (1 if legacy_gear_done else 0)
    _set_version(c, version)
    return version


def _set_version(c, version):
    c.execute("INSERT OR REPLACE INTO kv VALUES('schema_version', ?, ?)",
              (json.dumps(version), time.time()))


def init():
    global _ready
    with _lock:
        if _ready:
            return
        with _conn() as c:
            c.executescript(SCHEMA)
            version = _current_version(c)
            for i, migrate in enumerate(MIGRATIONS, start=1):
                if version < i:
                    migrate(c)
                    version = i
                    _set_version(c, version)
            # Idempotent data seeding -- safe to run every time (INSERT OR
            # IGNORE), so it isn't a versioned migration.
            c.execute("INSERT OR IGNORE INTO gear"
                      "(name, display, start_mi, threshold_mi, retired, brand, model, is_default)"
                      " VALUES('asics gel nimbus 27','Nimbus 27',0,400,0,'Asics','Gel Nimbus 27',1)")
            c.execute("INSERT OR IGNORE INTO gear"
                      "(name, display, start_mi, threshold_mi, retired, brand, model, is_default)"
                      " VALUES('asics superblast','Superblast',0,400,0,'Asics','Superblast',0)")
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


_BACKUP_KEY_PATH = os.path.expanduser("~/.timely_backup_key")


def _backup_fernet():
    """G3: the key for encrypting backups before they leave the (FileVault-
    encrypted) Mac disk. Lives outside the repo, same pattern as
    ~/.garmin_tokens and ~/.mcm_coach_key -- generated on first use,
    0600 permissions, never logged or returned from any API."""
    try:
        with open(_BACKUP_KEY_PATH, "rb") as f:
            key = f.read().strip()
        if key:
            return Fernet(key)
    except FileNotFoundError:
        pass
    key = Fernet.generate_key()
    with open(_BACKUP_KEY_PATH, "wb") as f:
        f.write(key)
    os.chmod(_BACKUP_KEY_PATH, 0o600)
    return Fernet(key)


def backup(dest_dir):
    """Online backup of the whole DB (safe while in use) into dest_dir.
    The proprietary dataset should never live on exactly one disk.

    G1: this used to overwrite a single timely-backup.db every call -- a
    corrupt write (a backup taken mid-disk-error, say) would silently
    destroy your only copy with no history to fall back to. Now it keeps
    rolling, timestamped snapshots: a daily/ file per calendar day
    (re-backed-up on every call that day, so it always reflects that
    day's latest state) and a weekly/ file per ISO week, written once on
    the first backup of that week and left alone afterward -- a fixed
    checkpoint, not a moving target. Both are pruned to a retention
    window. `timely-backup.db.enc` (the old single-file path, now
    encrypted -- see G3 below) is kept too, as a fast "most recent"
    pointer.

    G3: dest_dir is the Drive-synced project folder -- it leaves the
    Mac's encrypted disk the moment Drive uploads it. The live DB itself
    is covered by FileVault (confirm this is actually on -- this code
    can't check that from here, see ARCHITECTURE.md), but a copy in
    cloud storage isn't covered by local disk encryption at all. The
    sqlite backup is taken to a private temp directory (never written
    into dest_dir), encrypted there with Fernet (AES-128-CBC + HMAC) using
    a key in ~/.timely_backup_key, and only the ciphertext ever touches
    dest_dir -- plaintext never exists in the synced folder, not even
    transiently.
    """
    init()
    daily_dir = os.path.join(dest_dir, "daily")
    weekly_dir = os.path.join(dest_dir, "weekly")
    os.makedirs(daily_dir, exist_ok=True)
    os.makedirs(weekly_dir, exist_ok=True)

    today = date.today()
    daily_path = os.path.join(daily_dir, "timely-%s.db.enc" % today.isoformat())
    iso_year, iso_week, _ = today.isocalendar()
    weekly_path = os.path.join(weekly_dir, "timely-%04d-W%02d.db.enc" % (iso_year, iso_week))
    latest_path = os.path.join(dest_dir, "timely-backup.db.enc")

    fernet = _backup_fernet()
    with tempfile.TemporaryDirectory() as td:
        plain_path = os.path.join(td, "plain.db")
        with _lock:
            src = _conn()
            try:
                dst = sqlite3.connect(plain_path)
                with dst:
                    src.backup(dst)
                dst.close()
            finally:
                src.close()
        with open(plain_path, "rb") as f:
            ciphertext = fernet.encrypt(f.read())

    with open(daily_path, "wb") as f:
        f.write(ciphertext)
    with open(latest_path, "wb") as f:
        f.write(ciphertext)
    if not os.path.exists(weekly_path):
        with open(weekly_path, "wb") as f:
            f.write(ciphertext)

    _prune_snapshots(daily_dir, keep=14)
    _prune_snapshots(weekly_dir, keep=8)
    return latest_path


def _prune_snapshots(dirpath, keep):
    """Filenames are timely-YYYY-MM-DD.db.enc / timely-YYYY-Www.db.enc --
    both sort lexicographically in chronological order, so a plain sort
    is enough to find the oldest."""
    files = sorted(f for f in os.listdir(dirpath)
                    if f.startswith("timely-") and f.endswith(".db.enc"))
    for f in files[:-keep] if len(files) > keep else []:
        try:
            os.remove(os.path.join(dirpath, f))
        except OSError:
            pass


def decrypt_backup(src_path, dest_path):
    """G3: manual recovery path. A backup you can't decrypt without this
    exact app running isn't much safer than no backup -- this is the one
    function that needs nothing but the key file and the cryptography
    library, so recovery doesn't depend on the rest of the app working.
    Raises cryptography.fernet.InvalidToken if the key doesn't match."""
    fernet = _backup_fernet()
    with open(src_path, "rb") as f:
        plaintext = fernet.decrypt(f.read())
    with open(dest_path, "wb") as f:
        f.write(plaintext)
    return dest_path


def verify_backup(dest_dir):
    """G2: 'a backup you've never restored isn't a backup.' Loads the
    most recent snapshot into a throwaway temp DB (never touches the
    live DB or the backup file itself), runs PRAGMA integrity_check, and
    sanity-checks row counts against the live DB. Raises on any failure
    -- a function that silently returns False here would recreate the
    exact gap this ticket exists to close. Returns a dict of row counts
    on success.

    G3: the snapshot is encrypted on disk (see backup()) -- this also
    exercises decrypt_backup() every time it runs, so a key mismatch or
    corrupted ciphertext surfaces via the same monthly drill + phone-push
    machinery as a corrupt DB would, instead of staying silent until the
    day someone actually needs to restore.
    """
    latest_path = os.path.join(dest_dir, "timely-backup.db.enc")
    if not os.path.exists(latest_path):
        raise RuntimeError("no backup found at %s" % latest_path)

    with tempfile.TemporaryDirectory() as td:
        tmp_path = os.path.join(td, "restore_check.db")
        try:
            decrypt_backup(latest_path, tmp_path)
        except InvalidToken:
            raise RuntimeError(
                "backup at %s could not be decrypted -- key mismatch or "
                "corrupted ciphertext" % latest_path)
        conn = sqlite3.connect(tmp_path)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise RuntimeError("backup failed integrity_check: %s" % ok)
            counts = {}
            for table in ("runs", "annotations", "gear", "weekly_reviews"):
                counts[table] = conn.execute(
                    "SELECT COUNT(*) FROM " + table).fetchone()[0]
        finally:
            conn.close()

    # gear always has >=2 rows after init() seeds default shoes -- if the
    # backup shows 0, something's structurally wrong with the snapshot,
    # not just "a quiet week."
    if counts["gear"] == 0:
        raise RuntimeError("backup's gear table is empty -- expected seeded "
                            "defaults; snapshot looks corrupt or truncated")

    with _lock:
        live = _conn()
        try:
            live_runs = live.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        finally:
            live.close()
    if live_runs > 0 and counts["runs"] == 0:
        raise RuntimeError(
            "backup has 0 runs but the live DB has %d -- snapshot looks "
            "empty/stale" % live_runs)

    return counts


# Tables worth exporting for G4. Deliberately wider than the ticket's own
# list (runs, annotations, gear, wellness, weekly_reviews) -- raw_activities/
# run_details/schedule_events also hold real proprietary or cached-third-
# -party data that would otherwise be just as trapped. Left out: `kv`
# (internal cache/schema-version bookkeeping, not user data) and
# `gcal_events` (sync plumbing -- a Calendar event ID is meaningless
# outside this app, nothing to "recover" from it).
EXPORT_TABLES = (
    "runs", "annotations", "gear", "wellness", "weekly_reviews",
    "raw_activities", "run_details", "schedule_events",
)


def export_all(dest_dir):
    """G4: one-command full export to plain JSON, one file per table --
    "your proprietary data should never be trapped in a format only this
    app reads." Deliberately plaintext (not encrypted like backup()/G3):
    this is meant to be opened by *anything* -- a text editor, a
    spreadsheet's JSON import, a different app entirely -- if this app or
    the unofficial Garmin API it depends on ever dies. At-rest protection
    for routine backups is G3's job, not this one's. Returns {table: row
    count} so a caller (or a human) can sanity-check nothing came back
    empty.
    """
    init()
    os.makedirs(dest_dir, exist_ok=True)
    written = {}
    with _lock:
        conn = _conn()
        try:
            for table in EXPORT_TABLES:
                rows = [dict(r) for r in
                        conn.execute("SELECT * FROM " + table).fetchall()]
                with open(os.path.join(dest_dir, table + ".json"), "w") as f:
                    json.dump(rows, f, indent=2, default=str)
                written[table] = len(rows)
        finally:
            conn.close()
    return written
