"""Tests for store.py's G1 (versioned snapshots) and G2 (verified
restore) backup work.

Before this, backup() overwrote a single timely-backup.db every call --
fine until one bad write corrupts the only copy you have, with no older
snapshot to fall back to. These tests exercise the new daily/weekly
rotation + retention pruning, and verify_backup()'s integrity_check +
row-count sanity checks (using the isolated_store fixture from conftest.py,
so this never touches the real database).
"""
import os
import time

import pytest

import store


class TestBackupRotation:
    def test_backup_writes_latest_daily_and_weekly(self, tmp_path):
        store.set_annotation(activity_id="1", note="hi")  # put something in the DB
        dest = store.backup(str(tmp_path))

        assert os.path.exists(dest)
        assert os.path.basename(dest) == "timely-backup.db"
        daily_files = os.listdir(tmp_path / "daily")
        weekly_files = os.listdir(tmp_path / "weekly")
        assert len(daily_files) == 1
        assert len(weekly_files) == 1

    def test_same_day_backups_overwrite_one_daily_file(self, tmp_path):
        store.backup(str(tmp_path))
        store.backup(str(tmp_path))
        store.backup(str(tmp_path))
        assert len(os.listdir(tmp_path / "daily")) == 1

    def test_weekly_snapshot_is_not_overwritten_once_taken(self, tmp_path, monkeypatch):
        store.backup(str(tmp_path))
        weekly_dir = tmp_path / "weekly"
        weekly_file = next(weekly_dir.iterdir())
        original_mtime = weekly_file.stat().st_mtime

        # Mutate the DB and back up again the same week -- the weekly
        # snapshot must stay exactly as it was on first write.
        store.set_annotation(activity_id="2", note="changed after weekly snapshot")
        time.sleep(0.05)
        store.backup(str(tmp_path))

        assert weekly_file.stat().st_mtime == original_mtime

    def test_daily_retention_prunes_oldest(self, tmp_path):
        daily_dir = tmp_path / "daily"
        daily_dir.mkdir(parents=True)
        # Seed 16 fake daily snapshot files (chronological by name).
        for i in range(16):
            (daily_dir / ("timely-2026-01-%02d.db" % (i + 1))).write_bytes(b"x")
        store._prune_snapshots(str(daily_dir), keep=14)
        remaining = sorted(os.listdir(daily_dir))
        assert len(remaining) == 14
        # The two oldest (01, 02) should be the ones pruned.
        assert "timely-2026-01-01.db" not in remaining
        assert "timely-2026-01-02.db" not in remaining
        assert "timely-2026-01-16.db" in remaining

    def test_retention_noop_under_the_limit(self, tmp_path):
        d = tmp_path / "few"
        d.mkdir()
        (d / "timely-2026-01-01.db").write_bytes(b"x")
        store._prune_snapshots(str(d), keep=14)
        assert len(os.listdir(d)) == 1


class TestVerifyBackup:
    def test_raises_if_no_backup_exists_yet(self, tmp_path):
        with pytest.raises(RuntimeError, match="no backup found"):
            store.verify_backup(str(tmp_path))

    def test_passes_on_a_healthy_backup(self, tmp_path):
        store.backup(str(tmp_path))
        counts = store.verify_backup(str(tmp_path))
        # gear is seeded with 2 default shoes by init() -- always present.
        assert counts["gear"] >= 2

    def test_raises_on_corrupt_backup_file(self, tmp_path):
        store.backup(str(tmp_path))
        # Truncate the "latest" pointer file to garbage bytes -- not a
        # valid SQLite file at all, so integrity_check (or even opening
        # it) should fail loudly rather than silently look like a normal,
        # if Spartan, database.
        latest = tmp_path / "timely-backup.db"
        latest.write_bytes(b"not a real sqlite file" * 50)
        with pytest.raises(Exception):
            store.verify_backup(str(tmp_path))

    def test_raises_if_backup_runs_table_empty_but_live_has_runs(self, tmp_path, monkeypatch):
        # Build a backup first (before any runs exist), then add a run to
        # the *live* DB only, simulating a stale/incomplete backup.
        store.backup(str(tmp_path))
        conn = store._conn()
        with conn:
            conn.execute(
                "INSERT INTO runs(activity_id, date, mi) VALUES (?, ?, ?)",
                ("999", "2026-06-19", 5.0))
        conn.close()
        with pytest.raises(RuntimeError, match="0 runs but the live DB has"):
            store.verify_backup(str(tmp_path))
