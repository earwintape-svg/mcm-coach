#!/usr/bin/env python3
"""MCM Coach — FastAPI application entry point.

Start directly:   uvicorn main:app --host 127.0.0.1 --port 8765
Via CLI shim:     python3 coach.py [--lan] [--port N] [--no-browser]
"""
from __future__ import annotations
import argparse
import secrets
import socket
import struct
import sys
import threading
import time
import webbrowser
import zlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from src.config import PORT, BACKUP_DIR
from src.api.routes import router, set_access_key
from src.services.notify import run_watcher, backup_loop, restore_drill_loop, garmin_canary_loop
from src.services.applog import get_logger

log = get_logger("http")
_START_TIME = time.time()

# ---------------------------------------------------------------------------
# App icon (pure stdlib PNG — no Pillow, no asset files, can't go missing)
# ---------------------------------------------------------------------------
_ICON: Optional[bytes] = None


def _icon_png() -> bytes:
    global _ICON
    if _ICON is not None:
        return _ICON
    S, R = 180, 10.0
    BG, MINT, CORAL = (16, 20, 24), (93, 202, 165), (240, 153, 123)
    mint_segs = [(56, 56, 93, 90), (93, 90, 56, 124)]
    coral_segs = [(101, 68, 129, 90), (129, 90, 101, 113)]

    def dist(px, py, segs):
        best = 1e9
        for ax, ay, bx, by in segs:
            vx, vy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
            dx, dy = px - (ax + t * vx), py - (ay + t * vy)
            best = min(best, (dx * dx + dy * dy) ** 0.5)
        return best

    def mix(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    rows = []
    for y in range(S):
        row = bytearray()
        for x in range(S):
            c = BG
            d1, d2 = dist(x, y, mint_segs), dist(x, y, coral_segs)
            d, col = (d2, CORAL) if d2 <= d1 else (d1, MINT)
            if d <= R + 1:
                c = mix(c, col, max(0.0, min(1.0, R + 1 - d)))
            row += bytes(c)
        rows.append(bytes(row))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    raw = b"".join(b"\x00" + r for r in rows)
    _ICON = (b"\x89PNG\r\n\x1a\n"
             + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 2, 0, 0, 0))
             + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    return _ICON


# ---------------------------------------------------------------------------
# Static asset serving (hot-reload on mtime change)
# ---------------------------------------------------------------------------
_ASSET_DIR = Path(__file__).parent
_ASSETS: dict = {}


def _asset(fname: str) -> bytes:
    path = _ASSET_DIR / fname
    mt = path.stat().st_mtime
    hit = _ASSETS.get(fname)
    if hit is None or hit[0] != mt:
        _ASSETS[fname] = (mt, path.read_bytes())
    return _ASSETS[fname][1]


# ---------------------------------------------------------------------------
# LAN access key
# ---------------------------------------------------------------------------
def _load_key() -> str:
    key_file = Path.home() / ".mcm_coach_key"
    try:
        key = key_file.read_text().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(6)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


# ---------------------------------------------------------------------------
# Lifespan — background threads start here
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    bd = BACKUP_DIR or str(_ASSET_DIR)
    log.info("startup: launching background threads (backup_dir=%s)", bd)
    threading.Thread(target=backup_loop, args=(bd,), daemon=True, name="backup_loop").start()
    threading.Thread(target=run_watcher, daemon=True, name="run_watcher").start()
    threading.Thread(target=restore_drill_loop, args=(bd,), daemon=True, name="restore_drill_loop").start()
    threading.Thread(target=garmin_canary_loop, daemon=True, name="garmin_canary_loop").start()
    yield
    log.info("shutdown")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="timely — MCM coach", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """T11: per-request visibility. Before this, the only record of an
    HTTP error was whatever the client happened to see — nothing server-
    side correlated a user-reported issue with what actually ran."""
    start = time.time()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error: %s %s", request.method, request.url.path)
        raise
    ms = (time.time() - start) * 1000
    level = log.warning if response.status_code >= 400 else log.info
    level("%s %s -> %d (%.1fms)", request.method, request.url.path, response.status_code, ms)
    return response


