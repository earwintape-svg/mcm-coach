"""Tests for store.py's versioned migration system (T7).

These intentionally bypass tests/conftest.py's autouse isolated_store
fixture's assumption that init() hasn't run yet -- each test here builds
its own temp DB by hand (sometimes pre-seeded to look like a database
from before this migration system existed) and drives store.init()
directly, so it can assert on schema_version and on whether data got
transformed exactly once.
"""
import json
import sqlite3
import time


import store


def _fresh_db_path(tmp_path, name="mig.db"):
    return str(tmp_path / name)


def _legacy_pre_migration_db(path):
    """Builds a database that looks like one created by the OLD ad-hoc
    init() -- base schema, no brand/model/is_default on gear, no
    schema_version row, an annotation with an old-scale (1-5) RPE and
    unnormalized shoes text."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE gear(
          name TEXT PRIMARY KEY, display TEXT, start_mi REAL DEFAULT 0,
          threshold_mi REAL DEFAULT 400, retired INTEGER DEFAULT 0
        );
        CREATE TABLE annotations(
          activity_id TEXT PRIMARY KEY, rpe INTEGER, note TEXT,
          shoes TEXT, updated_at REAL
        );
        CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT, updated_at REAL);
        CREATE TABLE runs(
          activity_id TEXT PRIMARY KEY, date TEXT NOT NULL, name TEXT,
          mi REAL, pace_sec INTEGER, raw TEXT, synced_at REAL
        );
        CREATE TABLE raw_activities(activity_id TEXT PRIMARY KEY, json TEXT, synced_at REAL);
        CREATE TABLE run_details(activity_id TEXT PRIMARY KEY, json TEXT, synced_at REAL);
        CREATE TABLE weather_log(date TEXT PRIMARY KEY, json TEXT, synced_at REAL);
        CREATE TABLE external_metrics(source TEXT, date TEXT, json TEXT, synced_at REAL, PRIMARY KEY(source, date));
        CREATE TABLE weekly_reviews(week INTEGER PRIMARY KEY, json TEXT, created_at REAL);
        CREATE TABLE wellness(date TEXT PRIMARY KEY, rhr INTEGER, sleep_h REAL, bb INTEGER, synced_at REAL);
        CREATE TABLE schedule_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, action TEXT, ref TEXT, detail TEXT);
    """)
    c.execute("INSERT INTO annotations VALUES('act1', 3, 'old note', '  Pegasus 41 ', ?)",
              (time.time(),))
    c.commit()
    c.close()


class TestFreshDatabase:
    def test_fresh_db_inits_to_latest_version(self, tmp_path, monkeypatch):
        path = _fresh_db_path(tmp_path)
        monkeypatch.setattr(store, "DB_PATH", path)
        monkeypatch.setattr(store, "_ready", False)
        store.init()

        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT v FROM kv WHERE k='schema_version'").fetchone()
        assert json.loads(row["v"]) == len(store.MIGRATIONS)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(gear)")}
        assert {"brand", "model", "is_default"} <= cols
        c.close()

    def test_fresh_db_seeds_gear_rotation(self, tmp_path, monkeypatch):
        path = _fresh_db_path(tmp_path)
        monkeypatch.setattr(store, "DB_PATH", path)
        monkeypatch.setattr(store, "_ready", False)
        store.init()
        gear = store.gear_summary()
        keys = {g["key"] for g in gear}
        assert "asics gel nimbus 27" in keys
        assert "asics superblast" in keys


class TestLegacyDatabase:
    def test_old_schema_db_migrates_correctly(self, tmp_path, monkeypatch):
        path = _fresh_db_path(tmp_path, "legacy.db")
        _legacy_pre_migration_db(path)
        monkeypatch.setattr(store, "DB_PATH", path)
        monkeypatch.setattr(store, "_ready", False)

        store.init()

        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        # gear v2 columns landed
        cols = {r["name"] for r in c.execute("PRAGMA table_info(gear)")}
        assert {"brand", "model", "is_default"} <= cols
        # RPE rescaled exactly once: 3 -> 6, not 3 -> 6 -> 12(clamped 10)
        row = c.execute("SELECT rpe, shoes FROM annotations WHERE activity_id='act1'").fetchone()
        assert row["rpe"] == 6
        assert row["shoes"] == "pegasus 41"
        # version recorded at the latest migration, not re-derived as 0
        ver = c.execute("SELECT v FROM kv WHERE k='schema_version'").fetchone()
        assert json.loads(ver["v"]) == len(store.MIGRATIONS)
        c.close()

    def test_legacy_rpe_flag_alone_is_treated_as_fully_migrated(self, tmp_path, monkeypatch):
        """A database that already went through the OLD ad-hoc migration
        (has the legacy 'rpe_scale' kv flag, RPE already on the 1-10
        scale) must NOT get its RPE doubled again just because
        schema_version itself was never written."""
        path = _fresh_db_path(tmp_path, "legacy_done.db")
        _legacy_pre_migration_db(path)
        c = sqlite3.connect(path)
        # Simulate: old code already ran once -- RPE already rescaled to
        # 6, legacy flag present, gear v2 columns already added.
        c.execute("UPDATE annotations SET rpe=6 WHERE activity_id='act1'")
        c.execute("ALTER TABLE gear ADD COLUMN brand TEXT")
        c.execute("ALTER TABLE gear ADD COLUMN model TEXT")
        c.execute("ALTER TABLE gear ADD COLUMN is_default INTEGER DEFAULT 0")
        c.execute("INSERT INTO kv VALUES('rpe_scale', '\"10\"', ?)", (time.time(),))
        c.commit()
        c.close()

        monkeypatch.setattr(store, "DB_PATH", path)
        monkeypatch.setattr(store, "_ready", False)
        store.init()

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT rpe FROM annotations WHERE activity_id='act1'").fetchone()
        assert row["rpe"] == 6, "must not double an already-migrated RPE"
        conn.close()


class TestReentrantInit:
    def test_rerunning_init_is_a_noop(self, tmp_path, monkeypatch):
        path = _fresh_db_path(tmp_path, "reentrant.db")
        _legacy_pre_migration_db(path)
        monkeypatch.setattr(store, "DB_PATH", path)
        monkeypatch.setattr(store, "_ready", False)

        store.init()
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        rpe_after_first = c.execute(
            "SELECT rpe FROM annotations WHERE activity_id='act1'").fetchone()["rpe"]
        c.close()

        # Force a second real pass through init() (in-process it would
        # short-circuit on _ready -- this simulates a second process
        # opening the same already-migrated database).
        monkeypatch.setattr(store, "_ready", False)
        store.init()

        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        rpe_after_second = c.execute(
            "SELECT rpe FROM annotations WHERE activity_id='act1'").fetchone()["rpe"]
        ver = c.execute("SELECT v FROM kv WHERE k='schema_version'").fetchone()
        c.close()

        assert rpe_after_second == rpe_after_first == 6
        assert json.loads(ver["v"]) == len(store.MIGRATIONS)
