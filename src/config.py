"""Central settings — edit .env in the project root; never commit secrets.

All environment variables are read here and nowhere else. Other modules
import from this file instead of calling os.environ directly.
"""
from __future__ import annotations
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Lightweight .env loader (no python-dotenv dependency)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
_env = _ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
PORT: int = int(os.environ.get("TIMELY_PORT", "8765"))
WX_LAT: float = float(os.environ.get("COACH_LAT", "40.78"))
WX_LON: float = float(os.environ.get("COACH_LON", "-73.97"))
BACKUP_DIR: str = os.environ.get("TIMELY_BACKUP_DIR", str(_ROOT))
