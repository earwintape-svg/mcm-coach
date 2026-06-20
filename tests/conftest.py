"""Test fixtures — shared across the suite.

Uses an in-memory SQLite store so tests never touch the real database.

`import store` etc. work because the project is installed editable
(`pip install -e .` via pyproject.toml's py-modules/packages.find config
-- see T4 in ENGINEERING_REVIEW_TASKS.md). This used to sys.path-insert
the repo root by hand; if `import store` starts failing again, the fix is
re-running `pip install -e .[dev]`, not restoring the hack.
"""
import pytest


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Redirect store to a fresh temp DB for every test.

    Also redirects the G3 backup-encryption key to a per-test temp file --
    without this, every test touching store.backup()/verify_backup() would
    read/write the real ~/.timely_backup_key on whatever machine runs the
    suite, and tests could leak a real key into CI's home directory or
    collide with each other if run in parallel.
    """
    import store as _store
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_store, "DB_PATH", db_path)
    monkeypatch.setattr(_store, "_BACKUP_KEY_PATH", str(tmp_path / "test_backup_key"))
    # Reset the init guard so the new DB gets its schema created
    monkeypatch.setattr(_store, "_ready", False)
    yield
    monkeypatch.setattr(_store, "_ready", False)
