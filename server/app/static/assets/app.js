/* Admin Portal SPA — Employee Monitoring & Endpoint Management (PRD §21, §22). */
"use strict";

const API = "";
const S = {
  token: localStorage.getItem("emp_token") || "",
  refresh: localStorage.getItem("emp_refresh") || "",
  role: localStorage.getItem("emp_role") || "",
  tenant: localStorage.getItem("emp_tenant") || "",
  name: localStorage.getItem("emp_name") || "",
  activeTenant: localStorage.getItem("emp_active_tenant") || "", // platform admin selects a tenant
};

/* ---------------- api client ---------------- */
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (S.token) headers["Authorization"] = "Bearer " + S.token;
  if (opts.body && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  let res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401 && S.refresh && !opts._retry) {
    if (await tryRefresh()) return api(path, { ...opts, _retry: true });
    logout();
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || JSON.stringify(j); } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}
function qp(extra = {}) {
  const p = new URLSearchParams();
  const t = tenantParam();
  if (t) p.set("tenant_id", t);
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== null && v !== "") p.set(k, v);
  const s = p.toString();
  return s ? "?" + s : "";
}
function tenantParam() {
  return S.role === "platform_super_admin" ? S.activeTenant : S.tenant;
}
async function tryRefresh() {
  try {
    const r = await fetch(API + "/api/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: S.refresh }),
    });
    if (!r.ok) return false;
    const d = await r.json();
    setSession(d);
    return true;
  } catch { return false; }
}
function setSession(d) {
  S.token = d.access_token; S.refresh = d.refresh_token; S.role = d.role;
  S.tenant = d.tenant_id || ""; S.name = d.full_name || "";
  localStorage.setItem("emp_token", S.token);
  localStorage.setItem("emp_refresh", S.refresh);
  localStorage.setItem("emp_role", S.role);
  localStorage.setItem("emp_tenant", S.tenant);
  localStorage.setItem("emp_name", S.name);
}
function logout() {
  api("/api/auth/logout", { method: "POST" }).catch(() => {});
  ["emp_token","emp_refresh","emp_role","emp_tenant","emp_name","emp_active_tenant"].forEach(k=>localStorage.removeItem(k));
  Object.assign(S, { token:"", refresh:"", role:"", tenant:"", name:"", activeTenant:"" });
  location.hash = ""; renderLogin();
}

/* ---------------- ui helpers ---------------- */
const el = (h) => { const t = document.createElement("template"); t.innerHTML = h.trim(); return t.content.firstChild; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;" }[c]));
function toast(msg, err=false){ const t=document.getElementById("toast"); t.textContent=msg; t.className="toast show"+(err?" err":""); setTimeout(()=>t.className="toast",3200); }
function fmtDate(s){ if(!s) return "—"; const d=new Date(s); return isNaN(d)?"—":d.toLocaleString(); }
function ago(s){ if(!s) return "never"; const d=(Date.now()-new Date(s))/1000; if(d<60)return Math.floor(d)+"s"; if(d<3600)return Math.floor(d/60)+"m"; if(d<86400)return Math.floor(d/3600)+"h"; return Math.floor(d/86400)+"d"; }
function statusBadge(s){ const m={active:"b-ok",online:"b-ok",offline:"b-off",pending:"b-pending",revoked:"b-rev"}; return `<span class="badge ${m[s]||"b-off"}">${esc(s)}</span>`; }
function sevBadge(s){ return `<span class="badge b-${esc(s)}">${esc(s)}</span>`; }

function modal(title, bodyNode, onSubmit, submitLabel="Save"){
  const bg=el(`<div class="modal-bg"></div>`);
  const m=el(`<div class="modal"><h3>${esc(title)}</h3></div>`);
  m.appendChild(bodyNode);
  const bar=el(`<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:18px"></div>`);
  if(onSubmit){
    const cancel=el(`<button class="btn ghost">Cancel</button>`); cancel.onclick=()=>bg.remove();
    bar.appendChild(cancel);
    const ok=el(`<button class="btn">${esc(submitLabel)}</button>`);
    ok.onclick=async()=>{ try{ await onSubmit(); bg.remove(); }catch(e){ toast(e.message,true);} };
    bar.appendChild(ok);
  } else {
    // info-only dialog (e.g. the "new password" result) — a single primary Close/Done button
    const label = (submitLabel && submitLabel !== "Save") ? submitLabel : "Close";
    const close=el(`<button class="btn">${esc(label)}</button>`); close.onclick=()=>bg.remove();
    bar.appendChild(close);
  }
  m.appendChild(bar); bg.appendChild(m);
  bg.onclick=(e)=>{ if(e.target===bg) bg.remove(); };
  document.body.appendChild(bg); return bg;
}
function fields(defs){ // [{k,label,type,value,options}]
  const wrap=el(`<div></div>`); const inputs={};
  defs.forEach(d=>{
    const f=el(`<div class="field"><label>${esc(d.label)}</label></div>`);
    let inp;
    if(d.type==="select"){ inp=el(`<select></select>`); (d.options||[]).forEach(o=>{ const op=el(`<option value="${esc(o.v??o)}">${esc(o.t??o)}</option>`); if((o.v??o)==d.value)op.selected=true; inp.appendChild(op);}); }
    else if(d.type==="textarea"){ inp=el(`<textarea rows="5"></textarea>`); inp.value=d.value??""; }
    else { inp=el(`<input type="${d.type||"text"}" />`); inp.value=d.value??""; }
    f.appendChild(inp); wrap.appendChild(f); inputs[d.k]=inp;
  });
  wrap._values=()=>Object.fromEntries(Object.entries(inputs).map(([k,i])=>[k,i.value]));
  return wrap;
}

/* ---------------- navigation ---------------- */
const NAV = [
  ["dashboard","Dashboard","▦"],
  ["devices","Devices","▣"],
  ["employees","Employees","☺"],
  ["assets","Assets","▤"],
  ["software","Software","◉"],
  ["activity","Web / App Activity","◍"],
  ["policies","Policies","⚙"],
  ["alerts","Alerts","⚑"],
  ["evidence","Screenshots / Evidence","▧"],
  ["remote","Remote Support","⤢"],
  ["reports","Reports","▭"],
  ["downloads","Downloads","⬇"],
  ["licenses","Licenses & Tenants","🔑"],
  ["audit","Audit Logs","❏"],
  ["settings","Settings","⋯"],
];

function renderShell(){
  const app=document.getElementById("app");
  const isPlatform = S.role==="platform_super_admin";
  app.innerHTML="";
  const shell=el(`<div class="shell"></div>`);
  const side=el(`<div class="side"></div>`);
  side.appendChild(el(`<div class="brand"><div class="logo"><img src="/assets/logo.jpg" alt="Voyager"/></div><div>Voyager<br><span class="brand-sub">Endpoint Mgmt</span></div></div>`));
  const nav=el(`<div class="nav"></div>`);
  NAV.forEach(([k,label,icon])=>{ nav.appendChild(el(`<a href="#${k}" data-k="${k}"><span>${icon}</span>${esc(label)}</a>`)); });
  side.appendChild(nav);
  side.appendChild(el(`<div class="who">${esc(S.name||"user")}<br><span class="pill">${esc(S.role)}</span>
    <div style="margin-top:10px"><button class="btn ghost sm" id="logoutBtn">Sign out</button></div></div>`));
  shell.appendChild(side);
  shell.appendChild(el(`<div class="main" id="main"></div>`));
  app.appendChild(shell);
  side.querySelector("#logoutBtn").onclick=logout;
  route();
  window.onhashchange=route;
}

function setActive(k){ document.querySelectorAll(".nav a").forEach(a=>a.classList.toggle("active",a.dataset.k===k)); }


const VIEWS = {};
async function route(){
  if(!S.token){ renderLogin(); return; }
  const k=(location.hash.replace("#","")||"dashboard").split("/")[0];
  setActive(k);
  const main=document.getElementById("main"); if(!main){ renderShell(); return; }
  const need = S.role==="platform_super_admin" && !S.activeTenant && !["dashboard","licenses","audit","settings"].includes(k);
  try{
    // License activation gate for company users (PRD §8: inactive until attached to a server).
    if(S.role!=="platform_super_admin" && !["activate","settings"].includes(k)){
      const ls=await api("/api/license/status").catch(()=>({usable:true}));
      if(!ls.usable){ if(k!=="activate"){ location.hash="activate"; return; } }
    }
    if(need){ main.innerHTML=""; main.appendChild(topbar("Select a tenant"));
      main.appendChild(el(`<div class="notice">As Platform Super Admin, pick a tenant in <a href="#licenses">Licenses & Tenants</a> to view its data.</div>`)); return; }
    await VIEWS[k]?.(main, location.hash.split("/").slice(1));
  }catch(e){ main.innerHTML=""; main.appendChild(topbar("Error")); main.appendChild(el(`<div class="notice">${esc(e.message)}</div>`)); }
}

VIEWS.activate = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Activate this server"));
  const ls=await api("/api/license/status").catch(()=>({}));
  if(ls.usable){ main.appendChild(el(`<div class="notice">✓ Your license is active (${esc(ls.label||"active")}). <a href="#dashboard">Go to dashboard</a>.</div>`)); return; }
  main.appendChild(el(`<div class="notice">Your license is <b>${esc(ls.label||"inactive")}</b>. Agents cannot enroll until you attach the license key issued by your provider. Enter the License ID and License Key from your welcome email (or the .lic file).</div>`));
  const c=el(`<div class="card" style="max-width:560px"></div>`);
  const f=fields([
    {k:"license_id",label:"License ID",value:ls.license_id||""},
    {k:"license_key",label:"License Key",type:"textarea",value:""},
  ]);
  c.appendChild(f);
  const b=el(`<button class="btn">Activate license</button>`);
  b.onclick=async()=>{ try{
    const v=f._values();
    await api("/api/license/activate-here",{method:"POST",body:{license_id:v.license_id.trim(),license_key:v.license_key.trim()}});
    toast("License activated — this server is live"); location.hash="dashboard";
  }catch(e){ toast(e.message,true); } };
  c.appendChild(b);
  main.appendChild(c);
};

