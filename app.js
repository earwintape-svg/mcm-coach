function escapeHTML(s){
 return String(s==null?'':s).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function shoeName(key){
 const g=(S.gear||[]).find(x=>x.key===key);
 return g?(g.nickname||g.display):key;
}
const IS_MOBILE=matchMedia('(max-width:700px)').matches;
let S={schedule:[],plan:null,runs:[],weeklyActual:{},wellness:null,fitForm:null,month:null,
 prs:null,undo:null,moveItem:null,selDate:null,
 planMode:(function(){try{return localStorage.getItem('coachPlanMode')||(IS_MOBILE?'list':'month');}
  catch(e){return 'month';}})()};
function setPlanMode(m){S.planMode=m;try{localStorage.setItem('coachPlanMode',m);}catch(e){}render();}
const DAY=864e5;
const fmt=d=>d.toISOString().slice(0,10);
const parse=s=>new Date(s+'T12:00:00');
const fmtPace=s=>s?Math.floor(s/60)+':'+String(s%60).padStart(2,'0'):'—';

function kind(t){
 if(/MP Finish|mi LR/.test(t))return'c-long';
 if(/Strides/.test(t))return'c-strides';
 if(/Tempo|Hill/.test(t))return'c-tempo';
 if(/\dx/.test(t))return'c-hard';
 return'c-easy';
}
const isHard=t=>/Tempo|Hill|\dx|MP Finish/.test(t);
const kindVar={'c-easy':'easy','c-strides':'strides','c-tempo':'tempo','c-hard':'hard','c-long':'long'};

function toast(msg,opts){
 opts=opts||{};
 const t=document.getElementById('toast');
 t.className=opts.err?'err':'';
 t.innerHTML=msg+(opts.undo?' <button class="ghost" onclick="doUndo()">Undo</button>':'')+
   (opts.cancel?' <button class="ghost" onclick="exitMoveMode()">Cancel</button>':'');
 t.style.display='flex';
 clearTimeout(t._h);
 if(!opts.sticky)t._h=setTimeout(()=>t.style.display='none',opts.undo?6000:3000);
}
function hideToast(){document.getElementById('toast').style.display='none';}
const KEY=new URLSearchParams(location.search).get('key')||'';
async function jget(u){const r=await fetch(u,{headers:{'X-Key':KEY}});
 const j=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(j.detail||j.error||('HTTP '+r.status));
 if(j.error)throw new Error(j.error);return j;}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'X-Key':KEY},body:JSON.stringify(b)});
 const j=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(j.detail||j.error||('HTTP '+r.status));
 if(j.error)throw new Error(j.error);return j;}

async function load(force){
 try{
  const d=await jget('/api/data'+(force?'?refresh=1':''));
  S.plan=d.plan;S.schedule=d.schedule;
  if(S.month===null){
   const t=parse(S.plan.today),lo=parse(S.plan.start),hi=parse(S.plan.race);
   const c=t<lo?lo:(t>hi?hi:t);S.month=c.getFullYear()*12+c.getMonth();
  }
  render();
  jget('/api/actuals').then(j=>{S.runs=j.runs||[];S.weeklyActual=j.weekly||{};
   S.ann=j.ann||{};if(j.stale)toast('Garmin unreachable — showing locally saved data',{err:1});
   render();}).catch(()=>{});
  jget('/api/wellness').then(j=>{S.wellness=j;render();}).catch(()=>{});
  jget('/api/fitness_form').then(j=>{S.fitForm=j;render();}).catch(()=>{});
  jget('/api/weather').then(j=>{S.weather=j;render();}).catch(()=>{});
  jget('/api/fitness').then(j=>{S.fit=j;render();}).catch(()=>{});
  jget('/api/gear').then(j=>{S.gear=j.gear||[];render();}).catch(()=>{});
  jget('/api/trends').then(j=>{S.trends=j;render();}).catch(()=>{});
  jget('/api/review').then(j=>{S.review=j.review;render();}).catch(()=>{});
  jget('/api/coach').then(j=>{S.props=(j.proposals||[]);render();}).catch(()=>{});
  jget('/api/other_activities').then(j=>{S.otherActs=j.activities||[];render();}).catch(()=>{});
  jget('/api/prs').then(j=>{S.prs=j;render();}).catch(()=>{});
 }catch(e){toast('Couldn’t reach Garmin: '+escapeHTML(e.message),{err:1});}
}
function nav(d){S.month+=d;render();}

async function syncCalendar(){
 const btn=document.getElementById('syncCalBtn');
 if(btn){btn.disabled=true;btn.textContent='Syncing…';}
 try{
  const r=await jpost('/api/sync_calendar',{});
  toast(`Calendar synced — ${r.created} added, ${r.updated} updated, ${r.deleted} removed`);
 }catch(e){
  toast(escapeHTML(e.message),{err:1,sticky:true});
 }finally{
  if(btn){btn.disabled=false;btn.textContent='📅 Sync to Calendar';}
 }
}

function setView(v){
 S.view=v;
 const m={today:'v-today',plan:'v-plan',acts:'v-acts'};
 Object.keys(m).forEach(k=>document.getElementById(m[k]).style.display=k===v?'':'none');
 document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.v===v));
 try{localStorage.setItem('coachView',v);}catch(e){}
 window.scrollTo(0,0);
}

/* ---------------- readiness ---------------- */
function readiness(){
 const w=S.wellness;
 if(!w||!w.days||!w.days.length)return null;
 const today=w.days[0],hist=w.days.slice(1).filter(d=>d.rhr);
 const base=hist.length?Math.round(hist.reduce((a,d)=>a+d.rhr,0)/hist.length):null;
 const flags=[];
 if(today.rhr&&base&&today.rhr-base>=5)flags.push('resting HR +'+(today.rhr-base)+' over your baseline');
 if(today.sleepH!==null&&today.sleepH>0&&today.sleepH<6)flags.push('only '+today.sleepH+'h sleep');
 if(today.bb!==null&&today.bb!==undefined&&today.bb<30)flags.push('Body Battery at '+today.bb);
 const ff=S.fitForm;
 if(ff&&ff.hrvReadiness&&ff.hrvReadiness.baseline>0){
  const pct=(ff.hrvReadiness.recent-ff.hrvReadiness.baseline)/ff.hrvReadiness.baseline*100;
  if(pct<=-10)flags.push('HRV '+Math.round(Math.abs(pct))+'% below your 60-day baseline');
 }
 if(ff&&ff.formToday!==undefined&&ff.formToday<-20)flags.push('training form at '+ff.formToday+' (carrying real fatigue)');
 return {today:today,base:base,flags:flags,level:flags.length>=2?'red':(flags.length?'amber':'ok'),fitForm:ff};
}

function render(){
 if(!S.plan)return;
 const today=S.plan.today;
 document.getElementById('cdays').textContent=
   Math.max(0,Math.round((parse(S.plan.race)-parse(today))/DAY));
 document.getElementById('nsched').textContent=S.schedule.length;
 const wk=Math.floor((parse(today)-parse(S.plan.start))/DAY/7)+1;
 // miles this week = actual runs since Monday, independent of plan weeks
 const mon=new Date(parse(today));mon.setDate(mon.getDate()-((mon.getDay()+6)%7));
 const ranWk=S.runs.filter(r=>r.date>=fmt(mon)&&r.date<=today).reduce((a,r)=>a+r.mi,0);
 const planWk=S.plan.plannedWeekly[wk];
 document.getElementById('wkmi').textContent=
   Math.round(ranWk)+(planWk?' / '+Math.round(planWk):'');
 renderStrip(today);
 renderReview();
 renderCoach();
 renderBrief(today);
 renderQuickLog(today);
 renderTrends();
 renderFitForm();
 renderCountdown();
 renderPlanTab(today);
 renderWeek(today);
 renderChart();
 renderRamp(wk);
 renderPRs();
 renderActs();
 renderOtherActs();
}

function renderPlanTab(today){
 const list=S.planMode==='list';
 document.getElementById('segList').className=list?'on':'';
 document.getElementById('segMonth').className=list?'':'on';
 document.getElementById('mnav').style.display=list?'none':'';
 document.getElementById('calwrap').style.display=list?'none':'';
 document.getElementById('planlist').style.display=list?'':'none';
 if(list)renderPlanList(today);else renderGrid(today);
}

function renderPlanList(today){
 const el=document.getElementById('planlist');
 const items=S.schedule.slice().sort((a,b)=>a.date.localeCompare(b.date));
 const ranByDate={};
 S.runs.forEach(r=>ranByDate[r.date]=(ranByDate[r.date]||0)+r.mi);
 let h='',lastWk=null;
 items.forEach(it=>{
  const wk=Math.floor((parse(it.date)-parse(S.plan.start))/DAY/7)+1;
  if(wk!==lastWk){
   h+='<div style="color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.7px;margin:16px 0 4px">'+
    'Week '+wk+' · '+(S.plan.plannedWeekly[wk]||'?')+' mi</div>';
   lastWk=wk;
  }
  const past=it.date<today, isT=it.date===today, ran=ranByDate[it.date];
  let status;
  if(ran)status='<span style="color:var(--good);font-weight:700">✓ '+ran.toFixed(1)+'</span>';
  else if(isT)status='<span style="color:var(--accent);font-weight:700">today</span>';
  else if(past)status='<span style="color:var(--hard)">missed</span>';
  else status='<span style="color:var(--faint)">›</span>';
  h+='<div onclick="openDetail('+it.scheduleId+')" style="display:flex;gap:11px;align-items:center;'+
   'padding:10px 2px;border-top:1px solid var(--line);cursor:pointer'+(past?';opacity:.55':'')+'">'+
   '<span style="width:64px;flex:none;color:'+(isT?'var(--accent)':'var(--dim)')+';font-size:12.5px;font-weight:600">'+
    parse(it.date).toLocaleDateString(undefined,{weekday:'short',day:'numeric'})+'</span>'+
   '<span style="width:9px;height:9px;border-radius:5px;flex:none;background:var(--'+kindVar[kind(it.title)]+')"></span>'+
   '<b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+
    escapeHTML(it.title.replace(/^W\d+ \w+ /,''))+'</b>'+
   '<span style="color:var(--dim);font-size:12.5px">'+(S.plan.planMiles[it.title]||'?')+' mi</span>'+
   status+'</div>';
 });
 el.innerHTML=h||'<p style="color:var(--dim)">No scheduled workouts — run upload first.</p>';
}

