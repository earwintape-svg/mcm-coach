"""Structured logging (T11/G10).

Before this, the only visibility into the running server was whatever got
print()'d to stdout, captured by launchd into a single never-rotated
~/Library/Logs/mcmcoach.log. Two concrete gaps that motivated this:
  - The FastAPI layer logged nothing per-request -- no way to see what was
    slow, what was erroring, or correlate a user-reported issue with what
    actually happened (T11).
  - notify.py's run_watcher()/backup_loop() background threads catch every
    exception and silently continue (`except Exception: pass`) -- resilient
    to a single bad iteration, but a *persistent* failure (Garmin auth
    broken, disk full) would loop forever with zero signal (G10).

get_logger() returns a logger writing to a rotating file (5MB x 3 backups)
at ~/Library/Logs/timely.log, separate from launchd's raw stdout capture
so structured entries don't interleave with arbitrary print() output.
"""
import logging
import logging.handlers
import os

LOG_PATH = os.path.expanduser("~/Library/Logs/timely.log")

_configured = False


def _configure():
    global _configured
    if _configured:
        return
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    root = logging.getLogger("timely")
    root.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """name is typically __name__ or a short component tag like 'http'."""
    _configure()
    return logging.getLogger("timely." + name)