function topbar(title, tools){
  const t=el(`<div class="topbar"><h2>${esc(title)}</h2><div class="tools"></div></div>`);
  if(S.role==="platform_super_admin"){
    const span=el(`<span class="pill">tenant: ${esc(S.activeTenant||"— none —")}</span>`);
    t.querySelector(".tools").appendChild(span);
  }
  if(tools) tools.forEach(n=>t.querySelector(".tools").appendChild(n));
  return t;
}

/* ================= VIEWS ================= */

VIEWS.dashboard = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Dashboard"));
  if(S.role==="platform_super_admin" && !S.activeTenant){
    main.appendChild(el(`<div class="notice">Pick a tenant in Licenses & Tenants to see device metrics. Platform-wide tenant list below.</div>`));
    const ts=await api("/api/tenants");
    const c=el(`<div class="card"></div>`); c.appendChild(renderTenantTable(ts)); main.appendChild(c); return;
  }
  const d=await api("/api/dashboard"+qp());
  const k=el(`<div class="grid kpis"></div>`);
  const kpi=(n,l,sub)=>el(`<div class="card kpi"><div class="n">${n}</div><div class="l">${l}</div>${sub?`<div class="sub">${sub}</div>`:""}</div>`);
  k.appendChild(kpi(d.devices.total,"Devices",`${d.devices.online} online · ${d.devices.offline} offline · ${d.devices.pending} pending`));
  k.appendChild(kpi(d.alerts_open,"Open alerts",Object.entries(d.alerts_by_severity||{}).map(([s,c])=>`${s}:${c}`).join(" · ")||"none"));
  k.appendChild(kpi(d.employees,"Employees"));
  k.appendChild(kpi(d.assets,"Assets"));
  k.appendChild(kpi(d.software_distinct,"Distinct software"));
  k.appendChild(kpi(d.evidence,"Evidence items"));
  if(d.license){ const L=d.license;
    k.appendChild(kpi(L.days_left+"d","License",`${esc(L.edition)} · ${esc(L.status)} · ${L.used_devices}/${L.max_devices} seats`)); }
  main.appendChild(k);

  // ---- interactive charts (PRD §2.7 / §3.3) ----
  const charts=el(`<div class="grid" style="grid-template-columns:1fr 1fr;margin-top:14px"></div>`);
  const dv=d.devices;
  const donut=donutSVG([["online",dv.online,"#22c55e"],["offline",dv.offline,"#93a2c4"],["pending",dv.pending,"#3b82f6"]]);
  const c1=el(`<div class="card"><h3 style="margin:0 0 10px">Devices</h3></div>`); c1.appendChild(donut); charts.appendChild(c1);
  const sev=d.alerts_by_severity||{};
  const bars=barsSVG([["critical",sev.critical||0,"#ef4444"],["high",sev.high||0,"#fb7185"],["medium",sev.medium||0,"#f59e0b"],["low",sev.low||0,"#38bdf8"],["info",sev.info||0,"#7dd3fc"]]);
  const c2=el(`<div class="card"><h3 style="margin:0 0 10px">Open alerts by severity</h3></div>`); c2.appendChild(bars); charts.appendChild(c2);
  main.appendChild(charts);

  const live=el(`<div class="card" style="margin-top:14px"><h3 style="margin:0 0 10px">Live device health</h3></div>`);
  const health=await api("/api/health/latest"+qp());
  live.appendChild(tableFrom(["Device","Status","CPU %","RAM %","Disk %","Battery","Last seen"],
    health.map(h=>[`<a href="#devices/${h.device_id}">${esc(h.hostname||h.device_id)}</a>`, statusBadge(h.status),
      fmtNum(h.cpu), fmtNum(h.ram), fmtNum(h.disk), h.battery!=null?fmtNum(h.battery):"—", ago(h.last_seen)])));
  main.appendChild(live);
};
const fmtNum=(n)=> n==null?"—":Number(n).toFixed(0);

VIEWS.devices = async (main, args) => {
  if(args && args[0]){ return deviceDetail(main, args[0]); }
  main.innerHTML=""; main.appendChild(topbar("Devices", [tokenBtn()]));
  const bar=el(`<div class="toolbar"></div>`);
  const search=el(`<input placeholder="Search hostname..." />`);
  const sel=el(`<select><option value="">All statuses</option><option>active</option><option>offline</option><option>pending</option><option>revoked</option></select>`);
  bar.append(search,sel); main.appendChild(bar);
  const host=el(`<div class="card"></div>`); main.appendChild(host);
  async function load(){
    const rows=await api("/api/devices"+qp({q:search.value, status_filter:sel.value}));
    host.innerHTML="";
    host.appendChild(tableFrom(["Hostname","OS","Status","IP","Agent","Last seen","Actions"],
      rows.map(d=>[`<a href="#devices/${d.id}">${esc(d.hostname||"(unnamed)")}</a>`,
        esc((d.os_name||"")+" "+(d.os_version||"")), statusBadge(d.status), esc(d.ip_address||"—"),
        esc(d.agent_version||"—"), ago(d.last_seen),
        `<button class="btn sm ghost" data-ss="${d.id}">Screenshot</button> <button class="btn sm danger" data-rev="${d.id}">Revoke</button>`])));
    host.querySelectorAll("[data-ss]").forEach(b=>b.onclick=()=>requestScreenshot(b.dataset.ss));
    host.querySelectorAll("[data-rev]").forEach(b=>b.onclick=async()=>{ if(confirm("Revoke device? Agent will be denied.")){ await api(`/api/devices/${b.dataset.rev}/revoke`,{method:"POST"}); toast("Device revoked"); load(); }});
  }
  search.oninput=debounce(load,300); sel.onchange=load; load();
};

async function deviceDetail(main, id){
  main.innerHTML=""; main.appendChild(topbar("Device detail",[el(`<a class="btn ghost sm" href="#devices">← Back</a>`)]));
  const d=await api(`/api/devices/${id}/detail`);
  const dev=d.device;
  const info=el(`<div class="card"></div>`);
  info.appendChild(el(`<h3 style="margin:0 0 6px">${esc(dev.hostname)} ${statusBadge(dev.status)}</h3>`));
  info.appendChild(el(`<div class="muted" style="margin-bottom:10px">${esc(dev.os_name||"")} ${esc(dev.os_version||"")} · ${esc(dev.ip_address||"")} · ${esc(dev.mac_address||"")} · agent ${esc(dev.agent_version||"?")}</div>`));
  const hw=dev.hardware||{};
  info.appendChild(el(`<div class="toolbar"><span class="pill">CPU: ${esc(hw.cpu||"?")}</span><span class="pill">RAM: ${esc(hw.ram_gb||"?")} GB</span><span class="pill">Disk: ${esc(hw.disk_gb||"?")} GB</span><span class="pill">Dept: ${esc(dev.department||"—")}</span><span class="pill">Loc: ${esc(dev.location||"—")}</span>
    <button class="btn sm" id="viewBtn">👁 View screen (silent)</button><button class="btn sm ghost" id="ssBtn">Request screenshot</button><button class="btn sm ghost" id="rsBtn">Remote support</button><button class="btn sm ghost" id="syncBtn">Force resync</button></div>`));
  main.appendChild(info);
  info.querySelector("#viewBtn").onclick=()=>silentView(id, dev.hostname);
  info.querySelector("#ssBtn").onclick=()=>requestScreenshot(id);
  info.querySelector("#rsBtn").onclick=()=>requestRemote(id);
  info.querySelector("#syncBtn").onclick=async()=>{ await api(`/api/devices/${id}/resync`,{method:"POST"}); toast("Resync queued"); };

  // ---- Per-agent data profile (what this device collects / shows) ----
  const prof=d.collection||{};
  const CATS=[["health","Resource health (CPU/RAM/disk/battery)"],["active_time","Active-time tracking"],
    ["software","Installed software inventory"],["activity","Web / application activity"],
    ["website","Website monitoring"],["email","Email monitoring"],["file_events","File / DLP events"],
    ["usb","USB control"],["keystrokes","Keystroke logging (passwords skipped)"],["screenshots","Screenshots"]];
  const ROADMAP=new Set(["email","usb","keystrokes"]);   // platform-side collector pending
  const pc=el(`<div class="card" style="margin-top:14px"><h3 style="margin:0 0 6px">Data collection profile</h3>
    <div class="muted" style="margin-bottom:10px">Choose what to collect from <b>${esc(dev.hostname)}</b>. Disabled categories are not gathered by the agent and won't appear here — applied on the agent's next sync. Items marked (soon) are configured here but their Windows-side collector is still in development.</div></div>`);
  const togg=el(`<div style="display:flex;flex-wrap:wrap;gap:14px"></div>`);
  const boxes={};
  CATS.forEach(([k,label])=>{ const w=el(`<label style="display:flex;align-items:center;gap:6px;background:var(--panel2);border:1px solid var(--line);padding:8px 12px;border-radius:8px;cursor:pointer"></label>`);
    const cb=el(`<input type="checkbox" />`); cb.checked=!!prof[k]; boxes[k]=cb; w.append(cb, el(`<span>${esc(label)}${ROADMAP.has(k)?' <span class="muted">(soon)</span>':''}</span>`)); togg.appendChild(w); });
  pc.appendChild(togg);
  const iv=el(`<div class="field" style="max-width:260px;margin-top:12px"><label>Screenshot interval (seconds, 0 = on request only)</label><input type="number" min="0" value="${Number(prof.screenshot_interval||0)}" id="ssIv"/></div>`);
  pc.appendChild(iv);
  const save=el(`<button class="btn">Save profile</button>`);
  save.onclick=async()=>{ const body={}; CATS.forEach(([k])=>body[k]=boxes[k].checked); body.screenshot_interval=Number(pc.querySelector("#ssIv").value||0);
    try{ await api(`/api/devices/${id}/collection`,{method:"PUT",body}); toast("Profile saved — agent will apply on next sync"); }catch(e){toast(e.message,true);} };
  pc.appendChild(save);
  main.appendChild(pc);

  const grid=el(`<div class="grid" style="grid-template-columns:1fr 1fr;margin-top:14px"></div>`);
  grid.appendChild(cardTable("Recent alerts",["Sev","Rule","Status","When"],
    d.alerts.map(a=>[sevBadge(a.severity),esc(a.rule),esc(a.status),ago(a.created_at)])));
  grid.appendChild(cardTable("Software ("+d.software.length+")",["Name","Version","Status"],
    d.software.slice(0,30).map(s=>[esc(s.name),esc(s.version||""),s.list_status==="block"?`<span class="badge b-high">block</span>`:esc(s.list_status)])));
  grid.appendChild(cardTable("Recent activity",["When","App / Site","Title"],
    d.activity.slice(0,25).map(e=>[ago(e.ts),esc(e.app||e.domain||e.type),esc((e.title||"").slice(0,50))])));
  const evCard=cardTable("Evidence",["When","Reason","View"],
    d.evidence.map(e=>[fmtDate(e.captured_at),esc(e.reason),`<button class="btn sm ghost" data-ev="${e.id}">open</button>`]));
  evCard.querySelectorAll("[data-ev]").forEach(b=>b.onclick=async()=>{ try{ const u=await imgBlobURL(`/api/screenshots/${b.dataset.ev}/image`); window.open(u,"_blank"); }catch(e){toast(e.message,true);} });
  grid.appendChild(evCard);
  main.appendChild(grid);
}

