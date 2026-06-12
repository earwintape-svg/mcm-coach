#!/usr/bin/env python3
"""Build the portfolio demo: a single static HTML file with the full coach UI
running on a synthetic athlete — no Garmin account, no backend, no secrets.

Run:    python3 make_demo.py        →  demo/index.html

Deploy: push demo/ anywhere static (GitHub Pages, Vercel, Netlify).
The demo is generated FROM the real app's UI (coach.PAGE), so it never
drifts from the product. All /api/* calls are intercepted by a fetch shim
backed by embedded sample data; moves and vacation mode work (in-memory).

The synthetic athlete is mid-week-6: mostly on-target runs, one missed day,
one slow tempo, and a short-sleep morning so the readiness banner shows.
"""
import json
import os
import random
from datetime import timedelta

import coach
from plan import build_plan, PLAN_START

random.seed(42)
DEMO_TODAY = PLAN_START + timedelta(days=38)   # Thursday, week 6


def build_demo_data():
    plan_sum = coach.plan_summary()
    plan_sum["today"] = DEMO_TODAY.isoformat()

    schedule = []
    for i, p in enumerate(build_plan()):
        schedule.append({"scheduleId": 1000 + i, "workoutId": 5000 + i,
                         "title": p["name"], "date": p["date"].isoformat()})

    runs, weekly = [], {}
    for s in schedule:
        if s["date"] >= DEMO_TODAY.isoformat():
            continue
        if s["title"] == "W3 Fri 6mi Easy":      # the honest missed day
            continue
        planned = plan_sum["planMiles"][s["title"]]
        target = plan_sum["planTargets"][s["title"]]
        mi = round(planned * random.uniform(0.96, 1.06), 2)
        if target:
            pace = int((target["fastSec"] + target["slowSec"]) / 2 + random.uniform(-8, 8))
            if s["title"] == "W5 Thu Tempo 3mi":  # one rough day, amber verdict
                pace = target["slowSec"] + 28
        else:
            pace = random.randint(585, 615)       # easy ~9:45-10:15
        runs.append({"date": s["date"], "mi": mi, "paceSec": pace,
                     "pace": "%d:%02d" % (pace // 60, pace % 60),
                     "name": s["title"]})
        wk = int(s["title"].split(" ")[0][1:])
        weekly[str(wk)] = round(weekly.get(str(wk), 0.0) + mi, 1)

    days = []
    for i in range(7):
        d = (DEMO_TODAY - timedelta(days=i)).isoformat()
        if i == 0:   # short sleep this morning → readiness banner demo
            days.append({"date": d, "rhr": 49, "sleepH": 5.7, "bb": 52})
        else:
            days.append({"date": d, "rhr": random.randint(45, 48),
                         "sleepH": round(random.uniform(6.8, 7.9), 1),
                         "bb": random.randint(70, 92)})

    return {"data": {"plan": plan_sum, "schedule": schedule},
            "actuals": {"weekly": weekly, "runs": runs},
            "wellness": {"days": days}}


SHIM = """const DEMO=%s;
(function(){
 const D=DEMO;
 window.fetch=async function(u,opts){
  let resp;u=String(u);
  if(u.startsWith('/api/move')){const b=JSON.parse(opts.body);
   const it=D.data.schedule.find(i=>String(i.scheduleId)===String(b.scheduleId));
   if(it)it.date=b.date;resp={ok:true};}
  else if(u.startsWith('/api/shift_range')){const b=JSON.parse(opts.body);let n=0;
   D.data.schedule.forEach(i=>{if(i.date>=b.from&&i.date<=b.to){
    const d=new Date(i.date+'T12:00:00');d.setDate(d.getDate()+b.days);
    i.date=d.toISOString().slice(0,10);n++;}});resp={ok:true,moved:n};}
  else if(u.startsWith('/api/data'))resp=D.data;
  else if(u.startsWith('/api/actuals'))resp=D.actuals;
  else if(u.startsWith('/api/wellness'))resp=D.wellness;
  else resp={error:'not found'};
  return {json:async()=>resp};
 };
 addEventListener('DOMContentLoaded',function(){
  const b=document.createElement('a');
  b.textContent='DEMO \\u00b7 sample data';
  b.title='Interactive demo with a synthetic athlete. No real accounts, no backend.';
  b.style.cssText='position:fixed;top:10px;right:10px;background:#5aa2ff;color:#fff;'+
   'font:600 11px -apple-system,sans-serif;padding:5px 11px;border-radius:999px;'+
   'z-index:99;letter-spacing:.5px;text-decoration:none';
  document.body.appendChild(b);
 });
})();"""


def main():
    data = build_demo_data()
    shim = "<script>" + SHIM % json.dumps(data) + "</script>\n"
    marker = "<script>\nlet S="
    assert marker in coach.PAGE, "coach.PAGE layout changed; update make_demo.py"
    html = coach.PAGE.replace(marker, shim + marker)
    html = html.replace("<title>MCM Coach</title>",
                        "<title>MCM Coach — interactive demo</title>")
    os.makedirs("docs", exist_ok=True)   # GitHub Pages serves from /docs
    out = os.path.join("docs", "index.html")
    with open(out, "w") as f:
        f.write(html)
    print("Built %s (%.0f KB) — today is %s (week 6) in demo-land."
          % (out, os.path.getsize(out) / 1024.0, DEMO_TODAY))
    print("Deploy: GitHub repo Settings -> Pages -> branch main, folder /docs.")


if __name__ == "__main__":
    main()
