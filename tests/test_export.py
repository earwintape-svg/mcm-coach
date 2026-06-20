"""Tests for store.export_all() (G4).

"Your proprietary data should never be trapped in a format only this app
reads" -- unlike backup()/verify_backup() (G1/G2/G3), this is deliberately
plain, unencrypted JSON: insurance that works even if this app, or the
unofficial Garmin API it depends on, stops existing. Uses the
isolated_store fixture from conftest.py, so this never touches the real
database.
"""
import json
import os

import store


class TestExportAll:
    def test_writes_one_json_file_per_table(self, tmp_path):
        written = store.export_all(str(tmp_path))
        for table in store.EXPORT_TABLES:
            path = tmp_path / (table + ".json")
            assert path.exists(), "%s.json was not written" % table
            assert table in written

    def test_row_counts_match_what_was_written(self, tmp_path):
        store.set_annotation(activity_id="1", note="a")
        store.set_annotation(activity_id="2", note="b")
        written = store.export_all(str(tmp_path))
        assert written["annotations"] == 2
        # gear always has the 2 seeded default shoes.
        assert written["gear"] >= 2

    def test_exported_json_round_trips_real_data(self, tmp_path):
        store.set_annotation(activity_id="42", note="hello export", rpe=7)
        store.export_all(str(tmp_path))
        rows = json.loads((tmp_path / "annotations.json").read_text())
        match = [r for r in rows if r["activity_id"] == "42"]
        assert len(match) == 1
        assert match[0]["note"] == "hello export"
        assert match[0]["rpe"] == 7

    def test_output_is_plaintext_not_encrypted(self, tmp_path):
        """Deliberately the opposite of backup()'s G3 behavior -- this
        export must be openable by anything, with no key required."""
        store.set_annotation(activity_id="1", note="plaintext check")
        store.export_all(str(tmp_path))
        raw = (tmp_path / "annotations.json").read_text()
        assert "plaintext check" in raw

    def test_creates_dest_dir_if_missing(self, tmp_path):
        nested = tmp_path / "does" / "not" / "exist" / "yet"
        store.export_all(str(nested))
        assert os.path.exists(nested / "runs.json")

    def test_empty_tables_export_as_empty_lists_not_errors(self, tmp_path):
        written = store.export_all(str(tmp_path))
        assert written["weekly_reviews"] == 0
        assert json.loads((tmp_path / "weekly_reviews.json").read_text()) == []