VIEWS.employees = async (main) => {
  main.innerHTML="";
  const add=el(`<button class="btn sm">+ Employee</button>`);
  add.onclick=()=>{ const f=fields([
    {k:"employee_code",label:"Employee code"},{k:"name",label:"Name"},{k:"email",label:"Email",type:"email"},
    {k:"department",label:"Department"},{k:"designation",label:"Designation"},{k:"location",label:"Location"}]);
    modal("Add employee",f,async()=>{ await api("/api/employees"+qp(),{method:"POST",body:f._values()}); toast("Employee added"); VIEWS.employees(main); });};
  main.appendChild(topbar("Employees",[add]));
  const rows=await api("/api/employees"+qp());
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["Code","Name","Department","Designation","Location","Status"],
    rows.map(e=>[esc(e.employee_code),esc(e.name),esc(e.department||"—"),esc(e.designation||"—"),esc(e.location||"—"),statusBadge(e.status)])));
  main.appendChild(c);
};

VIEWS.assets = async (main) => {
  main.innerHTML="";
  const add=el(`<button class="btn sm">+ Asset</button>`);
  add.onclick=()=>{ const f=fields([
    {k:"asset_tag",label:"Asset tag"},
    {k:"category",label:"Category",type:"select",options:["computer","laptop","printer","network_device","software","peripheral"]},
    {k:"name",label:"Name"},{k:"serial_no",label:"Serial no"},{k:"model",label:"Model"},
    {k:"lifecycle",label:"Lifecycle",type:"select",options:["planned","in_stock","assigned","in_repair","retired","disposed"]},
    {k:"department",label:"Department"},{k:"location",label:"Location"}]);
    modal("Add asset",f,async()=>{ await api("/api/assets"+qp(),{method:"POST",body:f._values()}); toast("Asset added"); VIEWS.assets(main);});};
  main.appendChild(topbar("Assets",[add]));
  const rows=await api("/api/assets"+qp());
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["Tag","Category","Name","Serial","Lifecycle","Warranty"],
    rows.map(a=>[esc(a.asset_tag),esc(a.category),esc(a.name||"—"),esc(a.serial_no||"—"),`<span class="pill">${esc(a.lifecycle)}</span>`,a.warranty_expiry?fmtDate(a.warranty_expiry):"—"])));
  main.appendChild(c);
};

VIEWS.software = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Software inventory"));
  const bar=el(`<div class="toolbar"></div>`);
  const search=el(`<input placeholder="Search software..." />`);
  const sel=el(`<select><option value="">All</option><option value="block">Blocklisted</option><option value="allow">Allowlisted</option><option value="unknown">Unknown</option></select>`);
  bar.append(search,sel); main.appendChild(bar);
  const c=el(`<div class="card"></div>`); main.appendChild(c);
  async function load(){
    const rows=await api("/api/software"+qp({q:search.value,list_status:sel.value}));
    c.innerHTML="";
    c.appendChild(tableFrom(["Name","Publisher","Version","Status","Action"],
      rows.map(s=>[esc(s.name),esc(s.publisher||"—"),esc(s.version||"—"),
        s.list_status==="block"?`<span class="badge b-high">block</span>`:esc(s.list_status),
        `<button class="btn sm ghost" data-block="${s.id}">Block</button> <button class="btn sm ghost" data-allow="${s.id}">Allow</button>`])));
    c.querySelectorAll("[data-block]").forEach(b=>b.onclick=async()=>{await api(`/api/software/${b.dataset.block}/list-status?value=block`,{method:"POST"});toast("Blocklisted");load();});
    c.querySelectorAll("[data-allow]").forEach(b=>b.onclick=async()=>{await api(`/api/software/${b.dataset.allow}/list-status?value=allow`,{method:"POST"});toast("Allowlisted");load();});
  }
  search.oninput=debounce(load,300); sel.onchange=load; load();
};

VIEWS.activity = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Web / Application Activity"));
  const top=await api("/api/activity/top"+qp({by:"application",hours:24}));
  const topDom=await api("/api/activity/top"+qp({by:"domain",hours:24}));
  const grid=el(`<div class="grid" style="grid-template-columns:1fr 1fr"></div>`);
  grid.appendChild(cardTable("Top applications (24h)",["App","Duration","Events"],
    top.map(t=>[esc(t.name),fmtDur(t.duration_seconds),t.events])));
  grid.appendChild(cardTable("Top sites (24h)",["Domain","Duration","Events"],
    topDom.map(t=>[esc(t.name),fmtDur(t.duration_seconds),t.events])));
  main.appendChild(grid);
  const recent=await api("/api/activity"+qp({hours:24}));
  main.appendChild(cardTable("Recent events",["When","Device","Type","App / Site","Title"],
    recent.slice(0,100).map(e=>[fmtDate(e.ts),esc(e.device_id.slice(0,8)),esc(e.type),esc(e.app||e.domain||"—"),esc((e.title||"").slice(0,60))])));
};
const fmtDur=(s)=>{ s=s||0; if(s<60)return s+"s"; if(s<3600)return Math.floor(s/60)+"m"; return (s/3600).toFixed(1)+"h"; };

VIEWS.policies = async (main) => {
  main.innerHTML="";
  const add=el(`<button class="btn sm">+ Policy</button>`); add.onclick=()=>editPolicy(main,null);
  main.appendChild(topbar("Policies & Rules",[add]));
  main.appendChild(el(`<div class="notice">Rules: WHEN (event type) · IF (conditions) · THEN (actions). Scope: tenant → department → device.</div>`));
  const rows=await api("/api/policies"+qp());
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["Name","When","Scope","Priority","Enabled","Actions"],
    rows.map(p=>[esc(p.name),esc((p.rule.when||{}).type||"—"),`${esc(p.scope_type)}${p.scope_value?":"+esc(p.scope_value):""}`,
      p.priority,p.enabled?`<span class="badge b-ok">on</span>`:`<span class="badge b-off">off</span>`,
      `<button class="btn sm ghost" data-edit="${p.id}">Edit</button> <button class="btn sm danger" data-del="${p.id}">Delete</button>`])));
  c.querySelectorAll("[data-edit]").forEach(b=>b.onclick=()=>editPolicy(main,rows.find(p=>p.id===b.dataset.edit)));
  c.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{ if(confirm("Delete policy?")){await api(`/api/policies/${b.dataset.del}`,{method:"DELETE"});toast("Deleted");VIEWS.policies(main);}});
  main.appendChild(c);
};
function editPolicy(main,p){
  const f=fields([
    {k:"name",label:"Name",value:p?.name},
    {k:"priority",label:"Priority (lower first)",type:"number",value:p?.priority??100},
    {k:"scope_type",label:"Scope",type:"select",options:["tenant","department","location","device"],value:p?.scope_type||"tenant"},
    {k:"scope_value",label:"Scope value (dept/device id, blank for tenant)",value:p?.scope_value||""},
    {k:"rule",label:"Rule JSON (when/if/then)",type:"textarea",
     value:JSON.stringify(p?.rule||{when:{type:"health"},if:[{field:"cpu_percent",op:">",value:90,for_seconds:600}],then:[{action:"alert",severity:"high",message:"High CPU"}]},null,2)},
  ]);
  modal(p?"Edit policy":"New policy",f,async()=>{
    const v=f._values(); let rule; try{rule=JSON.parse(v.rule);}catch{throw new Error("Rule JSON invalid");}
    const body={name:v.name,priority:+v.priority,scope_type:v.scope_type,scope_value:v.scope_value||null,rule};
    if(p) await api(`/api/policies/${p.id}`+qp(),{method:"PUT",body});
    else await api("/api/policies"+qp(),{method:"POST",body});
    toast("Policy saved"); VIEWS.policies(main);
  });
}

