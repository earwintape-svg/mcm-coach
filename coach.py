#!/usr/bin/env python3
"""MCM Coach — backward-compatible entry point shim.

The real application now lives in main.py + src/.
This file exists so that:
  - ./lan.sh restart (sync_app + launchd kickstart) continues to work
  - python3 coach.py notify [--weekly] still works for launchd notify jobs

To start the server directly:
  python3 coach.py [--lan] [--port N] [--no-browser]
  # or: uvicorn main:app --host 127.0.0.1 --port 8765
"""
from main import main

if __name__ == "__main__":
    main()
