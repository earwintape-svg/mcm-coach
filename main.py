#!/usr/bin/env python3
"""MCM Coach — FastAPI application entry point.

Start directly:   uvicorn main:app --host 127.0.0.1 --port 8765
Via CLI shim:     python3 coach.py [--lan] [--port N] [--no-browser]
"""
from __future__ import annotations
import argparse
import os
import secrets
import socket
import struct
import sys
import threading
import webbrowser
import zlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from src.config import PORT, BACKUP_DIR
from src.api.routes import router, set_access_key
from src.services.notify import run_watcher, backup_loop

# ---------------------------------------------------------------------------
# App icon (pure stdlib PNG — no Pillow, no asset files, can't go missing)
# ---------------------------------------------------------------------------
_ICON: bytes | None = None


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
    threading.Thread(target=backup_loop, args=(bd,), daemon=True).start()
    threading.Thread(target=run_watcher, daemon=True).start()
    yield


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="timely — MCM coach", lifespan=lifespan)
app.include_router(router)


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


# ---------------------------------------------------------------------------
# CLI entry point (used by coach.py shim and lan.sh notify)
# ---------------------------------------------------------------------------
def lan_ip() -> str | None:
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
    ap.add_argument("command", nargs="?", choices=["serve", "notify"], default="serve")
    ap.add_argument("--lan", action="store_true", help="listen on Wi-Fi network (key-protected)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--weekly", action="store_true", help="with notify: send week-in-review")
    args = ap.parse_args()

    if args.command == "notify":
        from src.services.notify import cmd_notify
        cmd_notify(weekly=args.weekly)
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