VIEWS.alerts = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Alerts"));
  const bar=el(`<div class="toolbar"></div>`);
  const st=el(`<select><option value="">All</option><option>open</option><option>acknowledged</option><option>resolved</option></select>`);
  const sv=el(`<select><option value="">Any severity</option><option>critical</option><option>high</option><option>medium</option><option>low</option><option>info</option></select>`);
  bar.append(st,sv); main.appendChild(bar);
  const c=el(`<div class="card"></div>`); main.appendChild(c);
  async function load(){
    const rows=await api("/api/alerts"+qp({status_filter:st.value,severity:sv.value}));
    c.innerHTML="";
    c.appendChild(tableFrom(["Sev","Rule","Message","Value/Thr","Status","When","Actions"],
      rows.map(a=>[sevBadge(a.severity),esc(a.rule_name),esc((a.message||"").slice(0,60)),
        `${esc(a.current_value||"")}/${esc(a.threshold||"")}`,esc(a.status),ago(a.created_at),
        a.status!=="resolved"?`<button class="btn sm ghost" data-ack="${a.id}">Ack</button> <button class="btn sm" data-res="${a.id}">Resolve</button>`:"—"])));
    c.querySelectorAll("[data-ack]").forEach(b=>b.onclick=async()=>{await api(`/api/alerts/${b.dataset.ack}`,{method:"PATCH",body:{status:"acknowledged"}});load();});
    c.querySelectorAll("[data-res]").forEach(b=>b.onclick=async()=>{const note=prompt("Resolution note:")||"";await api(`/api/alerts/${b.dataset.res}`,{method:"PATCH",body:{status:"resolved",resolution_note:note}});load();});
  }
  st.onchange=load; sv.onchange=load; load();
};

VIEWS.evidence = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Screenshots / Evidence"));
  main.appendChild(el(`<div class="notice">Access is RBAC-restricted and every view/download/delete is audited. Evidence is encrypted at rest.</div>`));
  const rows=await api("/api/screenshots"+qp());
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["When","Device","Reason","Size","Retention","Actions"],
    rows.map(e=>[fmtDate(e.captured_at),esc(e.device_id.slice(0,8)),esc(e.reason),(e.size_bytes/1024).toFixed(0)+" KB",
      e.retention_until?fmtDate(e.retention_until):"—",
      `<button class="btn sm ghost" data-view="${e.id}">View</button> <button class="btn sm ghost" data-dl="${e.id}">Download</button> <button class="btn sm danger" data-del="${e.id}">Delete</button>`])));
  c.querySelectorAll("[data-view]").forEach(b=>b.onclick=async()=>{ try{ const u=await imgBlobURL(`/api/screenshots/${b.dataset.view}/image`); window.open(u,"_blank"); }catch(e){toast(e.message,true);} });
  c.querySelectorAll("[data-dl]").forEach(b=>b.onclick=()=>downloadWithAuth(`/api/screenshots/${b.dataset.dl}/download`,`evidence_${b.dataset.dl}.png`));
  c.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{ if(confirm("Delete evidence?")){await api(`/api/screenshots/${b.dataset.del}`,{method:"DELETE"});toast("Deleted");VIEWS.evidence(main);}});
  main.appendChild(c);
};

VIEWS.remote = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Remote Support"));
  main.appendChild(el(`<div class="notice">Authorized IT support only. Sessions are time-limited, tied to your identity, encrypted and audited. Consent mode is per policy.</div>`));
  const rows=await api("/api/remote"+qp());
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["Device","Status","Consent","Started","Ended","Expires","Action"],
    rows.map(s=>[esc(s.device_id.slice(0,8)),statusBadge(s.status),esc(s.consent_mode),fmtDate(s.started_at),fmtDate(s.ended_at),fmtDate(s.expires_at),
      ["requested","active"].includes(s.status)?`<button class="btn sm danger" data-end="${s.id}">End</button>`:"—"])));
  c.querySelectorAll("[data-end]").forEach(b=>b.onclick=async()=>{await api(`/api/remote/${b.dataset.end}/end`,{method:"POST"});toast("Session ended");VIEWS.remote(main);});
  main.appendChild(c);
};

VIEWS.reports = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Reports"));
  const kinds=[["devices","Device inventory"],["assets","Asset report"],["software","Software report"],
    ["alerts","Alert report"],["employees","Employee / device report"],["license","License report"]];
  const grid=el(`<div class="grid kpis"></div>`);
  kinds.forEach(([k,label])=>{
    const card=el(`<div class="card"><h3 style="margin:0 0 10px">${esc(label)}</h3></div>`);
    const row=el(`<div class="toolbar"></div>`);
    ["csv","xlsx","pdf"].forEach(fmt=>{ const b=el(`<a class="btn sm ghost" href="/api/reports/${k}?fmt=${fmt}${tenantParam()?"&tenant_id="+tenantParam():""}">${fmt.toUpperCase()}</a>`); row.appendChild(b); });
    card.appendChild(row); grid.appendChild(card);
  });
  main.appendChild(grid);
};

VIEWS.downloads = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Downloads"));
  if(S.role==="platform_super_admin"){
    main.appendChild(el(`<div class="notice">Downloads are per-company. Select a tenant in <a href="#licenses">Licenses & Tenants</a> to download their pre-configured agent, or sign in as the company owner.</div>`));
  }
  main.appendChild(el(`<div class="notice">Step 1 — install the <b>server software</b> (once, on your server machine). Step 2 — install the <b>agent software</b> on each employee PC. The agent is pre-configured with this server's address, your license and an enrollment token, so it enrolls automatically. Enrolled agents then appear on your dashboard.</div>`));
  const grid=el(`<div class="grid" style="grid-template-columns:1fr 1fr"></div>`);

  const s=el(`<div class="card"><h3 style="margin:0 0 8px">1 · Server software (Server_Setup.exe)</h3>
    <p class="muted">Double-click Windows installer — the Management Server + updater are bundled inside. No Python needed. It installs the service, adds a firewall rule and opens the console.</p></div>`);
  const sb=el(`<button class="btn">⬇ Download Server_Setup.exe</button>`);
  sb.onclick=()=>downloadWithAuth("/api/download/server","Server_Setup.exe");
  s.appendChild(sb);
  s.appendChild(el(`<p class="muted" style="margin-top:10px">Run it (allow through SmartScreen), set the port + license server URL, click Install. <code>update.exe</code> is installed alongside for one-click updates. <b>For Windows Server 2016 or newer.</b></p>`));
  const b12=el(`<button class="btn ghost" style="margin-top:8px">⬇ Windows Server 2012 bundle (.zip)</button>`);
  b12.onclick=()=>downloadWithAuth("/api/download/server-bundle","EndpointManagementServer-Universal-Windows.zip");
  s.appendChild(b12);
  s.appendChild(el(`<p class="muted" style="margin-top:8px">Server 2012 can't run the .exe. This zip runs on Python 3.8: extract → <code>SETUP_AND_RUN.bat</code> to install/run, <code>UPDATE.bat</code> to update from the license server.</p>`));
  grid.appendChild(s);

  const a=el(`<div class="card"><h3 style="margin:0 0 8px">2 · Agent software (.exe)</h3>
    <p class="muted">A standalone <b>VoyagerAgent.exe</b> — Python and every dependency are bundled inside, so <b>nothing needs installing</b> on the employee PC. Pre-configured with your server address, license and enrollment token.</p></div>`);
  const ab=el(`<button class="btn">⬇ Download agent (.exe package)</button>`);
  ab.onclick=async()=>{ try{ await downloadWithAuth("/api/download/agent", "VoyagerAgent.zip"); }catch(e){ toast(e.message,true); } };
  a.appendChild(ab);
  a.appendChild(el(`<p class="muted" style="margin-top:10px">Copy the folder to each PC → <b>double-click VoyagerAgent.exe</b>. It auto-enrolls, runs in the background and restarts at logon. Requires the license to be active.</p>`));
  grid.appendChild(a);

  main.appendChild(grid);
};

