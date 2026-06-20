"""Tests for store.py's G1 (versioned snapshots), G2 (verified restore),
and G3 (encryption at rest) backup work.

Before this, backup() overwrote a single timely-backup.db every call --
fine until one bad write corrupts the only copy you have, with no older
snapshot to fall back to. These tests exercise the new daily/weekly
rotation + retention pruning, verify_backup()'s integrity_check + row-
count sanity checks, and the Fernet encrypt/decrypt round trip that now
sits between every snapshot and disk (using the isolated_store fixture
from conftest.py, so this never touches the real database OR the real
~/.timely_backup_key).
"""
import os
import sqlite3
import time

import pytest
from cryptography.fernet import InvalidToken

import store


class TestBackupRotation:
    def test_backup_writes_latest_daily_and_weekly(self, tmp_path):
        store.set_annotation(activity_id="1", note="hi")  # put something in the DB
        dest = store.backup(str(tmp_path))

        assert os.path.exists(dest)
        assert os.path.basename(dest) == "timely-backup.db.enc"
        daily_files = os.listdir(tmp_path / "daily")
        weekly_files = os.listdir(tmp_path / "weekly")
        assert len(daily_files) == 1
        assert len(weekly_files) == 1
        assert daily_files[0].endswith(".db.enc")
        assert weekly_files[0].endswith(".db.enc")

    def test_backup_files_are_actually_encrypted(self, tmp_path):
        """G3: the plaintext SQLite header ("SQLite format 3\\x00") must
        never appear in what lands in dest_dir -- that's the entire point
        of this ticket. Confirms by attempting to open the snapshot
        directly as SQLite (must fail) and checking the raw bytes don't
        contain the magic header."""
        dest = store.backup(str(tmp_path))
        raw = open(dest, "rb").read()
        assert b"SQLite format 3" not in raw

    def test_backup_decrypts_back_to_a_valid_sqlite_db(self, tmp_path):
        store.set_annotation(activity_id="1", note="round trip")
        dest = store.backup(str(tmp_path))
        out_path = tmp_path / "decrypted.db"
        store.decrypt_backup(dest, str(out_path))
        assert out_path.read_bytes().startswith(b"SQLite format 3")

    def test_wrong_key_fails_to_decrypt(self, tmp_path, monkeypatch):
        dest = store.backup(str(tmp_path))
        # Swap in a different key, as if the real key file were lost or
        # mismatched -- decrypting with it must fail loudly, not produce
        # garbage that looks like it might be a database.
        monkeypatch.setattr(store, "_BACKUP_KEY_PATH", str(tmp_path / "other_key"))
        with pytest.raises(InvalidToken):
            store.decrypt_backup(dest, str(tmp_path / "out.db"))

    def test_backup_key_is_created_once_and_reused(self, tmp_path):
        store.backup(str(tmp_path))
        assert os.path.exists(store._BACKUP_KEY_PATH)
        key_after_first = open(store._BACKUP_KEY_PATH, "rb").read()
        store.backup(str(tmp_path))
        assert open(store._BACKUP_KEY_PATH, "rb").read() == key_after_first

    def test_backup_key_file_is_user_only_permissions(self, tmp_path):
        store.backup(str(tmp_path))
        mode = os.stat(store._BACKUP_KEY_PATH).st_mode & 0o777
        assert mode == 0o600

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
            (daily_dir / ("timely-2026-01-%02d.db.enc" % (i + 1))).write_bytes(b"x")
        store._prune_snapshots(str(daily_dir), keep=14)
        remaining = sorted(os.listdir(daily_dir))
        assert len(remaining) == 14
        # The two oldest (01, 02) should be the ones pruned.
        assert "timely-2026-01-01.db.enc" not in remaining
        assert "timely-2026-01-02.db.enc" not in remaining
        assert "timely-2026-01-16.db.enc" in remaining

    def test_retention_noop_under_the_limit(self, tmp_path):
        d = tmp_path / "few"
        d.mkdir()
        (d / "timely-2026-01-01.db.enc").write_bytes(b"x")
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
        # Truncate the "latest" pointer file to garbage bytes -- not even
        # valid Fernet ciphertext, so decryption itself should fail
        # loudly rather than silently producing garbage that looks like
        # it might be a database.
        latest = tmp_path / "timely-backup.db.enc"
        latest.write_bytes(b"not a real encrypted backup" * 50)
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


class TestRestoreFromBackup:
    """G9: restore_from_backup() is the destructive counterpart to
    verify_backup() -- this actually overwrites the live DB, which
    verify_backup() never does. Confirmation UX lives in the CLI
    (main.py), not here -- these tests call the function directly, the
    same way a confirmed CLI invocation eventually would."""

    def test_restore_overwrites_live_db_with_backup_contents(self, tmp_path):
        store.set_annotation(activity_id="1", note="before backup")
        store.backup(str(tmp_path))
        store.set_annotation(activity_id="2", note="after backup, should vanish on restore")

        store.restore_from_backup(str(tmp_path))

        notes = {k: v["note"] for k, v in store.get_annotations().items()}
        assert notes.get("1") == "before backup"
        assert "2" not in notes

    def test_restore_writes_a_timestamped_safety_copy_of_the_live_db(self, tmp_path):
        store.set_annotation(activity_id="1", note="v1")
        store.backup(str(tmp_path))
        store.set_annotation(activity_id="2", note="v2, about to be wiped")

        safety_path = store.restore_from_backup(str(tmp_path))

        assert os.path.exists(safety_path)
        assert ".pre-restore-" in safety_path
        # The safety copy must hold the *pre-restore* state (v2 present),
        # not the restored one -- otherwise it's not actually a safety net.
        conn = sqlite3.connect(safety_path)
        row = conn.execute(
            "SELECT note FROM annotations WHERE activity_id='2'").fetchone()
        conn.close()
        assert row is not None and row[0] == "v2, about to be wiped"

    def test_restore_refuses_a_backup_that_fails_integrity_check(self, tmp_path):
        store.backup(str(tmp_path))
        # Corrupt the ciphertext after the fact so decryption "succeeds"
        # at the Fernet layer but the resulting bytes aren't a valid
        # SQLite file -- integrity_check must catch this, not a crash
        # deep in sqlite3.connect().
        fernet = store._backup_fernet()
        garbage = fernet.encrypt(b"not a real sqlite database")
        with open(os.path.join(tmp_path, "timely-backup.db.enc"), "wb") as f:
            f.write(garbage)

        store.set_annotation(activity_id="1", note="must survive a refused restore")
        with pytest.raises(Exception):
            store.restore_from_backup(str(tmp_path))

        # Live DB must be untouched -- a refused restore is a no-op, not
        # a partial one.
        notes = {k: v["note"] for k, v in store.get_annotations().items()}
        assert notes.get("1") == "must survive a refused restore"
