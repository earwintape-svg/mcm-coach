"""Lazy Garmin Connect client.

One connection per process, initialised on first use. If credentials are
missing or the network is down, the caller catches the exception and falls
back to the local store — same pattern as the original coach.py.
"""
import threading
from upload_garmin_workouts import get_client, api, is_plan_name  # noqa: F401

_client = None
_lock = threading.Lock()


def client():
    """Return the shared Garmin Connect session, creating it on first call."""
    global _client
    with _lock:
        if _client is None:
            _client = get_client()
        return _client