VIEWS.licenses = async (main) => {
  main.innerHTML="";
  const isPlatform=S.role==="platform_super_admin";
  const tools=[];
  if(isPlatform){
    const b=el(`<button class="btn sm">+ Customer</button>`); b.onclick=()=>addTenant(main); tools.push(b);
    const a=el(`<button class="btn sm ghost">Activate from license server</button>`); a.onclick=()=>activateOnline(main); tools.push(a);
  }
  main.appendChild(topbar("Licenses & Tenants",tools));
  const tenants=await api("/api/tenants");
  const c=el(`<div class="card"></div>`);
  c.appendChild(renderTenantTable(tenants));
  main.appendChild(c);
};
function renderTenantTable(tenants){
  const isPlatform=S.role==="platform_super_admin";
  const wrap=el(`<div></div>`);
  const t=tableFrom(["Company","Registered email","Status","Retention","Actions"],
    tenants.map(t=>[esc(t.company_name),esc(t.contact_email||"—"),statusBadge(t.status),
      `${t.evidence_retention_days}d ev / ${t.event_retention_days}d evt`,
      `<button class="btn sm ghost" data-view="${t.id}">View data</button>`+
      (isPlatform?` <button class="btn sm ghost" data-cred="${t.id}">Credentials</button> <button class="btn sm ghost" data-lic="${t.id}">+ License</button> <button class="btn sm ghost" data-lics="${t.id}">Licenses</button>`:"")]));
  wrap.appendChild(t);
  wrap.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>{ S.activeTenant=b.dataset.view; localStorage.setItem("emp_active_tenant",S.activeTenant); toast("Tenant selected"); location.hash="dashboard"; });
  wrap.querySelectorAll("[data-cred]").forEach(b=>b.onclick=()=>showCredentials(tenants.find(x=>x.id===b.dataset.cred)));
  wrap.querySelectorAll("[data-lic]").forEach(b=>b.onclick=()=>addLicense(b.dataset.lic));
  wrap.querySelectorAll("[data-lics]").forEach(b=>b.onclick=()=>showLicenses(b.dataset.lics));
  return wrap;
}

async function showCredentials(tenant){
  const users=await api(`/api/users?tenant_id=${tenant.id}`);
  const body=el(`<div></div>`);
  body.appendChild(el(`<div class="muted" style="margin-bottom:10px">Registered email: <b>${esc(tenant.contact_email||"—")}</b> · Phone: ${esc(tenant.contact_phone||"—")}</div>`));
  body.appendChild(el(`<div class="notice">Passwords are stored one-way (hashed) and cannot be shown. Use <b>Reset</b> to issue a new password — it is displayed once and can be emailed to the registered address.</div>`));
  const t=tableFrom(["Username / email","Role","Active","Last login","Action"],
    users.map(u=>[esc(u.email),`<span class="pill">${esc(u.role)}</span>`,u.is_active?`<span class="badge b-ok">yes</span>`:`<span class="badge b-off">no</span>`,
      u.last_login?fmtDate(u.last_login):"never",
      `<button class="btn sm" data-reset="${u.id}" data-email="${esc(u.email)}">Reset password</button>`]));
  body.appendChild(t);
  const bg=modal(`Credentials — ${esc(tenant.company_name)}`, body, null, "Close");
  body.querySelectorAll("[data-reset]").forEach(b=>b.onclick=()=>resetPassword(b.dataset.reset, b.dataset.email, tenant));
}

function resetPassword(userId, email, tenant){
  const f=fields([
    {k:"new_password",label:"New password (blank = auto-generate a strong one)",type:"text",value:""},
    {k:"email_it",label:"Also email it to the registered address?",type:"select",options:[{v:"true",t:"Yes"},{v:"false",t:"No"}],value:"true"},
  ]);
  modal(`Reset password — ${esc(email)}`, f, async()=>{
    const v=f._values();
    const r=await api(`/api/users/${userId}/reset-password`,{method:"POST",
      body:{new_password:v.new_password||null, email_it:v.email_it==="true"}});
    const emailLine = r.emailed==="smtp" ? `✓ Emailed to <b>${esc(tenant.contact_email||email)}</b>.`
      : (r.emailed==="skipped" ? "" : `⚠ Email not sent (no SMTP) — saved to server outbox.`);
    modal("New password", el(`<div>
      <p class="muted">Share this with the user. It is shown only once.</p>
      <pre class="json">Username : ${esc(r.email)}
Password : ${esc(r.new_password)}</pre>
      <p>${emailLine}</p></div>`), null, "Done");
  }, "Reset");
}
async function activateOnline(main){
  const meta=await api("/api/meta").catch(()=>({}));
  const f=fields([
    {k:"license_server",label:"Cloud license server URL",value:meta.license_server||"http://vmgmt.voyager.co.in:8084"},
    {k:"license_id",label:"License ID"},
    {k:"license_key",label:"License Key",type:"textarea"},
    {k:"owner_password",label:"Company admin password (set/confirm for this server)",type:"password"},
  ]);
  modal("Activate this server from the license server",f,async()=>{
    const v=f._values();
    const r=await api("/api/license/activate-online",{method:"POST",body:{
      license_server:v.license_server.trim(), license_id:v.license_id.trim(),
      license_key:v.license_key.trim(), owner_password:v.owner_password}});
    const out=el(`<div><p class="muted">${esc(r.message||"Activated.")}</p>
      <pre class="json">Company  : ${esc(r.company_name)}
Username : ${esc(r.owner_email||"—")}
Password : ${esc(r.owner_password||"(unchanged — use the password you set)")}</pre>
      <p class="muted">Sign out and sign in with the company admin above to manage this company.</p></div>`);
    modal("Server activated",out,null,"Done");
    VIEWS.licenses(main);
  },"Activate");
}

function addTenant(main){
  const f=fields([
    {k:"company_name",label:"Company name"},
    {k:"contact_email",label:"Registered email (receives the license key)",type:"email"},
    {k:"deployment_model",label:"Deployment",type:"select",options:["on_premise","cloud_vps","hybrid","central_saas"]},
    {k:"owner_name",label:"Owner name",value:"Account Owner"},
    {k:"owner_email",label:"Owner username / email (blank = registered email)",type:"email"},
    {k:"owner_password",label:"Owner password (blank = auto-generate)",type:"text"},
    {k:"edition",label:"License edition",type:"select",options:["standard","enterprise","demo","custom"],value:"standard"},
    {k:"license_type",label:"License type",type:"select",options:[{v:"subscription_monthly",t:"Subscription (monthly)"},{v:"lifetime",t:"Lifetime (one-time, 1yr support)"}],value:"subscription_monthly"},
    {k:"term_days",label:"Term (days) — ignored for lifetime",type:"number",value:365},
    {k:"max_devices",label:"Max devices",type:"number",value:25},
  ]);
  modal("New customer — create account, license & email key",f,async()=>{
    const v=f._values();
    const body={company_name:v.company_name,contact_email:v.contact_email,deployment_model:v.deployment_model,
      owner_name:v.owner_name,owner_email:v.owner_email||null,owner_password:v.owner_password||null,
      edition:v.edition,license_type:v.license_type,term_days:+v.term_days,max_devices:+v.max_devices,send_email:true};
    const r=await api("/api/tenants/provision",{method:"POST",body});
    const emailLine = r.email_status==="smtp"
      ? `✓ License key emailed to <b>${esc(v.contact_email)}</b>.`
      : `⚠ No SMTP configured — email saved to the server <b>outbox</b> (data/outbox). Configure SMTP to deliver for real.`;
    const out=el(`<div>
      <p class="muted">Account created. Hand these to the customer (password is shown only once).</p>
      <pre class="json">Company   : ${esc(r.company_name)}
Console   : ${esc(location.origin)}
Username  : ${esc(r.owner_email)}
Password  : ${esc(r.owner_password)}

License ID : ${esc(r.license_id)}
License Key: ${esc(r.license_key)}
Devices    : ${r.max_devices}
Expires    : ${fmtDate(r.expiry)}</pre>
      <p>${emailLine}</p></div>`);
    const dl=el(`<button class="btn">⬇ Download license key (.lic)</button>`);
    dl.onclick=()=>downloadWithAuth(r.download_url, esc(r.company_name)+".lic");
    out.appendChild(dl);
    modal("Customer provisioned",out,null,"Done");
    VIEWS.licenses(main);
  },"Create & email");
}

async function downloadWithAuth(path, filename){
  try{
    const res=await fetch(API+path,{headers:{Authorization:"Bearer "+S.token}});
    if(!res.ok){ let m="Download failed ("+res.status+")"; try{const j=await res.json(); m=j.detail||m;}catch{} throw new Error(m); }
    const cd=res.headers.get("content-disposition")||"";
    const mt=cd.match(/filename="?([^"]+)"?/); if(mt) filename=mt[1];
    const blob=await res.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement("a"); a.href=url; a.download=filename; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(url); toast("Downloaded "+filename);
  }catch(e){ toast(e.message,true); }
}
function addLicense(tenantId){
  const f=fields([
    {k:"edition",label:"Edition",type:"select",options:["demo","standard","custom","enterprise"],value:"standard"},
    {k:"term_days",label:"Term (days)",type:"number",value:365},
    {k:"max_devices",label:"Max devices",type:"number",value:25},
    {k:"max_admins",label:"Max admins",type:"number",value:3},
    {k:"is_demo",label:"Demo?",type:"select",options:[{v:"false",t:"No"},{v:"true",t:"Yes"}],value:"false"}]);
  modal("Create license",f,async()=>{
    const v=f._values();
    const pkg=await api(`/api/tenants/${tenantId}/licenses`,{method:"POST",body:{edition:v.edition,term_days:+v.term_days,max_devices:+v.max_devices,max_admins:+v.max_admins,is_demo:v.is_demo==="true"}});
    const body=el(`<div><p class="muted">Provide this license package to the customer for Server_Setup.exe activation.</p>
      <pre class="json">License ID:       ${esc(pkg.license_id)}\nActivation token: ${esc(pkg.activation_token)}\nCompany:          ${esc(pkg.company_name)}</pre></div>`);
    modal("License created",body,null);
  },"Create");
}
async function showLicenses(tenantId){
  const rows=await api(`/api/tenants/${tenantId}/licenses`);
  const body=el(`<div></div>`);
  body.appendChild(tableFrom(["Edition","Type","Status","Expiry","Devices","Key"],
    rows.map(l=>[esc(l.edition),l.license_type==="lifetime"?'<span class="badge b-ok">lifetime</span>':'<span class="pill">monthly</span>',
      statusBadge(l.status),l.license_type==="lifetime"?"—":fmtDate(l.expiry_date),l.max_devices,
      `<button class="btn sm ghost" data-key="${l.id}">⬇ .lic</button>`])));
  body.querySelectorAll("[data-key]").forEach(b=>b.onclick=()=>downloadWithAuth(`/api/licenses/${b.dataset.key}/key`,"license.lic"));
  modal("Licenses",body,null,"Close");
}