function setActFilter(f){S.actFilter=f;render();}
function runKind(r){
 const it=S.schedule.find(i=>i.date===r.date);
 if(!it)return 'Easy';
 if(/mi LR|MP Finish/.test(it.title))return 'Long';
 return isHard(it.title)?'Quality':'Easy';
}
function renderActs(){
 const el=document.getElementById('actpanel');
 let runs=S.runs.slice().sort((a,b)=>b.date.localeCompare(a.date));
 if(!runs.length){
  el.innerHTML='<h3>Activities</h3><p style="color:var(--dim)">Completed runs land here automatically once training starts — tap any for the full breakdown.</p>';
  return;
 }
 const tot=runs.reduce((a,r)=>a+r.mi,0);
 let h='<h3>Activities <span style="color:var(--dim);font-weight:400">— '+
   runs.length+' runs · '+tot.toFixed(0)+' mi</span></h3>';
 // insight chips: 30-day volume, on-target rate, average RPE
 const cutoff=fmt(new Date(parse(S.plan.today).getTime()-30*DAY));
 const r30=runs.filter(r=>r.date>=cutoff);
 let hit=0,judged=0;
 r30.forEach(r=>{const it=S.schedule.find(i=>i.date===r.date);
  if(!it)return;const a=assess(it);if(!a)return;
  judged++;if(a.distOk&&a.paceOk!==false)hit++;});
 const rpes=Object.values(S.ann||{}).map(a=>a.rpe).filter(Boolean);
 h+='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px;margin:4px 0 10px">'+
  '<div style="background:var(--cell);border-radius:10px;padding:8px 10px;text-align:center"><b style="font-size:17px">'+
   r30.reduce((a,r)=>a+r.mi,0).toFixed(0)+'</b><div style="color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px">mi · 30d</div></div>'+
  '<div style="background:var(--cell);border-radius:10px;padding:8px 10px;text-align:center"><b style="font-size:17px">'+
   (judged?Math.round(hit/judged*100)+'%':'—')+'</b><div style="color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px">on target</div></div>'+
  '<div style="background:var(--cell);border-radius:10px;padding:8px 10px;text-align:center"><b style="font-size:17px">'+
   (rpes.length?(rpes.reduce((a,b)=>a+b,0)/rpes.length).toFixed(1):'—')+'</b><div style="color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px">avg RPE</div></div></div>';
 // gear
 const activeGear=(S.gear||[]).filter(g=>!g.retired);
 if(activeGear.length){
  h+='<h3 style="font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.7px;margin:14px 0 6px">Gear</h3>';
  activeGear.forEach(g=>{
   const pct=Math.min(100,g.mi/(g.threshold+100)*100);
   const col=g.mi>=g.threshold+50?'var(--hard)':(g.mi>=g.threshold-50?'var(--tempo)':'var(--good)');
   h+='<div onclick="gearEdit(\''+g.key.replace(/'/g,'')+'\')" style="padding:6px 0;cursor:pointer">'+
    '<div style="display:flex;justify-content:space-between;font-size:13px"><b>'+
    escapeHTML(g.nickname||g.display)+(g.isDefault?' <span style="color:var(--faint);font-weight:400">· default</span>':'')+'</b>'+
    '<span style="color:'+col+'">'+g.mi.toFixed(0)+' mi'+(g.mi>=g.threshold?' · time to retire?':'')+'</span></div>'+
    '<div style="background:var(--cell);height:5px;border-radius:3px;margin-top:4px">'+
    '<div style="width:'+pct.toFixed(0)+'%;height:5px;border-radius:3px;background:'+col+'"></div></div></div>';
  });
 }
 // filters
 h+='<div style="display:flex;gap:6px;margin:12px 0 2px">'+
  ['All','Quality','Easy','Long'].map(f=>'<button onclick="setActFilter(\''+f+'\')" '+
   ((S.actFilter||'All')===f?'class="primary" ':'')+'style="padding:5px 13px;font-size:12px">'+f+'</button>').join('')+'</div>';
 const flt=S.actFilter||'All';
 if(flt!=='All')runs=runs.filter(r=>runKind(r)===flt);
 if(S.fit&&S.fit.current){
  const gap=S.fit.goalGap,onTrack=gap<=0;
  const vo2=(S.fitForm&&S.fitForm.vo2max&&S.fitForm.vo2max.length)?
   S.fitForm.vo2max[S.fitForm.vo2max.length-1].v:null;
  h+='<div style="background:var(--cell);border-radius:12px;padding:11px 14px;margin:4px 0 10px">'+
   '<b>Fitness check:</b> VDOT '+S.fit.current+' → projects a <b style="color:'+
   (onTrack?'var(--good)':'var(--tempo)')+'">'+S.fit.marathon+'</b> marathon '+
   (onTrack?'— ahead of sub-3:25':'— '+Math.round(Math.abs(gap)/60)+' min off sub-3:25 (training-run floor; races read faster)')+
   (vo2?' <span style="color:var(--faint)">· Suunto VO2max '+vo2+'</span>':'')+
   '<div style="color:var(--faint);font-size:11.5px;margin-top:3px">Estimated from your training runs via Daniels VDOT — the trend matters more than the number.</div>'+
   (S.fit.efTrendPct!=null?'<div style="font-size:11.5px;margin-top:4px;color:'+
    (S.fit.efTrendPct>=0?'var(--good)':'var(--tempo)')+'">Efficiency (pace per heartbeat) '+
    (S.fit.efTrendPct>=0?'up ':'down ')+Math.abs(S.fit.efTrendPct)+
    '% vs prior weeks — '+(S.fit.efTrendPct>=0?'an early sign fitness is building':'worth watching')+
    ', independent of VDOT.</div>':'')+'</div>';
 }
 const prAids=new Set(Object.values(S.prs||{}).map(p=>String(p.activityId||'')).filter(Boolean));
 let lastWk=null;
 runs.forEach(r=>{
  const wk=Math.floor((parse(r.date)-parse(S.plan.start))/DAY/7)+1;
  if(wk!==lastWk){
   h+='<div style="color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.7px;margin:14px 0 2px">'+
    (wk<1?'Tune-up':'Week '+wk+
    (S.weeklyActual[wk]?' · '+S.weeklyActual[wk]+' of '+(S.plan.plannedWeekly[wk]||'?')+' mi':''))+'</div>';
   lastWk=wk;
  }
  const it=S.schedule.find(i=>i.date===r.date);
  const title=escapeHTML((it?it.title:r.name).replace(/^W\d+ \w+ /,''));
  const a=it?assess(it):null;
  const col=a?(a.distOk&&a.paceOk!==false?'var(--good)':'var(--tempo)'):'var(--dim)';
  const isPR=prAids.has(String(r.activityId));
  h+='<div onclick="openRun(\''+r.activityId+'\',\''+(it?it.title.replace(/'/g,''):'')+'\')"'+
   ' style="display:flex;gap:11px;align-items:center;padding:10px 0;border-top:1px solid var(--line);cursor:pointer">'+
   '<div style="width:9px;height:9px;border-radius:5px;flex:none;background:var(--'+
     (it?kindVar[kind(it.title)]:'easy')+')"></div>'+
   '<div style="flex:1;min-width:0"><b>'+title+'</b>'+
   '<div style="color:var(--dim);font-size:12px">'+
     parse(r.date).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'</div></div>'+
   '<div style="text-align:right"><b>'+r.mi.toFixed(1)+' mi</b>'+
   '<div style="color:'+col+';font-size:12px">'+(r.pace?r.pace+'/mi':'')+
     (a&&a.paceMsg?' · '+a.paceMsg:'')+'</div></div>'+
   (isPR?'<span style="font-size:15px" title="Personal record">🏆</span>':'')+
   (r.compliance!=null?'<span style="background:var(--cell);border:1px solid var(--line);border-radius:999px;'+
    'padding:3px 8px;font-size:11px;color:'+(r.compliance>=85?'var(--good)':'var(--tempo)')+'">'+
    Math.round(r.compliance)+'%</span>':'')+
   (((S.ann||{})[String(r.activityId)]||{}).rpe?
    '<span style="background:var(--cell);border:1px solid var(--line);border-radius:999px;'+
    'padding:3px 8px;font-size:11px;color:var(--dim)">RPE '+S.ann[String(r.activityId)].rpe+'</span>':'')+
   '<span style="color:var(--faint)">›</span></div>';
 });
 el.innerHTML=h;
}

const sportIcon={Ride:'🚴',VirtualRide:'🚴',Swim:'🏊',WeightTraining:'🏋️',
 Workout:'🏋️',Walk:'🚶',Hike:'🥾',Yoga:'🧘',RockClimbing:'🧗',Rowing:'🚣'};
function renderOtherActs(){
 const el=document.getElementById('otheractpanel');
 const acts=(S.otherActs||[]).slice(0,15);
 if(!acts.length){el.style.display='none';return;}
 el.style.display='';
 let h='<h3>Other activities <span style="color:var(--dim);font-weight:400">— cross-training synced from your watch</span></h3>';
 acts.forEach(a=>{
  h+='<div onclick="openRun(\''+a.activityId+'\',\'\')" style="display:flex;gap:11px;align-items:center;'+
   'padding:10px 0;border-top:1px solid var(--line);cursor:pointer">'+
   '<div style="font-size:18px;flex:none">'+(sportIcon[a.type]||'⚡')+'</div>'+
   '<div style="flex:1;min-width:0"><b>'+escapeHTML(a.name)+'</b>'+
   '<div style="color:var(--dim);font-size:12px">'+a.type+' · '+
    parse(a.date).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'</div></div>'+
   '<div style="text-align:right"><b>'+(a.durationSec?fmtDur(a.durationSec):'—')+'</b>'+
   (a.avgHr?'<div style="color:var(--dim);font-size:12px">avg HR '+a.avgHr+'</div>':'')+'</div>'+
   '<span style="color:var(--faint)">›</span></div>';
 });
 el.innerHTML=h;
}

function renderStrip(today){
 const t=parse(today);
 const mon=new Date(t);mon.setDate(t.getDate()-((t.getDay()+6)%7));
 let h='';
 for(let i=0;i<7;i++){
  const d=new Date(mon.getTime()+i*DAY),ds=fmt(d);
  const its=S.schedule.filter(x=>x.date===ds);
  const ran=runsOn(ds).length>0;
  h+='<div class="wd'+(ds===today?' today':'')+(ds===S.selDate?' sel':'')+'" onclick="stripTap(\''+ds+'\')">'+
   '<div class="l">'+['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][i]+'</div>'+
   '<span class="n">'+d.getDate()+'</span>'+
   '<div class="dots">'+(ran?'<span class="ck">✓</span>':
    its.slice(0,3).map(x=>'<i style="background:var(--'+kindVar[kind(x.title)]+')"></i>').join(''))+
   '</div></div>';
 }
 document.getElementById('wstrip').innerHTML=h;
}
function stripTap(ds){
 S.selDate=(S.selDate===ds)?null:ds;   // tap again to return to today
 render();
}
function renderCoach(){
 const el=document.getElementById('coachcard');
 if(!el)return;
 const p=(S.props||[])[0];
 if(!p||S.propsDismissed){el.style.display='none';return;}
 const to=p.to?parse(p.to).toLocaleDateString(undefined,{weekday:'long',month:'short',day:'numeric'}):null;
 el.innerHTML='<div class="top"><div style="flex:1;min-width:0">'+
  '<b>Coach suggestion</b>'+
  '<div class="sub">'+escapeHTML(p.reason)+'</div>'+
  '<div class="sub">'+escapeHTML(p.title.replace(/^W\d+ \w+ /,''))+' ('+p.date+') → '+
  (p.action==='move'?'<b>'+to+'</b>':'<b>skip it</b>')+'</div></div></div>'+
  '<div class="cta"><button onclick="S.propsDismissed=1;render()">Later</button>'+
  '<button class="primary" onclick="applyCoach()">Apply</button></div>';
 el.style.display='block';
}
async function applyCoach(){
 const p=(S.props||[])[0];
 if(!p)return;
 try{
  await jpost('/api/coach/apply',{action:p.action,scheduleId:p.scheduleId,
   workoutId:p.workoutId,to:p.to});
  toast(p.action==='move'?'Rescheduled — sync your watch':'Absorbed. Eyes forward.');
  S.props=[];load(true);
 }catch(e){toast('Apply failed: '+escapeHTML(e.message),{err:1});}
}

function renderReview(){
 const el=document.getElementById('reviewcard'),r=S.review;
 if(!r){el.style.display='none';return;}
 el.innerHTML='<div class="top"><div style="flex:1;min-width:0">'+
  '<b>Week '+r.week+' in review</b>'+
  '<div class="sub">'+r.mi.toFixed(1)+' of '+Math.round(r.planned||0)+' mi · '+
  r.runs+'/'+r.plannedRuns+' runs'+(r.judged?' · on target '+r.onTarget+' of '+r.judged:'')+
  (r.vdot?' · VDOT '+r.vdot:'')+'</div>'+
  '<div class="sub" style="color:var(--accent)">'+r.line+'</div></div></div>';
 el.style.display='block';
}

function sparkSvg(vals,color,goodDown){
 if(!vals||vals.length<3)return'';
 const v0=Math.min(...vals),v1=Math.max(...vals),sp=(v1-v0)||1,W=300,H=30;
 const pts=vals.map((v,i)=>((i/(vals.length-1))*W).toFixed(1)+','+
   (H-3-((v-v0)/sp)*(H-6)).toFixed(1)).join(' ');
 return '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:30px">'+
  '<polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="2"/></svg>';
}
function renderTrends(){
 const el=document.getElementById('trendpanel'),t=S.trends;
 if(!t||((t.rhr||[]).length<5&&(t.easy||[]).length<2)){if(el)el.style.display='none';return;}
 let h='<h3>Trends</h3>';
 if((t.rhr||[]).length>=5){
  const vs=t.rhr.map(x=>x.v),last=vs[vs.length-1],first=vs[0],d=last-first;
  h+='<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:4px">'+
   '<span style="color:var(--dim);font-size:13px">Resting HR · 30d</span>'+
   '<span><b>'+last+'</b> <span style="font-size:11.5px;color:'+(d<=0?'var(--good)':'var(--tempo)')+'">'+
   (d>0?'▲':'▼')+Math.abs(d)+'</span></span></div>'+sparkSvg(vs,'#3ec6c0');
 }
 if((t.easy||[]).length>=2){
  const vs=t.easy.map(x=>x.v),last=vs[vs.length-1],d=last-vs[0];
  h+='<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:8px">'+
   '<span style="color:var(--dim);font-size:13px">Easy pace · weekly median</span>'+
   '<span><b>'+fmtPace(last)+'</b> <span style="font-size:11.5px;color:'+(d<=0?'var(--good)':'var(--tempo)')+'">'+
   (d>0?'▲':'▼')+fmtPace(Math.abs(d))+'</span></span></div>'+sparkSvg(vs,'#5DCAA5');
 }
 h+='<div style="color:var(--faint);font-size:11px;margin-top:6px">RHR down = adapting · easy pace down at the same effort = fitness</div>';
 if(t.sleepPerf&&(t.sleepPerf.good||t.sleepPerf.poor)){
  const g=t.sleepPerf.good,p=t.sleepPerf.poor;
  const diff=(g&&p)?p.avgPace-g.avgPace:null;
  h+='<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">'+
   '<div style="color:var(--dim);font-size:13px;margin-bottom:6px">Sleep vs pace</div>'+
   (g?'<div style="display:flex;justify-content:space-between;font-size:12.5px;margin:3px 0">'+
    '<span>7h+ sleep <span style="color:var(--faint)">('+g.n+' runs)</span></span>'+
    '<b style="color:var(--good)">'+g.paceStr+'/mi</b></div>':'')+
   (p?'<div style="display:flex;justify-content:space-between;font-size:12.5px;margin:3px 0">'+
    '<span>Under 7h <span style="color:var(--faint)">('+p.n+' runs)</span></span>'+
    '<b style="color:var(--tempo)">'+p.paceStr+'/mi</b></div>':'')+
   (diff!=null&&Math.abs(diff)>=5?'<div style="color:var(--faint);font-size:11px;margin-top:4px">'+
    Math.round(Math.abs(diff))+'s/mi difference — sleep is training too</div>':'')+'</div>';
 }
 el.innerHTML=h;el.style.display='block';
}

function renderFitForm(){
 const el=document.getElementById('fitformpanel'),f=S.fitForm;
 if(!el)return;
 if(!f||(!f.ctlAtl&&!f.hrv&&!f.vo2max)){el.style.display='none';return;}
 let h='<h3>Fitness &amp; Form</h3>';
 if(f.ctlAtl&&f.ctlAtl.length>=3){
  const last=f.ctlAtl[f.ctlAtl.length-1];
  const formCol=last.form>0?'var(--good)':(last.form<-10?'var(--hard)':'var(--tempo)');
  h+='<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:4px">'+
   '<span style="color:var(--dim);font-size:13px">Form (CTL '+last.ctl.toFixed(0)+' &minus; ATL '+last.atl.toFixed(0)+')</span>'+
   '<span style="color:'+formCol+'"><b>'+(last.form>0?'+':'')+last.form.toFixed(1)+'</b></span></div>'+
   sparkSvg(f.ctlAtl.map(x=>x.form),'#3ec6c0')+
   '<div style="color:var(--faint);font-size:11px;margin-top:2px">Positive = fresh, ready for a hard effort. '+
   'Sustained negative = carrying fatigue — expected mid-block, a flag if it lingers into a quality week.</div>';
 }
 if(f.hrv&&f.hrv.length>=5){
  const vs=f.hrv.map(x=>x.v),last=vs[vs.length-1];
  h+='<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:10px">'+
   '<span style="color:var(--dim);font-size:13px">HRV</span><span><b>'+last+'</b>'+
   (f.hrvReadiness?' <span style="font-size:11.5px;color:var(--faint)">(60d avg '+f.hrvReadiness.baseline+')</span>':'')+
   '</span></div>'+sparkSvg(vs,'#9d7cd8');
 }
 if(f.vo2max&&f.vo2max.length>=2){
  const vs=f.vo2max.map(x=>x.v),last=vs[vs.length-1];
  h+='<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:10px">'+
   '<span style="color:var(--dim);font-size:13px">VO2max (Suunto est.)</span><span><b>'+last+'</b></span></div>'+
   (vs.length>=3?sparkSvg(vs,'#e0af68'):'');
 }
 el.innerHTML=h;el.style.display='block';
}

function renderCountdown(){
 const el=document.getElementById('countdownpanel'),f=S.fit;
 if(!el)return;
 if(!f||!f.daysToRace||!f.marathon){el.style.display='none';return;}
 const days=f.daysToRace,onTrack=f.goalGap<=0;
 const gapMin=Math.round(Math.abs(f.goalGap)/60);
 const gapStr=onTrack?'<span style="color:var(--good)">On track for sub-3:25 ✓</span>':
  '<span style="color:var(--tempo)">'+gapMin+' min behind sub-3:25</span>';
 el.innerHTML=
  '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">'+
  '<div>'+
   '<div style="font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.7px">Marine Corps Marathon</div>'+
   '<div style="font-size:28px;font-weight:700;line-height:1.1;margin:2px 0">'+days+'<span style="font-size:14px;font-weight:400;color:var(--dim)"> days</span></div>'+
   '<div style="font-size:12px;color:var(--dim)">Oct 25, 2026</div>'+
  '</div>'+
  '<div style="text-align:right">'+
   '<div style="font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.7px">Current projection</div>'+
   '<div style="font-size:24px;font-weight:700;color:'+(onTrack?'var(--good)':'var(--accent)')+'">'+f.marathon+'</div>'+
   '<div style="font-size:12px;margin-top:2px">'+gapStr+'</div>'+
  '</div></div>'+
  '<div style="font-size:11px;color:var(--faint);margin-top:6px">Based on recent training runs via Daniels VDOT · updates each run</div>';
 el.style.display='block';
}

function renderQuickLog(today){
 const el=document.getElementById('quicklogpanel');
 if(!el)return;
 // Only show once actuals have loaded (S.ann defined) and today has an unlogged run
 if(!S.ann){el.style.display='none';return;}
 const run=(S.runs||[]).find(r=>r.date===today&&r.activityId);
 if(!run||(S.ann[String(run.activityId)]||{}).rpe){el.style.display='none';return;}
 const mi=run.mi.toFixed(1);
 // Shoe default
 const known=(S.gear||[]).filter(g=>!g.retired);
 const def=known.find(g=>g.isDefault);
 const defKey=def?def.key:'';
 // RPE bands: value logged = lower bound of pair
 const bands=[[1,'1–2','Recovery'],[3,'3–4','Easy'],[5,'5–6','Moderate'],[7,'7–8','Hard'],[9,'9–10','Max']];
 let h='<div style="font-size:13px;color:var(--dim);margin-bottom:10px">How did today\'s <b style="color:var(--tx)">'+mi+' mi</b> feel?</div>'+
  '<div style="display:flex;gap:6px;margin-bottom:12px">';
 bands.forEach(([v,label,name])=>{
  h+='<button class="rpe-btn" onclick="quickLogRpe('+run.activityId+','+v+')" title="'+name+'">'+label+'</button>';
 });
 h+='</div>';
 if(known.length){
  h+='<select id="qlShoes" style="width:100%;background:var(--cell);border:1px solid var(--line);'+
   'color:var(--tx);border-radius:9px;padding:10px 11px;font-size:15px">'+
   '<option value="">Shoes — none logged</option>';
  known.forEach(g=>{h+='<option value="'+escapeHTML(g.key)+'"'+(defKey===g.key?' selected':'')+'>'+
   escapeHTML(g.nickname||g.display)+(g.isDefault?' · default':'')+'</option>';});
  h+='</select>';
 }
 el.innerHTML=h;el.style.display='block';
}
async function quickLogRpe(aid,rpe){
 const sel=document.getElementById('qlShoes');
 const shoes=sel?sel.value:'';
 try{
  await jpost('/api/annotate',{activityId:aid,rpe:rpe,note:'',shoes:shoes});
  S.ann=S.ann||{};S.ann[String(aid)]={rpe:rpe,note:'',shoes:shoes};
  document.getElementById('quicklogpanel').style.display='none';
  toast('Logged '+rpe+' · '+rpeName(rpe)+(shoes?' + shoes':''));
  jget('/api/gear').then(j=>{S.gear=j.gear||[];render();}).catch(()=>{render();});
 }catch(e){toast('Save failed: '+escapeHTML(e.message),{err:1});}
}

function renderPRs(){
 const el=document.getElementById('prpanel'),p=S.prs;
 if(!el)return;
 if(!p||!Object.keys(p).length){el.style.display='none';return;}
 const labels={mile:'Best mile pace',
  '5k':'Best 5K pace',
  '10k':'Best 10K pace',
  half:'Best half pace',
  long:'Longest run'};
 let h='<h3>Personal Records</h3><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px">';
 const order=['mile','5k','10k','half','long'];
 order.forEach(k=>{
  const r=p[k];if(!r)return;
  const val=k==='long'?r.mi.toFixed(1)+' mi':(r.paceStr+'/mi');
  const d=r.date?new Date(r.date+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'2-digit'}):'';
  h+='<div onclick="openRun(\''+r.activityId+'\',\'\')" style="background:var(--cell);border-radius:10px;padding:10px 12px;cursor:pointer">'+
   '<div style="font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.5px">'+labels[k]+'</div>'+
   '<div style="font-size:20px;font-weight:700;margin:2px 0">'+val+'</div>'+
   '<div style="font-size:11px;color:var(--dim)">'+d+(r.mi&&k!=='long'?' · '+r.mi.toFixed(1)+' mi run':'')+'</div></div>';
 });
 h+='</div>';
 el.innerHTML=h;el.style.display='block';
}

function estRange(title){
 const mi=S.plan.planMiles[title];if(!mi)return'';
 const t=S.plan.planTargets[title];
 const lo=Math.round(mi*(t?t.fastSec:585)/60),hi=Math.round(mi*(t?t.slowSec:630)/60);
 return lo+'–'+hi+'m';
}
function renderBrief(today){
 const br=document.getElementById('brief'),bn=document.getElementById('banner');
 const r=readiness();
 bn.className='banner';bn.textContent='';
 // Focused item: the selected strip day, else today's workout, else next upcoming.
 let item,fdate;
 if(S.selDate){
  fdate=S.selDate;
  item=S.schedule.filter(i=>i.date===fdate)[0]||null;
 }else{
  item=S.schedule.filter(i=>i.date>=today).sort((a,b)=>a.date.localeCompare(b.date))[0]||null;
  fdate=item?item.date:today;
 }
 const isToday=fdate===today;
 const runs=runsOn(fdate);
 const bigRun=runs.filter(x=>x.activityId).sort((a,b)=>b.mi-a.mi)[0];
 const rel=isToday?'Today':parse(fdate).toLocaleDateString(undefined,{weekday:'long',month:'short',day:'numeric'});
 const wx=(isToday&&S.weather&&S.weather.tempF)?
  '<span class="wx">'+S.weather.tempF+'°'+
   (S.weather.humidity>=70?' · '+S.weather.humidity+'%':'')+'</span>':'';
 let h;
 if(item){
  const t=S.plan.planTargets[item.title];
  const done=runs.length>0;
  br.style.setProperty('--bcolor','var(--'+kindVar[kind(item.title)]+')');
  h='<div class="top"><div style="flex:1;min-width:0">'+
   '<b>'+escapeHTML(item.title.replace(/^W\d+ \w+ /,''))+'</b>'+
   '<div class="sub">'+rel+' · '+(S.plan.planMiles[item.title]||'?')+' mi · '+estRange(item.title)+
   (t?' · '+t.label:'')+'</div></div>'+
   (done?'<span class="donechip">✓ '+(bigRun?bigRun.mi.toFixed(1)+' mi':'Done')+'</span>':wx)+'</div>';
 }else{
  br.style.setProperty('--bcolor','var(--faint)');
  h='<div class="top"><div style="flex:1;min-width:0"><b>Rest day</b>'+
   '<div class="sub">'+rel+' · recovery is training too</div></div>'+
   (bigRun?'<span class="donechip">✓ '+bigRun.mi.toFixed(1)+' mi unplanned</span>':wx)+'</div>';
 }
 if(isToday&&r&&r.today){
  const ff=r.fitForm;
  h+='<div class="ready">'+
   (r.today.rhr?'<span>RHR <b>'+r.today.rhr+'</b>'+(r.base?' <span style="color:var(--faint)">(7-day '+r.base+')</span>':'')+'</span>':'')+
   (r.today.sleepH?'<span>Sleep <b>'+r.today.sleepH+'h</b></span>':'')+
   ((r.today.bb!==null&&r.today.bb!==undefined)?'<span>Body Battery <b>'+r.today.bb+'</b></span>':'')+
   ((ff&&ff.formToday!==undefined)?'<span>Form <b>'+(ff.formToday>0?'+':'')+ff.formToday+'</b></span>':'')+
   ((r.today.hrv!==null&&r.today.hrv!==undefined)?'<span>HRV <b>'+r.today.hrv+'</b></span>':'')+
   (r.level==='ok'?'<span style="color:var(--good)">● ready</span>':'')+'</div>';
 }
 const hp=(S.weather&&S.weather.heatPct)||0;
 if(hp>=0.02&&isToday&&item){
  const t2=S.plan.planTargets[item.title];
  h+='<div class="ready" style="color:var(--tempo)">Heat: feels like '+
   (S.weather.feelsF||S.weather.tempF)+'° — pace costs ~'+Math.round(hp*100)+'% today.'+
   (t2?' Heat-adjusted target: <b>'+fmtPace(Math.round(t2.fastSec*(1+hp)))+'–'+
    fmtPace(Math.round(t2.slowSec*(1+hp)))+'/mi</b> (watch shows the official one — trust effort).':
    ' Hydrate; effort over pace.')+'</div>';
 }
 const needsLog=bigRun&&!((S.ann||{})[String(bigRun.activityId)]||{}).rpe;
 h+='<div class="cta">'+
  (item?'<button onclick="openDetail('+item.scheduleId+')">Details</button>':'')+
  (bigRun?'<button '+(needsLog?'class="primary" ':'')+'onclick="openRun(\''+bigRun.activityId+'\',\''+(item?item.title.replace(/'/g,''):'')+'\')">'+
    (needsLog?'Log how it felt ▸':'View run ▸')+'</button>':'')+
  '</div>';
 br.innerHTML=h;br.style.display='block';
 const todayItem=S.schedule.filter(i=>i.date===today)[0];
 const showBanner=r&&r.flags.length&&isToday&&todayItem&&isHard(todayItem.title)&&!runsOn(today).length&&!S.bannerDismissed;
 if(showBanner){
  const isRed=r.level==='red';
  bn.className='banner '+(isRed?'red':'amber');
  const label=todayItem.title.replace(/^W\d+ \w+ /,'');
  if(isRed){
   bn.innerHTML='<b>Rough recovery detected</b> · '+r.flags.join(', ')+
    '<div style="margin:7px 0 10px;font-size:13px;opacity:.9">Today is a quality day (<b>'+escapeHTML(label)+'</b>).'+
    ' A forced injury delays the plan more than one moved workout.</div>'+
    '<div style="display:flex;gap:8px">'+
    '<button class="primary" onclick="bannerMove('+todayItem.scheduleId+','+todayItem.workoutId+',\''+today+'\')">Move to next clear day ▸</button>'+
    '<button onclick="bannerDismiss()">Dismiss</button></div>';
  }else{
   bn.innerHTML='<b>Heads up:</b> '+r.flags.join(', ')+' — today is a quality day (<b>'+escapeHTML(label)+'</b>).'+
    ' <button style="margin-left:8px" onclick="bannerMove('+todayItem.scheduleId+','+todayItem.workoutId+',\''+today+'\')">Move it ▸</button>'+
    ' <button onclick="bannerDismiss()" style="margin-left:4px">✕</button>';
  }
 }
}
async function bannerMove(scheduleId,workoutId,fromDate){
 const bn=document.getElementById('banner');
 if(bn)bn.innerHTML='<i>Finding next clear day…</i>';
 try{
  const s=await jget('/api/suggest_move?scheduleId='+scheduleId+'&fromDate='+fromDate);
  if(!s.to){toast('No clear slot found in the next 10 days',{err:1});return;}
  await jpost('/api/move',{scheduleId:scheduleId,workoutId:workoutId,date:s.to});
  S.bannerDismissed=true;
  toast('Moved to '+(s.toLabel||s.to));
  const d=await jget('/api/data');
  S.plan=d.plan;S.schedule=d.schedule;render();
 }catch(e){toast('Move failed: '+escapeHTML(e.message),{err:1});}
}
function bannerDismiss(){
 S.bannerDismissed=true;
 document.getElementById('banner').className='banner';
}

function renderGrid(today){
 const y=Math.floor(S.month/12),m=S.month%12;
 document.getElementById('mlabel').textContent=
   new Date(y,m,1).toLocaleDateString(undefined,{month:'long',year:'numeric'});
 const first=new Date(y,m,1);
 let start=new Date(first); start.setDate(1-((first.getDay()+6)%7));
 const byDate={};
 S.schedule.forEach(i=>(byDate[i.date]=byDate[i.date]||[]).push(i));
 const ranByDate={},ranTip={};
 S.runs.forEach(r=>{ranByDate[r.date]=(ranByDate[r.date]||0)+r.mi;
  ranTip[r.date]=((ranTip[r.date]||'')+' '+r.mi.toFixed(1)+'mi'+(r.pace?' @ '+r.pace+'/mi':'')).trim();});
 const hardDates=new Set(S.schedule.filter(i=>isHard(i.title)).map(i=>i.date));
 let html='';
 for(let i=0;i<42;i++){
  const d=new Date(start.getTime()+i*DAY), ds=fmt(d);
  const other=d.getMonth()!==m;
  html+='<div class="cell'+(other?' other':'')+(ds===today?' today':'')+'"'+
   ' data-date="'+ds+'" onclick="cellTap(event)"'+
   ' ondragover="dragOver(event)" ondragleave="dragLeave(event)" ondrop="drop(event)">'+
   '<div class="dnum"><b>'+d.getDate()+'</b>'+
   (ranByDate[ds]?'<span class="ran" onclick="ranTap(event,\''+ds+'\')" style="cursor:pointer" title="'+ranTip[ds]+' — tap for run details">✓ '+ranByDate[ds].toFixed(1)+'</span>':'')+'</div>';
  (byDate[ds]||[]).forEach(it=>{
   const prev=fmt(new Date(d.getTime()-DAY)),nxt=fmt(new Date(d.getTime()+DAY));
   const clash=isHard(it.title)&&(hardDates.has(prev)||hardDates.has(nxt));
   const short=escapeHTML(it.title.replace(/^W\d+ \w+ /,''));
   html+='<div class="chip '+kind(it.title)+(it.date<today?' past':'')+'" draggable="true"'+
    ' id="c'+it.scheduleId+'" data-sid="'+it.scheduleId+'"'+
    ' ondragstart="dragStart(event)" ondragend="dragEnd(event)"'+
    ' onclick="chipTap(event,'+it.scheduleId+')"'+
    ' title="'+escapeHTML(it.title)+' — tap for details">'+short+
    '<span class="mi">'+(S.plan.planMiles[it.title]||'?')+' mi</span>'+
    (clash?'<span class="warn" title="Back-to-back hard days">⚠️</span>':'')+'</div>';
  });
  html+='</div>';
 }
 document.getElementById('grid').innerHTML=html;
}

/* ---------------- performance ---------------- */
function runsOn(ds){return S.runs.filter(r=>r.date===ds);}
function assess(it){
 const planned=S.plan.planMiles[it.title]||0;
 const target=S.plan.planTargets[it.title];
 const rs=runsOn(it.date);
 if(!rs.length)return null;
 const mi=rs.reduce((a,r)=>a+r.mi,0);
 const main=rs.slice().sort((a,b)=>b.mi-a.mi)[0];
 const distOk=mi>=planned*0.9;
 let paceMsg='',paceOk=null;
 if(target&&main.paceSec){
  if(main.paceSec<target.fastSec-10){paceMsg='faster than target';paceOk=false;}
  else if(main.paceSec>target.slowSec+10){paceMsg='slower than target';paceOk=false;}
  else{paceMsg='on target';paceOk=true;}
 }
 return {mi:mi,paceSec:main.paceSec,distOk:distOk,paceOk:paceOk,paceMsg:paceMsg};
}

function renderWeek(today){
 const wk=Math.floor((parse(today)-parse(S.plan.start))/DAY/7)+1;
 const panel=document.getElementById('weekpanel');
 if(wk<1||wk>19){panel.style.display='none';return;}
 const items=S.schedule.filter(i=>{
  const w=Math.floor((parse(i.date)-parse(S.plan.start))/DAY/7)+1;return w===wk;
 }).sort((a,b)=>a.date.localeCompare(b.date));
 if(!items.length){panel.style.display='none';return;}
 const ran=S.weeklyActual[wk]||0,goal=S.plan.plannedWeekly[wk]||0;
 const pct=goal?Math.min(100,Math.round(ran/goal*100)):0;
 let h='<h3>Week '+wk+' report card <span style="color:var(--dim);font-weight:400">— '+
   ran.toFixed(1)+' of '+Math.round(goal)+' mi ('+pct+'%)</span></h3>'+
   '<div style="background:var(--cell);border-radius:6px;height:7px;margin:2px 0 13px">'+
   '<div style="background:var(--good);height:7px;border-radius:6px;width:'+pct+'%"></div></div>';
 items.forEach(it=>{
  const t=S.plan.planTargets[it.title];const a=assess(it);
  let status,color;
  if(a){
   const ok=a.distOk&&(a.paceOk!==false);
   status='✓ '+a.mi.toFixed(1)+' mi @ '+fmtPace(a.paceSec)+'/mi'+(a.paceMsg?' · '+a.paceMsg:'');
   color=ok?'var(--good)':'var(--tempo)';
  }else if(it.date<today){status='✗ missed — tap to reschedule';color='var(--hard)';}
  else if(it.date===today){status='today';color='var(--accent)';}
  else{status='upcoming';color='var(--faint)';}
  h+='<div onclick="openDetail('+it.scheduleId+')" style="display:flex;gap:10px;align-items:baseline;'+
   'padding:6px 0;border-top:1px solid var(--line);cursor:pointer;flex-wrap:wrap">'+
   '<span style="width:84px;color:var(--dim);flex:none">'+
     parse(it.date).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'</span>'+
   '<b style="flex:1;min-width:120px">'+escapeHTML(it.title.replace(/^W\d+ \w+ /,''))+'</b>'+
   '<span style="color:var(--dim)">'+(S.plan.planMiles[it.title]||'?')+' mi'+(t?' · '+t.label:'')+'</span>'+
   '<span style="color:'+color+'">'+status+'</span></div>';
 });
 panel.innerHTML=h;panel.style.display='block';
}

function renderChart(){
 const planned=S.plan.plannedWeekly;
 const weeks=Object.keys(planned).map(Number).sort((a,b)=>a-b);
 const maxv=Math.max(...weeks.map(w=>Math.max(planned[w]||0,S.weeklyActual[w]||0)),1);
 const W=1040,H=140,bw=W/weeks.length;
 let s='<svg viewBox="0 0 '+W+' '+(H+22)+'" xmlns="http://www.w3.org/2000/svg" style="width:100%">';
 weeks.forEach((w,i)=>{
  const ph=(planned[w]||0)/maxv*H, ah=(S.weeklyActual[w]||0)/maxv*H, x=i*bw;
  s+='<rect x="'+(x+3)+'" y="'+(H-ph)+'" width="'+(bw/2-5)+'" height="'+Math.max(ph,1)+'" fill="#5b6671" opacity=".75" rx="2"/>';
  if(S.weeklyActual[w])s+='<rect x="'+(x+bw/2-1)+'" y="'+(H-ah)+'" width="'+(bw/2-5)+'" height="'+ah+'" fill="#5DCAA5" rx="2"/>';
  s+='<text x="'+(x+bw/2)+'" y="'+(H+15)+'" fill="#93a0ad" font-size="10.5" text-anchor="middle">W'+w+'</text>';
 });
 document.getElementById('chart').innerHTML=s+'</svg>';
}

function renderRamp(wk){
 const el=document.getElementById('rampnote');el.textContent='';
 if(wk<2||wk>19)return;
 const get=w=>S.weeklyActual[w]!==undefined&&w<wk?S.weeklyActual[w]:S.plan.plannedWeekly[w];
 const hist=[wk-1,wk-2,wk-3].filter(w=>w>=1).map(get).filter(v=>v);
 if(!hist.length)return;
 const avg=hist.reduce((a,b)=>a+b,0)/hist.length;
 const cur=S.plan.plannedWeekly[wk]||0;
 if(!avg||!cur)return;
 const pct=Math.round((cur/avg-1)*100);
 if(pct>25)el.innerHTML='⚠️ This week is <b style="color:var(--tempo)">+'+pct+'%</b> over your recent average — a big jump. Protect sleep, keep easy days truly easy.';
 else if(pct<-20)el.textContent='Recovery/taper week: '+pct+'% vs recent — let it be easy, the fitness is already in the bank.';
}

/* ---------------- drag & drop + tap-to-move ---------------- */
let dragSid=null;
function dragStart(e){dragSid=e.target.dataset.sid;e.target.classList.add('dragging');
 e.dataTransfer.effectAllowed='move';}
function dragEnd(e){e.target.classList.remove('dragging');
 document.querySelectorAll('.cell.over').forEach(c=>c.classList.remove('over'));}
function dragOver(e){e.preventDefault();e.currentTarget.classList.add('over');}
function dragLeave(e){e.currentTarget.classList.remove('over');}
function drop(e){
 e.preventDefault();
 const cell=e.currentTarget;cell.classList.remove('over');
 const it=S.schedule.find(i=>String(i.scheduleId)===String(dragSid));
 const nd=cell.dataset.date;
 if(it&&nd&&it.date!==nd)applyMove(it,nd,true);
}
function chipTap(e,sid){
 if(S.moveItem)return;        // bubbling to cellTap completes a move
 e.stopPropagation();
 openDetail(sid);
}
function cellTap(e){
 if(!S.moveItem)return;
 const nd=e.currentTarget.dataset.date;
 const it=S.moveItem;
 exitMoveMode();
 if(nd&&it.date!==nd)applyMove(it,nd,true);
}
function enterMoveMode(sid){
 const it=S.schedule.find(i=>String(i.scheduleId)===String(sid));
 if(!it)return;
 closeDetail();
 if(S.view!=='plan')setView('plan');
 if(S.planMode!=='month'){S.planMode='month';render();}  // moving needs the grid
 S.moveItem=it;
 document.body.classList.add('movemode');
 toast('Tap a day to move <b>'+escapeHTML(it.title.replace(/^W\d+ \w+ /,''))+'</b>',{sticky:1,cancel:1});
}
function exitMoveMode(){
 S.moveItem=null;document.body.classList.remove('movemode');hideToast();
}
function applyMove(it,newDate,allowUndo){
 const old=it.date;
 it.date=newDate;render();
 jpost('/api/move',{scheduleId:it.scheduleId,workoutId:it.workoutId,date:newDate})
  .then(()=>{
   if(allowUndo){S.undo={it:it,back:old};
    toast('<b>'+escapeHTML(it.title.replace(/^W\d+ \w+ /,''))+'</b>&nbsp;→ '+
     parse(newDate).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'}),
     {undo:1});}
   load(true);
  })
  .catch(err=>{it.date=old;render();toast('Move failed — put it back. '+escapeHTML(err.message),{err:1});});
}
function doUndo(){
 if(!S.undo)return;
 applyMove(S.undo.it,S.undo.back,false);
 toast('Undone');S.undo=null;
}

/* ---------------- workout detail ---------------- */
function openDetail(sid){
 const it=S.schedule.find(i=>String(i.scheduleId)===String(sid));
 if(!it)return;
 const t=S.plan.planTargets[it.title],a=assess(it),isRepeat=/\dx/.test(it.title);
 let h='<h3>'+escapeHTML(it.title)+'</h3><p>'+
   parse(it.date).toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric'})+'</p>'+
   '<div class="preview" style="margin-top:0">'+
   '<b>Plan:</b> '+(S.plan.planMiles[it.title]||'?')+' mi'+
   (t?' · target '+t.label:' · no pace target (easy)')+'<br>';
 if(a){
  h+='<b>You ran:</b> '+a.mi.toFixed(2)+' mi @ '+fmtPace(a.paceSec)+'/mi<br>'+
   '<b>Distance:</b> '+(a.distOk?'✓ covered':'▲ short of plan')+'<br>'+
   (t&&a.paceMsg?'<b>Pace:</b> '+(a.paceOk?'🎯 ':'▲ ')+a.paceMsg+'<br>':'')+
   (isRepeat?'<span style="color:var(--faint)">Interval day: average pace includes recovery jogs — check lap splits in Garmin Connect for true rep paces.</span>':'');
 }else if(it.date<S.plan.today){
  h+='<span style="color:var(--hard)">No run recorded this day.</span>';
 }else{
  h+='<span style="color:var(--faint)">Not run yet.</span>';
 }
 const big=runsOn(it.date).filter(r=>r.activityId).sort((x,y)=>y.mi-x.mi)[0];
 const missed=it.date<S.plan.today&&!big;
 const annD=big?((S.ann||{})[String(big.activityId)]||{}):{};
 if(annD.rpe||annD.note||annD.shoes){
  h+='<br><b>Your log:</b> '+[annD.rpe?'RPE '+annD.rpe:null,
    annD.shoes?escapeHTML(shoeName(annD.shoes)):null].filter(Boolean).join(' · ')+
   (annD.note?'<br><span style="color:var(--faint)">“'+escapeHTML(annD.note)+'”</span>':'');
 }
 h+='</div><div class="row">'+
  (big?'<button onclick="closeDetail();openRun(\''+big.activityId+'\',\''+it.title.replace(/'/g,'')+'\')">View run ▸</button>':'')+
  (missed?'<button class="primary" onclick="openReplan('+it.scheduleId+')">Replan ▸</button>':'')+
  '<button onclick="enterMoveMode('+it.scheduleId+')">Move to another day…</button>'+
  '<button '+(missed?'':'class="primary" ')+'onclick="closeDetail()">Done</button></div>';
 document.getElementById('dmodal').innerHTML=h;
 document.getElementById('dscrim').classList.add('show');
}
function closeDetail(){document.getElementById('dscrim').classList.remove('show');}

/* ---------------- missed-workout replanning ---------------- */
function openReplan(sid){
 const it=S.schedule.find(i=>String(i.scheduleId)===String(sid));
 if(!it)return;
 const hard=isHard(it.title),today=S.plan.today;
 let recDate=null;
 if(hard){
  const occupied=new Set(S.schedule.filter(x=>x.scheduleId!==it.scheduleId).map(x=>x.date));
  const hardSet=new Set(S.schedule.filter(x=>x.scheduleId!==it.scheduleId&&isHard(x.title)).map(x=>x.date));
  const near=d=>hardSet.has(d)||hardSet.has(fmt(new Date(parse(d).getTime()-DAY)))||
               hardSet.has(fmt(new Date(parse(d).getTime()+DAY)));
  const preferWE=/mi LR|MP Finish/.test(it.title);
  for(let k=1;k<=10;k++){
   const d=fmt(new Date(parse(today).getTime()+k*DAY)),dow=parse(d).getDay();
   if(occupied.has(d)||near(d))continue;
   if(preferWE&&k<=7&&dow!==6&&dow!==0)continue;
   recDate=d;break;
  }
 }
 const recLabel=recDate?parse(recDate).toLocaleDateString(undefined,{weekday:'long',month:'short',day:'numeric'}):null;
 let h='<h3>Replan: '+escapeHTML(it.title.replace(/^W\d+ \w+ /,''))+'</h3>'+
  '<p>Missed on '+parse(it.date).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'.</p>'+
  '<div class="preview" style="margin-top:0">'+
  (hard?
   (recDate?'This is a key session — worth keeping. <b>'+recLabel+'</b> is the first clean day '+
    '(no hard days adjacent, nothing displaced).':
    'This is a key session but the next 10 days are full — absorbing it is cleaner than cramming.'):
   'Easy miles are volume filler — absorbing a missed one is what a coach would tell you. '+
   'Don’t chase it; the plan’s intact.')+
  '</div><div class="row">'+
  '<button onclick="closeDetail()">Cancel</button>'+
  (recDate?'<button onclick="applyReplan('+sid+',\'skip\',null)">Skip it</button>'+
   '<button class="primary" onclick="applyReplan('+sid+',\'move\',\''+recDate+'\')">Move to '+recLabel+'</button>':
   '<button class="primary" onclick="applyReplan('+sid+',\'skip\',null)">Absorb it</button>')+
  '</div>';
 document.getElementById('dmodal').innerHTML=h;
 document.getElementById('dscrim').classList.add('show');
}
async function applyReplan(sid,act,dateStr){
 const it=S.schedule.find(i=>String(i.scheduleId)===String(sid));
 closeDetail();
 try{
  if(act==='move')await jpost('/api/move',{scheduleId:it.scheduleId,workoutId:it.workoutId,date:dateStr});
  else await jpost('/api/unschedule',{scheduleId:it.scheduleId});
  toast(act==='move'?'Rescheduled — sync your watch':'Absorbed. Eyes forward.');
  load(true);
 }catch(e){toast('Replan failed: '+escapeHTML(e.message),{err:1});}
}

/* ---------------- run detail sheet ---------------- */
const fmtDur=s=>{const h=Math.floor(s/3600),m=Math.floor(s/60)-h*60,x=s-h*3600-m*60;
 return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(x).padStart(2,'0');};
function ranTap(e,ds){
 e.stopPropagation();
 if(S.moveItem)return;
 const rs=runsOn(ds).filter(r=>r.activityId).sort((a,b)=>b.mi-a.mi);
 if(!rs.length)return;
 const it=S.schedule.find(i=>i.date===ds);
 openRun(rs[0].activityId,it?it.title:null);
}
let CUR_AID=null,CUR_RPE=null;
async function openRun(aid,title){
 CUR_AID=aid;CUR_RPE=((S.ann||{})[String(aid)]||{}).rpe||null;
 const m=document.getElementById('rmodal');
 m.innerHTML='<div class="skel" style="padding:60px 20px"><i></i>Loading run…</div>';
 document.getElementById('rscrim').classList.add('show');
 let j;
 try{j=await jget('/api/run/'+aid);}
 catch(e){m.innerHTML='<h3>Couldn’t load run</h3><p>'+escapeHTML(e.message)+
  '</p><div class="row"><button onclick="closeRun()">Close</button></div>';return;}
 renderRun(j,title?S.plan.planTargets[title]:null,title);
}
function closeRun(){document.getElementById('rscrim').classList.remove('show');}

/* ---- interactive run analysis engine (Strava-style scrub/select) ---- */
let RS=null;
const RW=520,RPAD=14;
function paceCol(t){
 t=Math.max(0,Math.min(1,t));
 const a=[93,202,165],b=[240,153,123];
 return 'rgb('+a.map((v,k)=>Math.round(v+(b[k]-v)*t)).join(',')+')';
}
function hrCol(t){
 // blue (easy) → red (hard)
 t=Math.max(0,Math.min(1,t));
 const a=[80,140,220],b=[240,80,80];
 return 'rgb('+a.map((v,k)=>Math.round(v+(b[k]-v)*t)).join(',')+')';
}
let mapMode='pace'; // 'pace' or 'hr'
function setMapMode(m){mapMode=m;const el=document.getElementById('routewrap');if(el)el.innerHTML=routeSvgX(RS._rt)+mapControls();}
function mapControls(){
 const hp=RS.hr&&RS.hr.some(x=>x!=null);
 if(!hp)return'';
 return '<div style="display:flex;align-items:center;gap:8px;margin-top:6px">'+
  '<span style="font-size:11px;color:var(--faint)">Color by</span>'+
  ['pace','hr'].map(m=>'<button onclick="setMapMode(\''+m+'\')" style="padding:3px 10px;font-size:11px'+
   (mapMode===m?';background:var(--accent);color:#000;border-color:var(--accent)':'')+
   '">'+m+'</button>').join('')+
  '<span style="flex:1"></span>'+legendSvg()+'</div>';
}
function legendSvg(){
 const stops=8,W=80,H=8;
 let bars='';
 for(let i=0;i<stops;i++){
  const t=i/(stops-1);
  const c=mapMode==='hr'?hrCol(t):paceCol(t);
  bars+='<rect x="'+(i*W/stops).toFixed(1)+'" y="0" width="'+(W/stops+1).toFixed(1)+'" height="'+H+'" fill="'+c+'"/>';
 }
 const lo=mapMode==='hr'?'easy HR':'fast',hi=mapMode==='hr'?'hard HR':'slow';
 return '<div style="display:flex;align-items:center;gap:4px;font-size:10px;color:var(--faint)">'+
  '<span>'+lo+'</span>'+
  '<svg viewBox="0 0 '+W+' '+H+'" style="width:50px;height:8px;border-radius:3px;overflow:hidden">'+bars+'</svg>'+
  '<span>'+hi+'</span></div>';
}
function rx(i){return RPAD+(RS.d[i]/RS.d[RS.n-1])*(RW-2*RPAD);}

function routeSvgX(rt){
 if(!rt||rt.length<8){RS.rpts=null;RS._rt=null;return'';}
 RS._rt=rt;
 const lats=rt.map(p=>p[0]),lons=rt.map(p=>p[1]);
 const la0=Math.min(...lats),la1=Math.max(...lats),lo0=Math.min(...lons),lo1=Math.max(...lons);
 const H=170,kx=Math.cos((la0+la1)/2*Math.PI/180);
 const spanX=(lo1-lo0)*kx||1e-9,spanY=(la1-la0)||1e-9;
 const sc=Math.min((RW-2*RPAD)/spanX,(H-2*RPAD)/spanY);
 const ox=(RW-spanX*sc)/2,oy=(H-spanY*sc)/2;
 const pts=rt.map(p=>[ox+((p[1]-lo0)*kx)*sc,oy+(la1-p[0])*sc]);
 RS.rpts=pts;
 const useHr=mapMode==='hr'&&RS.hr&&RS.hr.some(x=>x!=null);
 const vals=useHr?RS.hr:RS.pace;
 const sorted=(vals||[]).filter(x=>x!=null).slice().sort((a,b)=>a-b);
 const v10=sorted[Math.floor(sorted.length*0.1)]||0,v90=sorted[Math.floor(sorted.length*0.9)]||1;
 const colFn=useHr?hrCol:paceCol;
 const step=Math.max(1,Math.floor(pts.length/56));
 let segs='';
 for(let i=0;i<pts.length-1;i+=step){
  const j=Math.min(pts.length-1,i+step);
  const si=Math.round(i/(pts.length-1)*(RS.n-1));
  const pv=vals[si];
  // for pace: higher=slower=warmer; for HR: higher=harder=warmer — both map t=0→fast/easy
  const t=(pv!=null&&v90>v10)?(pv-v10)/(v90-v10):0.5;
  const col=colFn(useHr?t:t);
  segs+='<polyline points="'+pts.slice(i,j+1).map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')+
   '" fill="none" stroke="'+col+'" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>';
 }
 return '<svg viewBox="0 0 '+RW+' '+H+'" style="width:100%;background:var(--cell);border-radius:12px">'+segs+
  '<polyline id="rsel" points="" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/>'+
  '<circle cx="'+pts[0][0].toFixed(1)+'" cy="'+pts[0][1].toFixed(1)+'" r="5" fill="var(--good)"/>'+
  '<circle cx="'+pts[pts.length-1][0].toFixed(1)+'" cy="'+pts[pts.length-1][1].toFixed(1)+'" r="5" fill="#fff"/>'+
  '<circle id="rdot" r="6.5" fill="#fff" stroke="#101418" stroke-width="2.5" style="display:none"/></svg>';
}

function chartX(id,vals,H,color,invert){
 const pts=[];
 for(let i=0;i<RS.n;i++)if(vals[i]!=null)pts.push(i);
 if(pts.length<5)return'';
 const ys=pts.map(i=>vals[i]);
 const v0=Math.min(...ys),v1=Math.max(...ys),sp=(v1-v0)||1;
 const path=pts.map((i,k)=>{
  let y=(vals[i]-v0)/sp;if(invert)y=1-y;
  return (k?'L':'M')+rx(i).toFixed(1)+' '+(H-7-y*(H-14)).toFixed(1);
 }).join(' ');
 return '<svg viewBox="0 0 '+RW+' '+H+'" style="width:100%;background:var(--cell);border-radius:10px">'+
  '<rect id="sel-'+id+'" class="selr" x="0" y="0" width="0" height="'+H+'"/>'+
  '<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="2"/>'+
  '<line id="cur-'+id+'" y1="0" y2="'+H+'" x1="0" x2="0" stroke="#f0f3f6" stroke-width="1" opacity=".85" style="display:none"/></svg>';
}

function rngStats(a,b){
 let ps=[],hs=[];
 for(let i=a;i<=b;i++){if(RS.pace[i]!=null)ps.push(RS.pace[i]);if(RS.hr[i]!=null)hs.push(RS.hr[i]);}
 return {mi:RS.d[b]-RS.d[a],
  pace:ps.length?Math.round(ps.reduce((x,y)=>x+y,0)/ps.length):null,
  hr:hs.length?Math.round(hs.reduce((x,y)=>x+y,0)/hs.length):null};
}
function setSel(a,b,lap){
 if(a>b){const t=a;a=b;b=t;}
 RS.sel=(RS.sel&&RS.sel[0]===a&&RS.sel[1]===b)?null:[a,b];
 RS.selLap=RS.sel?lap:null;
 drawSel();
}
function clearSel(){RS.sel=null;RS.selLap=null;drawSel();}
function drawSel(){
 const sel=RS.sel;
 RS.charts.forEach(id=>{
  const r=document.getElementById('sel-'+id);
  if(r){r.setAttribute('x',sel?rx(sel[0]):0);r.setAttribute('width',sel?Math.max(2,rx(sel[1])-rx(sel[0])):0);}
 });
 const rl=document.getElementById('rsel');
 if(rl&&RS.rpts){
  rl.setAttribute('points',!sel?'':(function(){
   const m=RS.rpts.length-1;
   const a=Math.round(sel[0]/(RS.n-1)*m),b=Math.round(sel[1]/(RS.n-1)*m);
   return RS.rpts.slice(a,b+1).map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');})());
 }
 document.querySelectorAll('.splitrow').forEach((el,i)=>el.classList.toggle('on',RS.selLap===i));
 const st=document.getElementById('selstats');
 if(!st)return;
 if(sel){
  const g=rngStats(sel[0],sel[1]);
  st.innerHTML='<b>'+g.mi.toFixed(2)+' mi selected</b>'+
   (g.pace?'<span>'+fmtPace(g.pace)+'/mi</span>':'')+
   (g.hr?'<span>'+g.hr+' bpm</span>':'')+
   '<button class="ghost" style="margin-left:auto;padding:0 6px" onclick="clearSel()">✕ clear</button>';
 }else{
  st.innerHTML='<span style="color:var(--dim)">Touch or hover the charts to scrub · tap a split to inspect it'+
   (matchMedia('(pointer:fine)').matches?' · drag to select a stretch':'')+'</span>';
 }
}
function selectLap(k){
 let cum=0;
 for(let i=0;i<k;i++)cum+=RS.laps[i].mi;
 const end=cum+RS.laps[k].mi;
 let a=0,b=RS.n-1;
 for(let i=0;i<RS.n;i++){if(RS.d[i]<=cum)a=i;if(RS.d[i]<=end)b=i;}
 setSel(a,b,k);
}
function updateScrub(i){
 RS.charts.forEach(id=>{
  const l=document.getElementById('cur-'+id);
  if(l){l.style.display='';l.setAttribute('x1',rx(i));l.setAttribute('x2',rx(i));}
 });
 const dot=document.getElementById('rdot');
 if(dot&&RS.rpts){
  const p=RS.rpts[Math.round(i/(RS.n-1)*(RS.rpts.length-1))];
  dot.style.display='';dot.setAttribute('cx',p[0].toFixed(1));dot.setAttribute('cy',p[1].toFixed(1));
 }
 const tip=document.getElementById('ctip');
 if(tip){
  tip.style.display='block';
  tip.style.left=(rx(i)/RW*100)+'%';tip.style.top='2px';
  tip.innerHTML='mi '+RS.d[i].toFixed(2)+
   (RS.pace[i]!=null?' · <b>'+fmtPace(RS.pace[i])+'/mi</b>':'')+
   (RS.hr[i]!=null?' · '+RS.hr[i]+' bpm':'')+
   (RS.elev&&RS.elev[i]!=null?' · '+Math.round(RS.elev[i])+'′':'');
 }
}
function hideScrub(){
 RS.charts.forEach(id=>{const l=document.getElementById('cur-'+id);if(l)l.style.display='none';});
 const dot=document.getElementById('rdot');if(dot)dot.style.display='none';
 const tip=document.getElementById('ctip');if(tip)tip.style.display='none';
}
function initRunUX(){
 const box=document.getElementById('chartsbox');
 if(!box||!RS||RS.n<5)return;
 const fracIdx=e=>{
  const r=box.getBoundingClientRect();
  const fx=(e.clientX-r.left)/r.width*RW;
  const frac=(fx-RPAD)/(RW-2*RPAD);
  return Math.max(0,Math.min(RS.n-1,Math.round(frac*(RS.n-1))));
 };
 box.addEventListener('pointerdown',e=>{
  const ph=document.getElementById('chartPlayhead');if(ph)ph.classList.add('gone');
  RS.drag={x:e.clientX,i:fracIdx(e),t:e.pointerType};
 });
 box.addEventListener('pointermove',e=>{
  const i=fracIdx(e);
  if(RS.drag&&RS.drag.t==='mouse'&&e.buttons&&Math.abs(e.clientX-RS.drag.x)>10)setSel(RS.drag.i,i,null);
  updateScrub(i);
 });
 box.addEventListener('pointerup',()=>{RS.drag=null;});
 box.addEventListener('pointerleave',()=>{RS.drag=null;hideScrub();});
 drawSel();
}
function zonesHtml(){
 const hs=(RS.hr||[]).filter(x=>x!=null);
 if(hs.length<10)return'';
 const zmax=Math.max(RS.maxHr||0,Math.max(...hs),185);
 const th=[0,0.6,0.7,0.8,0.9].map(p=>Math.round(p*zmax));
 const cols=['#5b6671','#3ec6c0','#34c77b','#f5a623','#ff6b6b'];
 const names=['Recovery','Easy','Aerobic','Threshold','Max'];
 let counts=[0,0,0,0,0];
 hs.forEach(v=>{let z=0;for(let k=4;k>=0;k--){if(v>=th[k]){z=k;break;}}counts[z]++;});
 let h='<h4>Heart rate zones</h4>';
 counts.forEach((c,k)=>{
  const pct=c/hs.length;
  const secs=Math.round(pct*RS.durSec);
  h+='<div class="zrow"><span class="zl">Z'+(k+1)+' '+names[k]+'</span>'+
   '<div class="zbar"><i style="width:'+(pct*100).toFixed(0)+'%;background:'+cols[k]+'"></i></div>'+
   '<span class="zt">'+(pct*100).toFixed(0)+'% · '+fmtDur(secs)+'</span></div>';
 });
 return h;
}

function renderRun(j,target,title){
 const s=j.summary||{},laps=j.laps||[],ser=j.series||{};
 RS={d:ser.d||[],pace:ser.pace||[],hr:ser.hr||[],elev:ser.elev||[],gap:ser.gap||[],
     laps:laps,n:(ser.d||[]).length,durSec:s.durSec||0,maxHr:s.maxHr,
     sel:null,selLap:null,charts:[],rpts:null,drag:null};
 let h='<h3>'+escapeHTML(title||s.name)+'</h3><p>'+escapeHTML(s.name)+
  (s.compliance!=null?' <span style="color:'+(s.compliance>=85?'var(--good)':'var(--tempo)')+
   '">· '+Math.round(s.compliance)+'% plan match</span>':'')+'</p>';
 mapMode='pace';
 h+='<div id="routewrap">'+routeSvgX(j.route)+mapControls()+'</div>';
 const fastLaps=laps.filter(l=>l.mi>=0.9).map(l=>l.paceSec);
 h+='<div class="statgrid">'+
  '<div><b>'+(s.mi!=null?s.mi.toFixed(2):'—')+'</b><span>miles</span></div>'+
  '<div><b>'+(s.durSec?fmtDur(s.durSec):'—')+'</b><span>time</span></div>'+
  '<div><b>'+(s.paceSec?fmtPace(s.paceSec):'—')+'</b><span>avg /mi</span></div>'+
  '<div><b>'+(s.avgHr||'—')+'</b><span>avg hr</span></div>'+
  '<div><b>'+(s.maxHr||'—')+'</b><span>max hr</span></div>'+
  '<div><b>'+(s.cad?Math.round(s.cad)+'':'—')+'</b><span>cadence</span></div>'+
  '<div><b>'+(s.elevFt!=null?s.elevFt+'′':'—')+'</b><span>elev gain</span></div>'+
  '<div><b>'+(fastLaps.length?fmtPace(Math.min.apply(null,fastLaps)):'—')+'</b><span>best split</span></div>'+
  '<div><b>'+(RS.n?RS.d[RS.n-1].toFixed(1):'—')+'</b><span>gps mi</span></div>'+
  (s.gapSec!=null?'<div><b>'+fmtPace(s.gapSec)+'</b><span>adj pace</span></div>':'')+
  (s.hrRecovery!=null?'<div><b>'+s.hrRecovery+'</b><span>hr recovery</span></div>':'')+
  '</div>';
 if(laps.length>1){
  // laps vs target: bar height ∝ speed, white band = target pace range
  const v=laps.map(l=>1/l.paceSec);
  let vmin=Math.min(...v),vmax=Math.max(...v);
  if(target){vmin=Math.min(vmin,1/target.slowSec);vmax=Math.max(vmax,1/target.fastSec);}
  const lo=vmin*0.93,span=(vmax*1.03-lo)||1e-9;
  const hpc=x=>Math.round((1/x-lo)/span*100);
  h+='<h4>Laps'+(target?' vs target ('+target.label+')':'')+'</h4><div class="lapbars">';
  if(target){
   const top=hpc(target.fastSec),bot=hpc(target.slowSec);
   h+='<div class="tband" style="bottom:'+bot+'%;height:'+Math.max(3,top-bot)+'%"></div>';
  }
  laps.forEach(l=>{h+='<div class="b" style="height:'+Math.max(4,hpc(l.paceSec))+'%" title="'+
    l.mi.toFixed(2)+' mi @ '+fmtPace(l.paceSec)+'/mi"></div>';});
  h+='</div>';
  h+='<h4>Splits — tap one to inspect it</h4>';
  const fastest=Math.min(...laps.map(l=>l.paceSec));
  laps.forEach((l,i)=>{
   const w=35+55*(fastest/l.paceSec);
   let pm='';
   if(i>0){const dlt=laps[i-1].paceSec-l.paceSec;
    pm='<span class="pm" style="color:'+(dlt>=0?'var(--good)':'var(--hard)')+'">'+
     (dlt>=0?'+':'−')+fmtPace(Math.abs(dlt))+'</span>';}
   h+='<div class="splitrow" onclick="selectLap('+i+')"><span class="n">'+
    (l.mi>=0.95&&l.mi<=1.05?(i+1):l.mi.toFixed(2))+
    '</span><div class="bar" style="width:'+w.toFixed(0)+'%">'+fmtPace(l.paceSec)+'/mi</div>'+pm+'</div>';
  });
 }
 if(RS.n>=5){
  h+='<h4>Analysis</h4><div class="selstats" id="selstats"></div>'+
   '<div class="chartwrap" id="chartsbox">';
  if(RS.pace.some(x=>x!=null)){h+=chartX('pc',RS.pace,86,'#3ec6c0',true);RS.charts.push('pc');}
  if(RS.hr.some(x=>x!=null)){h+=chartX('hr',RS.hr,70,'#ff6b6b',false);RS.charts.push('hr');}
  if(RS.elev&&RS.elev.some(x=>x!=null)){h+=chartX('el',RS.elev,52,'#5b8db8',false);RS.charts.push('el');}
  h+='<div class="playhead" id="chartPlayhead"></div>'+
     '<div class="ctip" id="ctip"></div></div>';
  const isTouch='ontouchstart' in window||navigator.maxTouchPoints>0;
  if(isTouch)h+='<div style="text-align:center;font-size:12px;color:var(--faint);margin:-2px 0 10px">Slide to analyze pace and HR</div>';
  h+=zonesHtml();
 }
 const ann=(S.ann||{})[String(CUR_AID)]||{};
 h+='<h4>How did it feel? <span id="rpeVal" style="color:var(--accent);font-weight:600;text-transform:none;letter-spacing:0">'+
   (ann.rpe?ann.rpe+' · '+rpeName(ann.rpe):'slide to rate')+'</span></h4>'+
  '<input type="range" id="rpeSlide" min="1" max="10" step="1" value="'+(ann.rpe||5)+
   '" oninput="rpeLab(this.value)" style="width:100%">'+
  '<div style="display:flex;color:var(--faint);font-size:11px;justify-content:space-between;margin:2px 2px 9px">'+
  '<span>1–3 recovery</span><span>4–6 easy</span><span>7–8 hard</span><span>9–10 max</span></div>'+
  '<input id="annNote" placeholder="Notes — how it went, what hurt, what worked" value="'+
   escapeHTML(ann.note||'')+'" style="width:100%;background:var(--cell);'+
   'border:1px solid var(--line);color:var(--tx);border-radius:9px;padding:10px 11px;font-size:16px;margin-bottom:8px">'+
  (function(){
   const known=(S.gear||[]).filter(g=>!g.retired);
   const def=known.find(g=>g.isDefault);
   const selKey=ann.shoes||(def?def.key:'');
   let s='<select id="annShoesSel" onchange="shoesSel(this)" style="width:100%;background:var(--cell);'+
    'border:1px solid var(--line);color:var(--tx);border-radius:9px;padding:10px 11px;font-size:16px;margin-bottom:8px">'+
    '<option value="">Shoes — none logged</option>';
   known.forEach(g=>{s+='<option value="'+escapeHTML(g.key)+'"'+(selKey===g.key?' selected':'')+'>'+
     escapeHTML(g.nickname||g.display)+(g.isDefault?' · default':'')+'</option>';});
   if(ann.shoes&&!known.some(g=>g.key===ann.shoes))
    s+='<option value="'+escapeHTML(ann.shoes)+'" selected>'+escapeHTML(ann.shoes)+'</option>';
   s+='<option value="__new">+ New shoe…</option></select>'+
    '<input id="annShoesNew" placeholder="New shoe name (e.g. Superblast 2)" style="display:none;width:100%;'+
    'background:var(--cell);border:1px solid var(--line);color:var(--tx);border-radius:9px;'+
    'padding:10px 11px;font-size:16px">';
   return s;})();
 h+='<div class="row"><button onclick="closeRun()">Close</button>'+
  '<button class="primary" onclick="saveAnn(true)">Save & done</button></div>';
 document.getElementById('rmodal').innerHTML=h;
 initRunUX();
}
function rpeName(v){v=+v;return v<=3?'Recovery':(v<=6?'Easy':(v<=8?'Hard':'Max effort'));}
function rpeLab(v){
 CUR_RPE=+v;
 const el=document.getElementById('rpeVal');
 if(el)el.textContent=v+' · '+rpeName(v);
}
function shoesSel(s){
 document.getElementById('annShoesNew').style.display=s.value==='__new'?'':'none';
 if(s.value==='__new')document.getElementById('annShoesNew').focus();
}
async function saveAnn(close){
 const note=document.getElementById('annNote').value;
 const sel=document.getElementById('annShoesSel');
 let shoes=sel?sel.value:'';
 if(shoes==='__new')shoes=document.getElementById('annShoesNew').value.trim();
 try{
  await jpost('/api/annotate',{activityId:CUR_AID,rpe:CUR_RPE,note:note,shoes:shoes});
  S.ann=S.ann||{};S.ann[String(CUR_AID)]={rpe:CUR_RPE,note:note,shoes:shoes};
  toast('Logged — this is your data now');
  if(close)closeRun();
  jget('/api/gear').then(j=>{S.gear=j.gear||[];render();}).catch(()=>{render();});
 }catch(e){toast('Save failed: '+escapeHTML(e.message),{err:1});}
}

function gearEdit(key){
 const g=(S.gear||[]).find(x=>x.key===key);
 if(!g)return;
 const lbl=t=>'<label style="display:block;font-size:12px;color:var(--dim);margin:8px 0 4px">'+t+'</label>';
 let h='<h3>'+escapeHTML(g.nickname||g.display)+'</h3><p>'+g.mi.toFixed(0)+' mi across '+g.runs+' runs'+
  (g.last?' · last used '+g.last:'')+'</p>'+
  lbl('Nickname')+'<input id="gDisp" value="'+escapeHTML(g.nickname||g.display)+'">'+
  '<div style="display:flex;gap:8px">'+
  '<div style="flex:1">'+lbl('Brand')+'<input id="gBrand" value="'+escapeHTML(g.brand||'')+'"></div>'+
  '<div style="flex:1">'+lbl('Model')+'<input id="gModel" value="'+escapeHTML(g.model||'')+'"></div></div>'+
  lbl('Miles before timely (if not new when first logged)')+
  '<input id="gStart" type="number" value="0">'+
  lbl('Retire at (mi)')+'<input id="gThresh" type="number" value="'+g.threshold+'">'+
  '<div class="row">'+
  '<button onclick="gearSave(\''+key+'\',true,false)" style="color:var(--hard)">Retire</button>'+
  (g.isDefault?'':'<button onclick="gearSave(\''+key+'\',false,true)">Make default</button>')+
  '<button onclick="closeDetail()">Cancel</button>'+
  '<button class="primary" onclick="gearSave(\''+key+'\',false,false)">Save</button></div>';
 document.getElementById('dmodal').innerHTML=h;
 document.getElementById('dscrim').classList.add('show');
}
async function gearSave(key,retire,makeDefault){
 try{
  await jpost('/api/gear',{key:key,display:document.getElementById('gDisp').value,
   brand:document.getElementById('gBrand').value,
   model:document.getElementById('gModel').value,
   startMi:parseFloat(document.getElementById('gStart').value)||0,
   thresholdMi:parseFloat(document.getElementById('gThresh').value)||400,
   retired:retire,isDefault:makeDefault?true:undefined});
  closeDetail();toast(retire?'Retired — thanks for the miles':
   (makeDefault?'Default trainer set':'Gear updated'));
  const j=await jget('/api/gear');S.gear=j.gear||[];render();
 }catch(e){toast('Gear save failed: '+escapeHTML(e.message),{err:1});}
}

/* ---------------- vacation mode: plan around it ---------------- */
let VPLAN=null;
function openVacation(){
 document.getElementById('scrim').classList.add('show');
 ['vfrom','vto'].forEach(id=>document.getElementById(id).oninput=previewVacation);
 previewVacation();
}
function closeVacation(){document.getElementById('scrim').classList.remove('show');}

function buildVacationPlan(f,t){
 // Rule-based coach: easy/strides in range → skip (volume filler).
 // Long runs and quality → first clean day after return, hard days never
 // adjacent. Long runs prefer weekends.
 const hits=S.schedule.filter(i=>i.date>=f&&i.date<=t)
   .sort((a,b)=>a.date.localeCompare(b.date));
 const occupied=new Set(S.schedule.filter(i=>i.date<f||i.date>t).map(i=>i.date));
 const hardSet=new Set(S.schedule.filter(i=>(i.date<f||i.date>t)&&isHard(i.title)).map(i=>i.date));
 const near=d=>hardSet.has(d)||hardSet.has(fmt(new Date(parse(d).getTime()-DAY)))||
              hardSet.has(fmt(new Date(parse(d).getTime()+DAY)));
 const isLong=ti=>/mi LR|MP Finish/.test(ti);
 const actions=[];
 function place(it,preferWeekend){
  for(let k=1;k<=14;k++){
   const d=fmt(new Date(parse(t).getTime()+k*DAY));
   const dow=parse(d).getDay();
   if(occupied.has(d))continue;
   if(near(d))continue;
   if(preferWeekend&&k<=9&&dow!==6&&dow!==0)continue;
   occupied.add(d);hardSet.add(d);
   actions.push({act:'move',it:it,to:d});
   return;
  }
  actions.push({act:'skip',it:it,why:'no clean slot'});
 }
 hits.filter(i=>isLong(i.title)).forEach(i=>place(i,true));
 hits.filter(i=>!isLong(i.title)&&isHard(i.title)).forEach(i=>place(i,false));
 hits.filter(i=>!isHard(i.title)).forEach(i=>actions.push({act:'skip',it:i}));
 actions.sort((a,b)=>a.it.date.localeCompare(b.it.date));
 return actions;
}

function previewVacation(){
 const f=document.getElementById('vfrom').value,t=document.getElementById('vto').value;
 const pv=document.getElementById('vpreview'),go=document.getElementById('vgo');
 VPLAN=null;
 if(!f||!t||t<f){pv.textContent='Pick dates to preview.';go.disabled=true;return;}
 const acts=buildVacationPlan(f,t);
 if(!acts.length){pv.textContent='No workouts in that range — enjoy the trip.';go.disabled=true;return;}
 VPLAN={from:f,to:t,actions:acts};
 pv.innerHTML=acts.map(a=>{
  const name=escapeHTML(a.it.title.replace(/^W\d+ /,''));
  if(a.act==='skip')
   return '<span style="color:var(--faint)">skip</span> '+name+(a.why?' <span style="color:var(--faint)">('+escapeHTML(a.why)+')</span>':'');
  return '<span style="color:var(--accent)">move</span> '+name+' → <b>'+
   parse(a.to).toLocaleDateString(undefined,{weekday:'short',month:'short',day:'numeric'})+'</b>';
 }).join('<br>');
 const moves=acts.filter(a=>a.act==='move').length;
 go.disabled=false;
 go.textContent='Apply: move '+moves+', skip '+(acts.length-moves);
}

async function doVacation(){
 if(!VPLAN)return;
 const acts=VPLAN.actions;
 closeVacation();toast('Re-planning around your trip…',{sticky:1});
 try{
  for(const a of acts){
   if(a.act==='move')
    await jpost('/api/move',{scheduleId:a.it.scheduleId,workoutId:a.it.workoutId,date:a.to});
   else
    await jpost('/api/unschedule',{scheduleId:a.it.scheduleId});
  }
  toast('Done — plan adjusted around your vacation. Sync your watch.');
  load(true);
 }catch(e){toast('Vacation plan failed partway: '+escapeHTML(e.message)+' — hit Refresh to see current state.',{err:1});load(true);}
}

try{setView(localStorage.getItem('coachView')||'today');}catch(e){setView('today');}
load(false);
