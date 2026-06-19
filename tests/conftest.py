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
    """Redirect store to a fresh temp DB for every test."""
    import store as _store
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(_store, "DB_PATH", db_path)
    # Reset the init guard so the new DB gets its schema created
    monkeypatch.setattr(_store, "_ready", False)
    yield
    monkeypatch.setattr(_store, "_ready", False)