VIEWS.audit = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Audit Logs"));
  const rows=await api("/api/audit"+(S.activeTenant&&S.role==="platform_super_admin"?`?tenant_id=${S.activeTenant}`:""));
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["When","Action","Actor","Target","Result","IP"],
    rows.map(a=>[fmtDate(a.ts),`<span class="pill">${esc(a.action)}</span>`,esc(a.actor||"system"),
      esc((a.target_type||"")+" "+(a.target_id||"").slice(0,8)),a.result==="success"?`<span class="badge b-ok">ok</span>`:`<span class="badge b-high">${esc(a.result)}</span>`,esc(a.source_ip||"—")])));
  main.appendChild(c);
};

VIEWS.settings = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Settings"));

  // ---- Software updates (Platform Super Admin) ----
  if(S.role==="platform_super_admin"){
    try{
      const info=await api("/api/system/info");
      const uc=el(`<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 6px">Software updates</h3></div>`);
      uc.appendChild(el(`<div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:10px">
        <div><div class="l" style="font-size:11px;color:var(--muted);text-transform:uppercase">Version</div><div style="font-size:22px;font-weight:700">v${esc(info.version_display||info.version)}</div></div>
        <div><div class="l" style="font-size:11px;color:var(--muted);text-transform:uppercase">Build</div><div style="font-size:15px;font-weight:600;padding-top:5px">${esc(info.commit||"—")}</div></div>
        <div><div class="l" style="font-size:11px;color:var(--muted);text-transform:uppercase">Last updated</div><div style="font-size:15px;font-weight:600;padding-top:5px">${info.updated_at?esc(fmtDate(info.updated_at)):"—"}</div></div>
        <div><div class="l" style="font-size:11px;color:var(--muted);text-transform:uppercase">Role</div><div style="font-size:15px;font-weight:600;padding-top:5px">${esc(info.role.replace("_"," "))}</div></div>
      </div>`));
      uc.appendChild(el(`<div class="muted" style="margin-bottom:10px">${info.role==="license_server"?"Updates from GitHub and distributes builds to client servers &amp; agents.":"Updates from the license server ("+esc(info.license_server||"not set")+")."}${info.previous_version?" · previously v"+esc(info.previous_version):""}</div>`));
      const status=el(`<div class="muted" style="margin:8px 0"></div>`);
      const bar=el(`<div class="toolbar"></div>`);
      if(info.role==="license_server"){
        const b=el(`<button class="btn">⬆ Update from GitHub &amp; restart</button>`);
        b.onclick=async()=>{ if(!confirm("Pull latest from GitHub and restart the license server?")) return;
          status.textContent="Pulling from GitHub…";
          try{ const r=await api("/api/system/update",{method:"POST",body:{pip_install:true,restart:true}});
            status.innerHTML=r.ok?`✓ ${esc(r.message)}`:`✗ Pull issues — see below`;
            status.appendChild(el(`<pre class="json" style="margin-top:8px">${esc(r.steps.map(s=>s.cmd+" (exit "+s.code+")\n"+(s.out||"").slice(-500)).join("\n\n"))}</pre>`));
          }catch(e){ status.textContent="✗ "+e.message; } };
        bar.appendChild(b);
      } else {
        const chk=el(`<button class="btn ghost">Check for updates</button>`);
        const apply=el(`<button class="btn" style="margin-left:8px">⬆ Update from license server</button>`);
        chk.onclick=async()=>{ status.textContent="Checking…";
          try{ const r=await api("/api/system/check-update");
            status.textContent=r.update_available?`Update available: ${r.latest} (current ${r.current})`:`Up to date (current ${r.current}${r.latest?", latest "+r.latest:""}).`;
          }catch(e){ status.textContent="✗ "+e.message; } };
        apply.onclick=async()=>{ if(!confirm("Download the latest build from the license server and restart this server?")) return;
          status.textContent="Downloading & applying…";
          try{ const r=await api("/api/system/apply-update",{method:"POST"});
            status.textContent=(r.ok?"✓ ":"✗ ")+(r.message||""); }catch(e){ status.textContent="✗ "+e.message; } };
        bar.append(chk,apply);
      }
      uc.appendChild(bar); uc.appendChild(status);
      if(!info.self_update_enabled) uc.appendChild(el(`<div class="muted" style="margin-top:8px">Self-update is disabled (EMP_ALLOW_SELF_UPDATE=false).</div>`));
      main.appendChild(uc);
    }catch(e){ /* system routes may be disabled */ }
  }

  // ---- License sync (client servers: pull credential resets + entitlements from cloud) ----
  if(["platform_super_admin","customer_owner"].includes(S.role)){
    try{
      const m=await api("/api/meta");
      if(m.license_server){
        const sc=el(`<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 6px">License server sync</h3>
          <div class="muted" style="margin-bottom:10px">This client server syncs company-admin password resets and entitlement changes from <b>${esc(m.license_server)}</b> (automatically every 10 min). If your provider reset your admin password, sync to apply it now.</div></div>`);
        const sb=el(`<button class="btn">Sync from license server now</button>`);
        const out=el(`<span class="muted" style="margin-left:10px"></span>`);
        sb.onclick=async()=>{ out.textContent="Syncing…"; try{ const r=await api("/api/license/sync-now",{method:"POST"}); out.textContent=r.synced?("✓ "+r.detail):("✗ "+r.detail); }catch(e){ out.textContent="✗ "+e.message; } };
        sc.append(sb,out); main.appendChild(sc);
      }
    }catch(e){}
  }

  // ---- Email (SMTP) setup — sysadmin: global; company owner: their tenant ----
  if(["platform_super_admin","customer_owner"].includes(S.role)){
    const scope = S.role==="platform_super_admin" ? "global" : "";
    const e=await api("/api/settings/email"+(scope?`?scope=${scope}`:""));
    const ec=el(`<div class="card" style="margin-bottom:16px"></div>`);
    ec.appendChild(el(`<h3 style="margin:0 0 4px">Email setup (SMTP)</h3>
      <div class="muted" style="margin-bottom:10px">${S.role==="platform_super_admin"?"Platform-wide — used to email license keys to customers.":"Your company's outgoing mail for notifications."} Current: <b>${e.configured?"configured ("+esc(e.host||"")+")":"not configured — emails go to server outbox"}</b></div>`));
    const ef=fields([
      {k:"host",label:"SMTP host",value:e.host||""},
      {k:"port",label:"Port",type:"number",value:e.port||587},
      {k:"user",label:"Username",value:e.user||""},
      {k:"password",label:"Password (blank = keep current)",type:"password",value:""},
      {k:"from_addr",label:"From address",value:e.from_addr||""},
      {k:"from_name",label:"From name",value:e.from_name||""},
      {k:"use_tls",label:"Use STARTTLS",type:"select",options:[{v:"true",t:"Yes"},{v:"false",t:"No"}],value:String(e.use_tls)},
    ]);
    ec.appendChild(ef);
    const result=el(`<div id="emailResult" style="margin-top:12px"></div>`);
    const showResult=(okMsg,errMsg)=>{ result.innerHTML=""; result.appendChild(el(
      errMsg?`<div class="notice" style="background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.5);color:#fca5a5">✗ ${esc(errMsg)}</div>`
            :`<div class="notice" style="background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.5);color:#86efac">✓ ${esc(okMsg)}</div>`)); };
    const save=el(`<button class="btn">Save &amp; verify</button>`);
    save.onclick=async()=>{ try{ const v=ef._values(); v.use_tls=(v.use_tls==="true"); if(v.password==="") delete v.password;
      const r=await api("/api/settings/email"+(scope?`?scope=${scope}`:""),{method:"PUT",body:v});
      const vr=r.verify||{};
      if(vr.ok){ showResult("Settings saved and SMTP login verified. Email delivery is working.",null); toast("Saved — SMTP verified ✓"); }
      else { showResult(null, "Settings saved, but SMTP check failed: "+(vr.error||"unknown error")+"  (emails will fall back to the server outbox until this is fixed)"); toast("Saved, but SMTP failed",true); }
    }catch(err){ showResult(null, err.message); toast(err.message,true); } };
    const test=el(`<button class="btn ghost" style="margin-left:8px">Send test email</button>`);
    test.onclick=async()=>{ const to=prompt("Send test email to:", e.from_addr||""); if(!to) return;
      try{ const r=await api("/api/settings/email/test"+(scope?`?scope=${scope}`:""),{method:"POST",body:{to}});
        if(r.delivered){ showResult("Test email sent to "+to+" via SMTP.",null); toast("Email sent ✓"); }
        else { showResult(null, "Email NOT sent — "+(r.detail||r.via)+". It was saved to the server outbox instead."); toast("Email not sent",true); }
      }catch(err){ showResult(null, err.message); toast(err.message,true); } };
    const bar=el(`<div style="margin-top:12px"></div>`); bar.append(save,test); ec.appendChild(bar); ec.appendChild(result);
    main.appendChild(ec);
  }

  // ---- Monitoring & privacy policy ----
  if(["customer_owner","security_admin"].includes(S.role)){
    const m=await api("/api/settings/monitoring").catch(()=>null);
    if(m){
      const mc=el(`<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 6px">Monitoring &amp; privacy policy</h3>
        <div class="muted" style="margin-bottom:10px">Controls whether admins may view employee screens without notifying the employee. These devices are company-owned; you are responsible for ensuring this complies with your local law and employment policy. All admin access is recorded in the audit log.</div></div>`);
      const row=el(`<label style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><input type="checkbox" id="silentAcc"/> Allow silent screen access (no employee notification)</label>`);
      row.querySelector("#silentAcc").checked=!!m.silent_access;
      const cm=fields([{k:"default_consent_mode",label:"Default consent mode for remote sessions",type:"select",options:["silent","notify","consent"],value:m.default_consent_mode||"silent"}]);
      mc.append(row,cm);
      const sv=el(`<button class="btn">Save monitoring policy</button>`);
      sv.onclick=async()=>{ try{ await api("/api/settings/monitoring",{method:"PUT",body:{silent_access:row.querySelector("#silentAcc").checked, default_consent_mode:cm._values().default_consent_mode}}); toast("Monitoring policy saved"); }catch(e){toast(e.message,true);} };
      mc.appendChild(sv);
      main.appendChild(mc);
    }
  }

  // ---- License (company owner) ----
  if(S.role==="customer_owner"){
    const ls=await api("/api/license/status").catch(()=>null);
    if(ls){ const lc=el(`<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 6px">License</h3>
      <div class="muted">Status: <b>${esc(ls.label)}</b> · ${ls.edition?esc(ls.edition)+" · ":""}${ls.max_devices?ls.max_devices+" devices · ":""}${ls.expiry?"expires "+fmtDate(ls.expiry):""}</div></div>`);
      if(!ls.usable){ const a=el(`<button class="btn warn" style="margin-top:10px">Activate license</button>`); a.onclick=()=>location.hash="activate"; lc.appendChild(a); }
      main.appendChild(lc);
    }
  }

  // ---- Change password ----
  const meta=await api("/api/meta");
  const c=el(`<div class="card"></div>`);
  c.appendChild(el(`<h3 style="margin:0 0 10px">Change password</h3>`));
  const f=fields([{k:"current_password",label:"Current password",type:"password"},{k:"new_password",label:"New password",type:"password"}]);
  c.appendChild(f);
  const b=el(`<button class="btn">Update password</button>`);
  b.onclick=async()=>{ try{ await api("/api/auth/change-password",{method:"POST",body:f._values()}); toast("Password changed"); }catch(e){toast(e.message,true);} };
  c.appendChild(b);
  c.appendChild(el(`<div style="margin-top:18px" class="muted">Server ${esc(meta.version)} · heartbeat ${meta.heartbeat_interval}s · <a href="/docs" target="_blank">API docs</a></div>`));
  main.appendChild(c);
};

