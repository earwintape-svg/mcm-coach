"""Weather service — current conditions from Open-Meteo (free, no key)."""
import json
import urllib.request
from datetime import date

import store
from src.services import cache
from src.config import WX_LAT, WX_LON


def heat_pct(wx: dict) -> float:
    """Pace slowdown for heat: +0.4%/°F above 60°F apparent, +1% if humid,
    capped at 10%. Display-only — the watch plan never changes."""
    if not wx or wx.get("tempF") is None:
        return 0.0
    feels = wx.get("feelsF") or wx["tempF"]
    excess = max(0, feels - 60)
    pct = excess * 0.004
    if excess > 0 and (wx.get("humidity") or 0) >= 65:
        pct += 0.01
    return round(min(0.10, pct), 3)


def fetch_weather() -> dict:
    """Current conditions, 30-minute cache."""
    if cache.get("wx") is not None and cache.fresh("wx_ts", 1800):
        return cache.get("wx")
    out: dict = {}
    try:
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
               "&current=temperature_2m,apparent_temperature,relative_humidity_2m"
               "&temperature_unit=fahrenheit" % (WX_LAT, WX_LON))
        with urllib.request.urlopen(url, timeout=6) as r:
            cur = (json.load(r).get("current") or {})
        out = {
            "tempF": round(cur.get("temperature_2m") or 0),
            "feelsF": round(cur.get("apparent_temperature") or 0),
            "humidity": cur.get("relative_humidity_2m"),
        }
    except Exception as e:
        out = {"error": str(e)}
    if out.get("tempF") is not None:
        out["heatPct"] = heat_pct(out)
        store.save_weather(date.today().isoformat(), out)
    cache.set("wx", out, "wx_ts")
    return out
