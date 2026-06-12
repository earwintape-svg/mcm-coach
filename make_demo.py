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
        runs.append({"activityId": s["date"], "date": s["date"], "mi": mi,
                     "paceSec": pace,
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
 function mulberry(seed){return function(){seed|=0;seed=(seed+0x6D2B79F5)|0;
  let t=Math.imul(seed^(seed>>>15),1|seed);t=(t+Math.imul(t^(t>>>7),61|t))^t;
  return ((t^(t>>>14))>>>0)/4294967296;};}
 window.demoRun=function(aid){
  const r=D.actuals.runs.find(x=>x.activityId===aid);
  if(!r)return {error:'not found'};
  const it=D.data.schedule.find(i=>i.date===r.date);
  const title=it?it.title:'';
  const target=title?D.data.plan.planTargets[title]:null;
  const rnd=mulberry(r.date.split('-').join('')*1);
  // laps: workout structure for interval days, mile splits otherwise
  const laps=[];
  const rep=title.match(/(\\d+)x ?(\\d+|Mile|Cat|Harlem)/);
  if(target&&rep){
   const n=Math.min(10,parseInt(rep[1],10));
   const raw=parseInt(rep[2],10);
   const repMi=rep[2]==='Mile'?1:(raw>30?raw/1609.34:(rep[2]==='Harlem'?0.4:0.2));
   laps.push({mi:1.5,paceSec:600+Math.round(rnd()*30)});
   for(let i=0;i<n;i++){
    laps.push({mi:repMi,paceSec:Math.round((target.fastSec+target.slowSec)/2+(rnd()-0.5)*16)});
    laps.push({mi:0.25,paceSec:660+Math.round(rnd()*40)});
   }
   laps.push({mi:1.5,paceSec:610+Math.round(rnd()*25)});
  }else{
   const n=Math.max(1,Math.round(r.mi));
   for(let i=0;i<n;i++){
    let p=r.paceSec+(rnd()-0.5)*20;
    if(target&&i===0)p=r.paceSec+55;          // warmup mile
    if(target&&i===n-1)p=r.paceSec-12;        // strong finish
    laps.push({mi:i===n-1?(r.mi-(n-1))||1:1,paceSec:Math.round(p)});
   }
  }
  // series + abstract riverside route
  const N=150,d=[],pc=[],hr=[],rt=[];
  let cum=0;const tot=laps.reduce((a,l)=>a+l.mi,0);
  for(let i=0;i<N;i++){
   const x=i/(N-1)*tot;d.push(Math.round(x*1000)/1000);
   let acc=0,lp=laps[laps.length-1];
   for(const l of laps){acc+=l.mi;if(x<=acc){lp=l;break;}}
   pc.push(Math.round(lp.paceSec+Math.sin(i*1.7)*9+(rnd()-0.5)*8));
   hr.push(Math.round(Math.min(184,128+x/tot*22+ (620-lp.paceSec)*0.12+Math.sin(i*0.9)*3)));
   const t=i/(N-1);
   rt.push([40.72+t*0.028+Math.sin(t*9)*0.0012,
            -74.012+Math.sin(t*3.1)*0.004+Math.cos(t*13)*0.0009]);
  }
  return {summary:{name:title?title+' — demo run':'Demo run',mi:r.mi,
    durSec:Math.round(r.mi*r.paceSec),paceSec:r.paceSec,
    avgHr:Math.round(hr.reduce((a,b)=>a+b,0)/hr.length),
    maxHr:Math.max.apply(null,hr),cad:157,elevFt:48},
   laps:laps,series:{d:d,pace:pc,hr:hr},route:rt};
 };
 window.fetch=async function(u,opts){
  let resp;u=String(u);
  if(u.startsWith('/api/move')){const b=JSON.parse(opts.body);
   const it=D.data.schedule.find(i=>String(i.scheduleId)===String(b.scheduleId));
   if(it)it.date=b.date;resp={ok:true};}
  else if(u.startsWith('/api/unschedule')){const b=JSON.parse(opts.body);
   const i=D.data.schedule.findIndex(x=>String(x.scheduleId)===String(b.scheduleId));
   if(i>=0)D.data.schedule.splice(i,1);resp={ok:true};}
  else if(u.startsWith('/api/shift_range')){const b=JSON.parse(opts.body);let n=0;
   D.data.schedule.forEach(i=>{if(i.date>=b.from&&i.date<=b.to){
    const d=new Date(i.date+'T12:00:00');d.setDate(d.getDate()+b.days);
    i.date=d.toISOString().slice(0,10);n++;}});resp={ok:true,moved:n};}
  else if(u.startsWith('/api/run/'))resp=demoRun(decodeURIComponent(u.split('/api/run/')[1]));
  else if(u.startsWith('/api/data'))resp=D.data;
  else if(u.startsWith('/api/actuals'))resp=D.actuals;
  else if(u.startsWith('/api/wellness'))resp=D.wellness;
  else if(u.startsWith('/api/weather'))resp={tempF:84,feelsF:88,humidity:72};
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
    marker = "<script>\nconst IS_MOBILE"
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