/* ---------------- shared actions ---------------- */
async function imgBlobURL(path){
  const res=await fetch(API+path,{headers:{Authorization:"Bearer "+S.token}});
  if(!res.ok) throw new Error("Image load failed ("+res.status+")");
  return URL.createObjectURL(await res.blob());
}

// Silent live-ish screen view: requests screenshots and shows the newest frame. The employee
// is NOT notified (company-owned device, per tenant monitoring policy). Every capture/view is
// recorded in the audit log against the signed-in admin.
function silentView(deviceId, hostname){
  let stop=false, lastSeen=null;
  const body=el(`<div>
    <div class="notice">Silent view — the employee receives no notification. This session is audited. Frames arrive as the agent captures them (up to one heartbeat of latency).</div>
    <div id="frameWrap" style="background:#000;border-radius:10px;min-height:260px;display:grid;place-items:center">
      <span class="muted" id="frameStatus">Requesting first frame…</span>
    </div>
    <div class="toolbar" style="margin-top:10px">
      <label class="muted" style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="autoCap" checked/> Auto-capture every 10s</label>
      <button class="btn sm right" id="capNow">Capture frame now</button>
    </div></div>`);
  const bg=modal(`Screen — ${esc(hostname||deviceId.slice(0,8))}`, body, null, "Close");
  // stop polling when the modal closes
  const mo=new MutationObserver(()=>{ if(!document.body.contains(bg)){ stop=true; mo.disconnect(); } });
  mo.observe(document.body,{childList:true});

  const statusEl=()=>body.querySelector("#frameStatus");
  const wrap=body.querySelector("#frameWrap");
  async function requestFrame(){ try{ await api("/api/screenshots/request",{method:"POST",body:{device_id:deviceId,reason:"manual"}}); }catch(e){ if(statusEl())statusEl().textContent=e.message; } }
  async function poll(){
    if(stop) return;
    try{
      const ev=await api(`/api/screenshots?device_id=${deviceId}`);
      if(ev.length){
        const newest=ev[0];
        if(newest.id!==lastSeen){
          lastSeen=newest.id;
          const url=await imgBlobURL(`/api/screenshots/${newest.id}/image`);
          wrap.innerHTML=""; const im=el(`<img style="max-width:100%;border-radius:10px"/>`); im.src=url; wrap.appendChild(im);
          wrap.appendChild(el(`<div class="muted" style="margin-top:6px;font-size:12px">Captured ${fmtDate(newest.captured_at)} · ${esc(newest.reason)}</div>`));
        }
      } else if(statusEl()){ statusEl().textContent="Waiting for the agent to capture…"; }
    }catch(e){ if(statusEl())statusEl().textContent=e.message; }
    if(!stop) setTimeout(poll, 3000);
  }
  body.querySelector("#capNow").onclick=requestFrame;
  requestFrame(); poll();
  // periodic auto-capture
  (async function autoLoop(){
    while(!stop){ await new Promise(r=>setTimeout(r,10000)); if(stop)break; if(body.querySelector("#autoCap")?.checked) requestFrame(); }
  })();
}

function requestScreenshot(deviceId){
  const f=fields([{k:"reason",label:"Reason",type:"select",options:["manual","scheduled","interval","event","on_alert"],value:"manual"},
    {k:"scheduled_for",label:"Scheduled for (optional, ISO)",value:""}]);
  modal("Request screenshot",f,async()=>{ const v=f._values();
    await api("/api/screenshots/request",{method:"POST",body:{device_id:deviceId,reason:v.reason,scheduled_for:v.scheduled_for||null}});
    toast("Screenshot requested (agent captures on next heartbeat)"); },"Request");
}
function requestRemote(deviceId){
  const f=fields([{k:"consent_mode",label:"Consent mode",type:"select",options:["silent","notify","consent"],value:"silent"},
    {k:"duration_minutes",label:"Duration (min)",type:"number",value:30}]);
  modal("Request remote support",f,async()=>{ const v=f._values();
    const r=await api("/api/remote/request",{method:"POST",body:{device_id:deviceId,consent_mode:v.consent_mode,duration_minutes:+v.duration_minutes}});
    toast("Remote session requested: "+r.session_id.slice(0,8)); },"Request");
}
function tokenBtn(){
  const b=el(`<button class="btn sm ghost">Enrollment token</button>`);
  b.onclick=async()=>{
    const toks=await api("/api/enrollment-tokens"+qp());
    const body=el(`<div></div>`);
    const create=el(`<button class="btn sm" style="margin-bottom:12px">+ Generate token</button>`);
    create.onclick=async()=>{ const t=await api("/api/enrollment-tokens"+qp(),{method:"POST",body:{label:"console",ttl_hours:72,max_uses:0}}); toast("Token: "+t.token); b.onclick(); };
    body.appendChild(create);
    body.appendChild(tableFrom(["Token","Label","Expires","Uses","Revoked"],
      toks.map(t=>[`<code>${esc(t.token)}</code>`,esc(t.label),fmtDate(t.expires_at),t.uses,t.revoked?"yes":"no"])));
    modal("Enrollment tokens",body,null,"Close");
  };
  return b;
}

/* ---------------- table builders ---------------- */
function tableFrom(headers, rows){
  const t=el(`<table></table>`);
  const thead=el(`<thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join("")}</tr></thead>`);
  t.appendChild(thead);
  const tb=el(`<tbody></tbody>`);
  if(!rows.length) tb.appendChild(el(`<tr><td colspan="${headers.length}" class="muted" style="padding:20px;text-align:center">No data</td></tr>`));
  rows.forEach(r=>tb.appendChild(el(`<tr>${r.map(c=>`<td>${c==null?"—":c}</td>`).join("")}</tr>`)));
  t.appendChild(tb); return t;
}
function cardTable(title,headers,rows){ const c=el(`<div class="card"><h3 style="margin:0 0 10px">${esc(title)}</h3></div>`); c.appendChild(tableFrom(headers,rows)); return c; }