@app.get("/apple-touch-icon.png", include_in_schema=False)
def icon():
    return Response(content=_icon_png(), media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


@app.get("/app.js", include_in_schema=False)
def appjs():
    return Response(content=_asset("app.js"),
                    media_type="application/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    return HTMLResponse(content=_asset("ui.html").decode(),
                        headers={"Cache-Control": "no-store"})


@app.get("/healthz", include_in_schema=False)
def healthz():
    """G12: trivial liveness probe. Deliberately unauthenticated (an
    external uptime check can't carry the LAN key) and deliberately does
    no DB/network work (a health check that can fail for reasons
    unrelated to "is the process up" defeats its own purpose). launchd's
    KeepAlive already restarts a *crashed* process -- this closes the
    other half of "is it actually on": a process that's still running but
    wedged (event loop deadlocked) won't trip KeepAlive but will fail to
    answer this. lan.sh's `healthcheck-on` polls this from a separate
    launchd job and pushes an alert if it doesn't get a 200."""
    return {"status": "ok", "uptime_sec": round(time.time() - _START_TIME, 1)}


# ---------------------------------------------------------------------------
# CLI entry point (used by coach.py shim and lan.sh notify)
# ---------------------------------------------------------------------------
def lan_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    import uvicorn

    ap = argparse.ArgumentParser(description="MCM Coach — training dashboard")
    ap.add_argument("command", nargs="?",
                     choices=["serve", "notify", "backup", "verify-backup",
                              "restore", "export-all"], default="serve")
    ap.add_argument("--lan", action="store_true", help="listen on Wi-Fi network (key-protected)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--weekly", action="store_true", help="with notify: send week-in-review")
    ap.add_argument("--out", default=None,
                     help="with export-all: output directory (default: ./timely-export-YYYY-MM-DD)")
    ap.add_argument("--yes", action="store_true",
                     help="with restore: skip the confirmation prompt (for scripting)")
    args = ap.parse_args()

    if args.command == "notify":
        from src.services.notify import cmd_notify
        cmd_notify(weekly=args.weekly)
        return

    if args.command == "backup":
        # G9: manual trigger for the same snapshot backup_loop takes
        # every 5 min -- useful right before something risky (a schema
        # change, an upload --force) when you don't want to wait.
        import store
        bd = BACKUP_DIR or str(_ASSET_DIR)
        dest = store.backup(bd)
        print("Backed up to %s" % dest)
        return

    if args.command == "verify-backup":
        # G2: manual trigger for the same drill restore_drill_loop runs
        # monthly -- "did the last backup actually load and check out?"
        # without waiting for the schedule.
        import store
        bd = BACKUP_DIR or str(_ASSET_DIR)
        try:
            counts = store.verify_backup(bd)
        except Exception as e:
            print("BACKUP VERIFICATION FAILED: %s" % e)
            sys.exit(1)
        print("Backup OK — %s" % counts)
        return

    if args.command == "restore":
        # G9: the destructive one. No automated path calls this -- it's
        # only ever a human, on purpose, after deciding the live DB needs
        # to go back to the last good snapshot. Confirms before
        # overwriting unless --yes (for scripting); store.restore_from_
        # backup() itself still refuses if the backup fails integrity_
        # check, and takes a timestamped safety copy of the current live
        # DB before overwriting it either way.
        import store
        bd = BACKUP_DIR or str(_ASSET_DIR)
        if not args.yes:
            resp = input(
                "This OVERWRITES the live database with the last backup "
                "in %s. Type 'yes' to continue: " % bd)
            if resp.strip().lower() != "yes":
                print("Aborted -- nothing changed.")
                return
        try:
            safety = store.restore_from_backup(bd)
        except Exception as e:
            print("RESTORE FAILED: %s" % e)
            sys.exit(1)
        print("Restored. Previous live DB saved to %s" % safety)
        return

    if args.command == "export-all":
        # G4: "your proprietary data should never be trapped in a format
        # only this app reads." Plain JSON, one file per table, no
        # encryption (deliberately -- meant to be openable by anything).
        import store
        from datetime import date
        out_dir = args.out or ("timely-export-%s" % date.today().isoformat())
        written = store.export_all(out_dir)
        print("Exported to %s/:" % out_dir)
        for table, n in written.items():
            print("  %-16s %d rows" % (table, n))
        return

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = "http://127.0.0.1:%d" % args.port
    print("Coach dashboard: %s  (Ctrl+C to stop)" % url)

    if args.lan:
        key = _load_key()
        set_access_key(key)
        ip = lan_ip()
        if ip:
            print("On your phone (same Wi-Fi): http://%s:%d/?key=%s" % (ip, args.port, key))
            print("Tip: in Safari, Share → Add to Home Screen for an app-like icon.")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=args.port, log_level="error")


if __name__ == "__main__":
    main()
