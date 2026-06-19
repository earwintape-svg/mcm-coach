"""Test fixtures — shared across the suite.

Uses an in-memory SQLite store so tests never touch the real database.
"""
import sys
import os
from pathlib import Path
import pytest

# Ensure the project root is on sys.path so `import store` etc. work.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


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