// ---- lightweight inline-SVG charts (no external libs) ----
function donutSVG(series){ // [[label,value,color],...]
  const total=series.reduce((s,x)=>s+x[1],0)||1;
  const r=52,cx=70,cy=70,circ=2*Math.PI*r; let off=0;
  const segs=series.filter(s=>s[1]>0).map(([lab,val,col])=>{
    const frac=val/total, dash=`${(frac*circ).toFixed(2)} ${(circ).toFixed(2)}`;
    const seg=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${col}" stroke-width="16" stroke-dasharray="${dash}" stroke-dashoffset="${(-off*circ).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})"><title>${esc(lab)}: ${val}</title></circle>`;
    off+=frac; return seg;
  }).join("");
  const legend=series.map(([lab,val,col])=>`<div style="display:flex;align-items:center;gap:6px;font-size:13px"><span style="width:10px;height:10px;border-radius:2px;background:${col};display:inline-block"></span>${esc(lab)} <b style="margin-left:auto">${val}</b></div>`).join("");
  const wrap=el(`<div style="display:flex;gap:18px;align-items:center"></div>`);
  wrap.appendChild(el(`<svg width="140" height="140" viewBox="0 0 140 140">${segs}<text x="70" y="76" text-anchor="middle" fill="#e8edf7" font-size="22" font-weight="700">${total}</text></svg>`));
  wrap.appendChild(el(`<div style="flex:1;display:flex;flex-direction:column;gap:6px">${legend}</div>`));
  return wrap;
}
function barsSVG(series){ // [[label,value,color],...]
  const max=Math.max(1,...series.map(s=>s[1])), W=340,H=160,pad=28,bw=(W-pad*2)/series.length;
  const bars=series.map(([lab,val,col],i)=>{
    const h=(val/max)*(H-pad*2), x=pad+i*bw+6, y=H-pad-h;
    return `<g><rect x="${x}" y="${y}" width="${bw-12}" height="${h}" rx="4" fill="${col}"><title>${esc(lab)}: ${val}</title></rect>`+
           `<text x="${x+(bw-12)/2}" y="${H-pad+14}" text-anchor="middle" fill="#93a2c4" font-size="11">${esc(lab)}</text>`+
           `<text x="${x+(bw-12)/2}" y="${y-5}" text-anchor="middle" fill="#e8edf7" font-size="11">${val||""}</text></g>`;
  }).join("");
  return el(`<svg width="100%" viewBox="0 0 ${W} ${H}"><line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="#263355"/>${bars}</svg>`);
}
function debounce(fn,ms){ let h; return (...a)=>{clearTimeout(h);h=setTimeout(()=>fn(...a),ms);}; }

/* ---------------- login ---------------- */
function renderLogin(){
  const app=document.getElementById("app");
  app.innerHTML="";
  const wrap=el(`<div class="login-wrap"></div>`);
  const card=el(`<div class="login-card">
    <div class="login-logo"><img src="/assets/logo.jpg" alt="Voyager"/></div>
    <h1>Voyager Endpoint Mgmt</h1><p class="sub">Sign in to the administration console</p>
    <div class="field"><label>Email</label><input id="email" type="email" value="owner@demo.local" /></div>
    <div class="field"><label>Password</label><input id="password" type="password" /></div>
    <div class="field"><label>Tenant ID (optional)</label><input id="tenant" placeholder="leave blank to auto-detect" /></div>
    <div class="field" id="mfaWrap" style="display:none"><label>MFA code</label><input id="mfa" /></div>
    <button class="btn" id="loginBtn" style="width:100%">Sign in</button>
    <div class="sub" style="margin-top:14px;display:flex;justify-content:space-between">
      <a href="#" id="forgotUser">Forgot username?</a>
      <a href="#" id="forgotPw">Forgot password?</a>
    </div>
    <p class="sub" style="margin-top:12px">First-run credentials are printed to the server console and <code>FIRST_RUN.txt</code>.</p>
  </div>`);
  wrap.appendChild(card); app.appendChild(wrap);
  const doLogin=async()=>{
    const body={email:card.querySelector("#email").value.trim(),password:card.querySelector("#password").value,
      tenant_id:card.querySelector("#tenant").value.trim()||null,mfa_code:card.querySelector("#mfa").value||null};
    try{ const d=await api("/api/auth/login",{method:"POST",body}); setSession(d); renderShell(); }
    catch(e){ if(/MFA/i.test(e.message)) card.querySelector("#mfaWrap").style.display="block"; toast(e.message,true); }
  };
  card.querySelector("#loginBtn").onclick=doLogin;
  card.querySelectorAll("input").forEach(i=>i.addEventListener("keydown",e=>{if(e.key==="Enter")doLogin();}));
  card.querySelector("#forgotUser").onclick=(e)=>{e.preventDefault();forgotFlow("username");};
  card.querySelector("#forgotPw").onclick=(e)=>{e.preventDefault();resetChooser();};
}

function resetChooser(){
  const body=el(`<div>
    <p class="muted" style="margin-bottom:12px">How do you want to reset your password?</p>
    <div style="display:flex;flex-direction:column;gap:8px">
      <button class="btn" id="rcEmail">✉ Email me a reset link</button>
      <button class="btn ghost" id="rcLocal">💾 Local reset (no email)</button>
      <button class="btn ghost" id="rcCode">I already have a reset code</button>
    </div></div>`);
  const bg=modal("Reset password", body, null, "Close");
  body.querySelector("#rcEmail").onclick=()=>{ bg.remove(); forgotFlow("password"); };
  body.querySelector("#rcCode").onclick=()=>{ bg.remove(); codeResetForm(); };
  body.querySelector("#rcLocal").onclick=()=>{ bg.remove(); localResetFlow(); };
}

function localResetFlow(){
  const f=fields([{k:"email",label:"Your account email (username)",type:"email"}]);
  modal("Local reset — write code on the server", f, async()=>{
    const email=(f._values().email||"").trim();
    if(!email) throw new Error("Enter an email");
    const r=await api("/api/auth/local-reset",{method:"POST",body:{email}});
    const out=el(`<div><div class="notice">${esc(r.message||"")}</div>
      <p class="muted">Open that file on the server, copy the <b>Reset code</b>, then continue to set a new password.</p></div>`);
    const b=el(`<button class="btn">I have the code → set new password</button>`);
    b.onclick=()=>{ document.querySelectorAll(".modal-bg").forEach(x=>x.remove()); codeResetForm(); };
    out.appendChild(b);
    modal("Reset code written on the server", out, null, "Close");
  }, "Create reset code");
}

function codeResetForm(){
  const f=fields([
    {k:"token",label:"Reset code (from email link or the server reset file)",type:"textarea"},
    {k:"new_password",label:"New password",type:"password"},
    {k:"confirm",label:"Confirm new password",type:"password"},
  ]);
  modal("Set a new password", f, async()=>{
    const v=f._values();
    if((v.new_password||"")!==(v.confirm||"")) throw new Error("Passwords do not match");
    await api("/api/auth/reset-password",{method:"POST",body:{token:(v.token||"").trim(),new_password:v.new_password}});
    toast("Password updated — please sign in");
  }, "Update password");
}

function forgotFlow(kind){
  const isUser = kind==="username";
  const f=fields([{k:"email",label:isUser?"Your company's registered email":"Your account email (username)",type:"email"}]);
  modal(isUser?"Forgot username":"Forgot password", f, async()=>{
    const email=(f._values().email||"").trim();
    if(!email) throw new Error("Enter an email");
    const path=isUser?"/api/auth/forgot-username":"/api/auth/forgot-password";
    const r=await api(path,{method:"POST",body:{email}});
    toast(r.message||"If a matching account exists, an email has been sent.");
  }, "Send email");
}

function renderReset(token){
  const app=document.getElementById("app");
  app.innerHTML="";
  const wrap=el(`<div class="login-wrap"></div>`);
  const card=el(`<div class="login-card">
    <h1>Set a new password</h1><p class="sub">Choose a new password for your account.</p>
    <div class="field"><label>New password</label><input id="np" type="password" /></div>
    <div class="field"><label>Confirm password</label><input id="cp" type="password" /></div>
    <button class="btn" id="go" style="width:100%">Update password</button>
    <p class="sub" style="margin-top:14px"><a href="#" id="back">Back to sign in</a></p>
  </div>`);
  wrap.appendChild(card); app.appendChild(wrap);
  card.querySelector("#back").onclick=(e)=>{e.preventDefault();location.hash="";renderLogin();};
  card.querySelector("#go").onclick=async()=>{
    const np=card.querySelector("#np").value, cp=card.querySelector("#cp").value;
    if(np!==cp){ toast("Passwords do not match",true); return; }
    try{
      await api("/api/auth/reset-password",{method:"POST",body:{token,new_password:np}});
      toast("Password updated — please sign in"); location.hash=""; renderLogin();
    }catch(e){ toast(e.message,true); }
  };
}

/* ---------------- boot ---------------- */
// Surface any unhandled error instead of leaving a blank screen.
window.addEventListener("error", (e)=>{
  const app=document.getElementById("app");
  if(app && !app.children.length){
    app.innerHTML = `<div style="color:#e8edf7;padding:40px;max-width:640px;margin:0 auto">
      <h2>Console failed to start</h2>
      <p class="muted">${esc(e.message||"Unknown error")}</p>
      <button class="btn" onclick="localStorage.clear();location.reload()">Clear session & reload</button></div>`;
  }
});

async function boot(){
  // Public password-reset link: /#reset/<token> — works with no active session.
  const h=location.hash.replace("#","");
  if(h.startsWith("reset/")){ renderReset(h.split("/")[1]); return; }
  if(!S.token){ renderLogin(); return; }
  // Validate the stored session; a stale token (e.g. after a DB reset) must fall back to login.
  try{
    await api("/api/auth/me");
    renderShell();
  }catch{
    ["emp_token","emp_refresh","emp_role","emp_tenant","emp_name","emp_active_tenant"].forEach(k=>localStorage.removeItem(k));
    Object.assign(S,{token:"",refresh:"",role:"",tenant:"",name:"",activeTenant:""});
    renderLogin();
  }
}
boot();
