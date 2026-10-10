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
    if (res.status === 404 && /^Not found: \/api\//.test(msg))
      msg = "This server does not support this page yet (" + path.split("?")[0] + "). It is probably still running an older version — restart the Management Server (or run UPDATE.bat again), then reload with Ctrl+F5.";
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("text/html") && path.startsWith("/api/"))
    throw new Error("The server returned a web page instead of data for " + path.split("?")[0] + ". It is probably still running an older version — restart the Management Server, then reload with Ctrl+F5.");
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
// The server stores and sends times in UTC; values without a zone marker are UTC, never local.
function parseTs(s){ if(!s) return null; if(s instanceof Date) return s; s=String(s);
  if(/^\d{4}-\d\d-\d\d[T ]\d\d:\d\d/.test(s) && !/([zZ]|[+-]\d\d:?\d\d)$/.test(s)) s=s.replace(" ","T")+"Z";
  const d=new Date(s); return isNaN(d)?null:d; }
function fmtDate(s){ const d=parseTs(s); return d?d.toLocaleString():"—"; }
function ago(s){ const t=parseTs(s); if(!t) return "never"; const d=Math.max(0,(Date.now()-t)/1000); if(d<60)return Math.floor(d)+"s ago"; if(d<3600)return Math.floor(d/60)+"m ago"; if(d<86400)return Math.floor(d/3600)+"h ago"; return Math.floor(d/86400)+"d ago"; }
function statusBadge(s){ const m={active:"b-ok",online:"b-ok",offline:"b-off",pending:"b-pending",revoked:"b-rev",inactive:"b-rev",suspended:"b-rev"}; return `<span class="badge ${m[s]||"b-off"}">${esc(s)}</span>`; }
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
  ["websites","Websites","🌐"],
  ["email","Email","✉"],
  ["tracking","Tracking (Login/Network/USB/Email)","◷"],
  ["policies","Policies","⚙"],
  ["alerts","Alerts","⚑"],
  ["evidence","Screenshots / Evidence","▧"],
  ["remote","Remote Support","⤢"],
  ["reports","Reports","▭"],
  ["downloads","Downloads","⬇"],
  ["repair","Agent Repair","🛠"],
  ["deploy","Domain Deploy","🖧"],
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

  const brandSub = isPlatform
    ? `<span class="pill" style="background:#0284c7;color:#fff;font-size:0.7rem;padding:2px 6px;">License Server Panel</span>`
    : `<span class="pill" style="background:#059669;color:#fff;font-size:0.7rem;padding:2px 6px;">Client Server Panel</span>`;

  side.appendChild(el(`<div class="brand"><div class="logo"><img src="/assets/logo.jpg" alt="Voyager"/></div><div>Voyager Inc<br>${brandSub}</div></div>`));
  const nav=el(`<div class="nav"></div>`);

  const items = isPlatform
    ? [["licenses","Licenses & Tenants","🔑"],["downloads","Server Downloads","⬇"],["audit","Audit Logs","❏"],["settings","Settings","⋯"]]
    : NAV.filter(([k])=>!["licenses"].includes(k));

  items.forEach(([k,label,icon])=>{ nav.appendChild(el(`<a href="#${k}" data-k="${k}"><span>${icon}</span>${esc(label)}</a>`)); });
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
  const need = S.role==="platform_super_admin" && !S.activeTenant && !["dashboard","licenses","audit","settings","downloads"].includes(k);
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

/* SMTP test popup: settings used, every step of the SMTP conversation with the server's
   reply, where it failed and how to fix it, plus "Copy details" for support. */
function showSmtpReport(d, title){
  const s=d.settings||{};
  const body=el(`<div style="max-width:760px"></div>`);
  body.appendChild(el(d.ok
    ?`<div class="notice" style="background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.5);color:#86efac">✓ <b>All checks passed.</b> Email delivery is working.</div>`
    :`<div class="notice" style="background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.5);color:#fca5a5">✗ <b>Failed at: ${esc(d.failed_step||"unknown step")}</b></div>`));
  if(!d.ok && d.hint) body.appendChild(el(`<div class="notice" style="margin-top:8px"><b>How to fix:</b> ${esc(d.hint)}</div>`));
  body.appendChild(el(`<h4 style="margin:14px 0 6px">Settings used</h4>`));
  body.appendChild(tableFrom(["Host","Port","Encryption","Username","Password","From","Test email to"],
    [[esc(s.host),esc(s.port),esc(s.encryption),esc(s.username),esc(s.password),esc(s.from),esc(s.send_to)]]));
  body.appendChild(el(`<h4 style="margin:14px 0 6px">Steps</h4>`));
  body.appendChild(tableFrom(["","Step","Result / server reply","Time"],
    (d.steps||[]).map(x=>[x.ok?'<span class="badge b-ok">✓</span>':'<span class="badge b-high">✗</span>',
      `<b>${esc(x.step)}</b>`,`<span style="white-space:pre-wrap;word-break:break-word">${esc(x.detail)}</span>`,`${x.ms} ms`])));
  const text=[`SMTP test: ${d.ok?"OK":"FAILED at "+d.failed_step}`,
    `Settings: host=${s.host} port=${s.port} encryption=${s.encryption} user=${s.username} password=${s.password} from=${s.from} to=${s.send_to}`,
    ...(d.steps||[]).map(x=>`${x.ok?"[OK]  ":"[FAIL]"} ${x.step}: ${x.detail} (${x.ms} ms)`),
    ...(d.hint?[`How to fix: ${d.hint}`]:[])].join("\n");
  const copy=el(`<button class="btn ghost" style="margin-top:12px">⧉ Copy details</button>`);
  copy.onclick=async()=>{ try{ await navigator.clipboard.writeText(text); toast("Details copied"); }
    catch{ const ta=el(`<textarea rows="8" style="width:100%;margin-top:8px"></textarea>`); ta.value=text; body.appendChild(ta); ta.select(); toast("Select and copy the text below"); } };
  body.appendChild(copy);
  modal(title,body,null,"Close");
}

/* .lic file picker: parses the license file and hands {license_id, license_key, company, branch, ...}
   to onLoad. Returns {node, parse} — parse(text) also accepts a whole .lic pasted as text. */
function licFilePicker(onLoad){
  const node=el(`<div>
    <div class="field"><label>Attach license file (.lic) — fills in License ID and License Key automatically</label>
      <input type="file" accept=".lic,.json,.txt" /></div>
    <div class="notice" style="display:none"></div></div>`);
  const preview=node.querySelector(".notice");
  const parse=(txt)=>{
    let d; try{ d=JSON.parse(String(txt).replace(/^﻿/,"")); }catch{ throw new Error("This is not a valid .lic file."); }
    if(!d.license_id||!d.license_key) throw new Error("The .lic file has no License ID / License Key.");
    preview.style.display="block";
    preview.innerHTML=`Company: <b>${esc(d.company||"—")}</b>${d.branch?` · Branch: <b>${esc(d.branch)}</b>`:""}`+
      ` · Devices: ${esc(d.max_devices??"—")} · Expires: ${d.expiry?esc(fmtDate(d.expiry)):"—"}`;
    onLoad(d); return d;
  };
  node.querySelector("input[type=file]").onchange=(e)=>{
    const file=e.target.files[0]; if(!file) return;
    const rd=new FileReader();
    rd.onload=()=>{ try{ parse(rd.result); toast("License file loaded"); }catch(err){ toast(err.message,true); } };
    rd.readAsText(file);
  };
  return {node, parse};
}
const cleanKey=s=>String(s||"").replace(/\s+/g,"").replace(/^["']+|["']+$/g,"");   // line breaks/quotes from copy-paste

VIEWS.activate = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Activate this server"));
  const ls=await api("/api/license/status").catch(()=>({}));
  if(ls.usable){ main.appendChild(el(`<div class="notice">✓ Your license is active (${esc(ls.label||"active")}). <a href="#dashboard">Go to dashboard</a>.</div>`)); return; }
  main.appendChild(el(`<div class="notice">Your license is <b>${esc(ls.label||"inactive")}</b>. Agents cannot enroll until you attach the license issued by your provider. Attach the <b>.lic file</b> (or enter the License ID and License Key from your welcome email).</div>`));
  const c=el(`<div class="card" style="max-width:560px"></div>`);
  const f=fields([
    {k:"license_id",label:"License ID",value:ls.license_id||""},
    {k:"license_key",label:"License Key",type:"textarea",value:""},
  ]);
  const ins=f.querySelectorAll("input,textarea");
  const pick=licFilePicker(d=>{ ins[0].value=d.license_id; ins[1].value=d.license_key; });
  c.appendChild(pick.node);
  c.appendChild(f);
  const b=el(`<button class="btn">Activate license</button>`);
  b.onclick=async()=>{ try{
    const v=f._values();
    if(v.license_key.trim().startsWith("{")) pick.parse(v.license_key);       // whole .lic pasted
    const w=f._values();
    await api("/api/license/activate-here",{method:"POST",body:{license_id:cleanKey(w.license_id),license_key:cleanKey(w.license_key)}});
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
  } else {
    const updBtn=el(`<button class="btn sm ghost" style="color:var(--accent,#38bdf8);border-color:var(--accent,#38bdf8);">⬆ Update Server</button>`);
    updBtn.onclick=()=>showClientUpdateModal();
    t.querySelector(".tools").appendChild(updBtn);
  }
  if(tools) tools.forEach(n=>t.querySelector(".tools").appendChild(n));
  return t;
}

async function showClientUpdateModal(){
  const body=el(`<div>
    <p class="muted" style="margin-bottom:12px">Check for server software updates from <b>http://vmgmt.voyager.co.in:8084</b> (Central License Server).</p>
    <div id="updStatus" class="notice">Click <b>Check for updates</b> to query http://vmgmt.voyager.co.in:8084.</div>
    <div style="display:flex;gap:10px;margin-top:14px;">
      <button class="btn ghost" id="chkBtn">🔍 Check for updates</button>
      <button class="btn" id="applyBtn" style="display:none;background:#0284c7;color:#fff;">⬆ Update from http://vmgmt.voyager.co.in:8084</button>
    </div>
  </div>`);

  modal("Client Management Server Update", body, null, "Close");
  const statusDiv = body.querySelector("#updStatus");
  const chkBtn = body.querySelector("#chkBtn");
  const applyBtn = body.querySelector("#applyBtn");

  chkBtn.onclick=async()=>{
    statusDiv.className="notice";
    statusDiv.textContent="Checking http://vmgmt.voyager.co.in:8084 for updates...";
    try {
      const r=await api("/api/system/check-update");
      if(r.update_available){
        statusDiv.className="notice b-high";
        statusDiv.innerHTML=`<b>Update Available!</b> Latest version: <b>${esc(r.latest)}</b> (Current: ${esc(r.current)}).<br>License Server: <code>${esc(r.license_server)}</code>`;
        applyBtn.style.display="inline-block";
      } else {
        statusDiv.innerHTML=`✓ Server is up to date (Version <b>${esc(r.current)}</b>).<br>License Server: <code>${esc(r.license_server)}</code>`;
        applyBtn.style.display="none";
      }
    } catch(e) {
      statusDiv.className="notice b-err";
      statusDiv.textContent="Could not reach license server: "+e.message;
    }
  };

  applyBtn.onclick=async()=>{
    if(!confirm("Download update from http://vmgmt.voyager.co.in:8084 and restart the server?")) return;
    statusDiv.className="notice";
    statusDiv.textContent="Downloading update from http://vmgmt.voyager.co.in:8084 and applying... Please wait ~20 seconds.";
    try {
      const r=await api("/api/system/apply-update", {method:"POST"});
      statusDiv.className = r.ok ? "notice b-ok" : "notice b-err";
      statusDiv.innerHTML = esc(r.message || "Update initiated.");
    } catch(e) {
      statusDiv.className="notice b-err";
      statusDiv.textContent="Update failed: "+e.message;
    }
  };
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
    host.appendChild(tableFrom(["Hostname","Current user","OS","Status","IP","Agent","Last seen","Actions"],
      rows.map(d=>[`<a href="#devices/${d.id}">${esc(d.hostname||"(unnamed)")}</a>`,
        esc(d.current_user||"—")+((d.logged_users||[]).length>1?`<div class="muted" style="font-size:11px">also: ${esc(d.logged_users.filter(u=>u!==d.current_user).join(", "))}</div>`:""),
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
    <button class="btn sm" id="viewBtn">👁 Live screen (silent)</button><button class="btn sm ghost" id="ssBtn">Request screenshot</button><button class="btn sm ghost" id="rsBtn">Remote support</button><button class="btn sm ghost" id="syncBtn">Force resync</button></div>`));
  main.appendChild(info);
  info.querySelector("#viewBtn").onclick=()=>silentView(id, dev.hostname);
  info.querySelector("#ssBtn").onclick=()=>requestScreenshot(id);
  info.querySelector("#rsBtn").onclick=()=>requestRemote(id);
  info.querySelector("#syncBtn").onclick=async()=>{ await api(`/api/devices/${id}/resync`,{method:"POST"}); toast("Resync queued"); };

  // ---- Per-agent data profile (what this device collects / shows) ----
  const prof=d.collection||{};
  const CATS=[["health","Resource health (CPU/RAM/disk/battery)"],["active_time","Active-time tracking"],
    ["software","Installed software inventory"],["activity","Web / application activity"],
    ["website","Website monitoring"],["email","Email monitoring (all Outlook sent mail: from/to/cc/bcc/subject/attachments)"],["file_events","File / DLP events"],
    ["usb","USB monitoring (drives + files copied to/from USB)"],["screenshots","Screenshots"]];
  const ROADMAP=new Set();   // every switch here has a working collector
  const pc=el(`<div class="card" style="margin-top:14px"><h3 style="margin:0 0 6px">Data collection profile</h3>
    <div class="muted" style="margin-bottom:10px">Choose what to collect from <b>${esc(dev.hostname)}</b>. Disabled categories are not gathered by the agent and won't appear here — applied on the agent's next sync. These switches add to the computer's <a href="#tracking/profiles">tracking profile</a>.</div></div>`);
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

VIEWS.assets = async (main, args) => {
  const tab=(args&&args[0])||"hardware";
  main.innerHTML="";
  const add=el(`<button class="btn sm">+ Asset</button>`);
  add.onclick=()=>{ const f=fields([
    {k:"asset_tag",label:"Asset tag"},
    {k:"category",label:"Category",type:"select",options:["computer","laptop","printer","network_device","software","peripheral"]},
    {k:"name",label:"Name"},{k:"serial_no",label:"Serial no"},{k:"model",label:"Model"},
    {k:"lifecycle",label:"Lifecycle",type:"select",options:["planned","in_stock","assigned","in_repair","retired","disposed"]},
    {k:"department",label:"Department"},{k:"location",label:"Location"}]);
    modal("Add asset",f,async()=>{ await api("/api/assets"+qp(),{method:"POST",body:f._values()}); toast("Asset added"); location.hash="assets/register"; route();});};
  main.appendChild(topbar("Assets",[add]));
  main.appendChild(el(`<div class="toolbar" style="margin-bottom:14px">
    <a class="btn sm ${tab==="hardware"?"":"ghost"}" href="#assets/hardware">Computer hardware</a>
    <a class="btn sm ${tab==="register"?"":"ghost"}" href="#assets/register">Asset register</a></div>`));
  if(tab==="register"){
    const rows=await api("/api/assets"+qp());
    const c=el(`<div class="card"></div>`);
    c.appendChild(tableFrom(["Tag","Category","Name","Serial","Lifecycle","Warranty"],
      rows.map(a=>[esc(a.asset_tag),esc(a.category),esc(a.name||"—"),esc(a.serial_no||"—"),`<span class="pill">${esc(a.lifecycle)}</span>`,a.warranty_expiry?fmtDate(a.warranty_expiry):"—"])));
    main.appendChild(c); return;
  }
  const rows=await api("/api/hardware"+qp());
  const exp=el(`<div class="toolbar" style="margin-bottom:10px;gap:10px"></div>`);
  exp.appendChild(exportButtons("hardware","Hardware report",()=>({})));
  main.appendChild(exp);
  const gbs=(v)=>v==null?"—":`${v} GB`;
  const c=el(`<div class="card"></div>`);
  c.appendChild(tableFrom(["Computer","Current user","Make / model","Serial","CPU","RAM","Disks","C: free","OS","Last boot",""],
    rows.map(d=>{ const h=d.hardware||{}; const disks=(h.disks||[]).map(x=>`${x.gb??"?"} GB ${x.type&&x.type!=="Unknown"?x.type:""}`.trim()).join(", ");
      const c0=(h.volumes||[]).find(v=>v.drive==="C:");
      return [`<b>${esc(d.hostname)}</b><div class="muted" style="font-size:11px">${statusBadge(d.status)} ${esc(d.ip_address||"")}</div>`,
        esc(d.current_user||"—"),esc([h.manufacturer,h.model].filter(Boolean).join(" ")||"—"),esc(h.serial||"—"),
        esc(h.cpu||"—")+(h.cores?`<div class="muted" style="font-size:11px">${h.cores} cores / ${h.threads||h.cores} threads</div>`:""),
        gbs(h.ram_gb),esc(disks||(h.disk_gb?h.disk_gb+" GB":"—")),
        c0?`${c0.free_gb} of ${c0.gb} GB`:"—",esc(h.os||"—"),esc(h.last_boot||"—"),
        `<button class="btn sm ghost" data-hw="${d.id}">Details</button>`]; })));
  c.querySelectorAll("[data-hw]").forEach(b=>b.onclick=()=>showHardware(rows.find(x=>x.id===b.dataset.hw)));
  main.appendChild(c);
  if(rows.length&&!rows.some(d=>(d.hardware||{}).serial)) main.appendChild(el(`<div class="notice" style="margin-top:10px">Full hardware details (model, serial, memory, disks, GPU…) arrive from agent 4.6 or newer — update agents in Settings → Agent updates.</div>`));
};
function showHardware(d){
  const h=d.hardware||{}, r=(k,v)=>`<tr><th style="text-align:left;width:150px">${k}</th><td>${v??"—"}</td></tr>`;
  const body=el(`<div style="max-width:780px"><table>
    ${r("Computer",esc(d.hostname))}${r("Current user",esc(d.current_user||"—"))}${r("Manufacturer / model",esc([h.manufacturer,h.model].filter(Boolean).join(" ")))}
    ${r("Serial number",esc(h.serial))}${r("BIOS",esc([h.bios,h.bios_date].filter(Boolean).join(" · ")))}${r("Motherboard",esc(h.board))}
    ${r("CPU",esc(h.cpu)+(h.cores?` · ${h.cores} cores / ${h.threads||h.cores} threads`:"")+(h.cpu_mhz?` · ${h.cpu_mhz} MHz`:""))}
    ${r("RAM",h.ram_gb!=null?h.ram_gb+" GB":"—")}${r("Operating system",esc([h.os,h.os_build?"build "+h.os_build:""].filter(Boolean).join(" · ")))}
    ${r("Windows installed",esc(h.os_installed))}${r("Last boot",esc(h.last_boot))}${r("Domain / workgroup",esc(h.domain))}
    ${r("Inventory collected",esc(h.collected_at))}</table></div>`);
  const sec=(t,hd,rows)=>{ body.appendChild(el(`<h4 style="margin:14px 0 6px">${t}</h4>`)); body.appendChild(tableFrom(hd,rows)); };
  sec("Memory modules",["Slot","Size","Speed","Maker","Part"],(h.memory_modules||[]).map(m=>[esc(m.slot),m.gb!=null?m.gb+" GB":"—",m.speed?m.speed+" MT/s":"—",esc(m.maker),esc(m.part)]));
  sec("Physical disks",["Model","Size","Type","Interface","Serial"],(h.disks||[]).map(x=>[esc(x.model),x.gb!=null?x.gb+" GB":"—",esc(x.type),esc(x.interface),esc(x.serial)]));
  sec("Drives",["Drive","Label","File system","Size","Free"],(h.volumes||[]).map(v=>[esc(v.drive),esc(v.label),esc(v.fs),v.gb!=null?v.gb+" GB":"—",v.free_gb!=null?v.free_gb+" GB":"—"]));
  sec("Graphics",["Adapter","Driver"],(h.gpus||[]).map(g=>[esc(g.name),esc(g.driver)]));
  sec("Network adapters",["Adapter","MAC","IP"],(h.network||[]).map(n=>[esc(n.name),esc(n.mac),esc(n.ip)]));
  modal(`Hardware — ${d.hostname}`,body,null,"Close");
}

VIEWS.software = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Software inventory"));
  const devs=await api("/api/tracking/devices"+qp()).catch(()=>[]);
  const bar=el(`<div class="toolbar"></div>`);
  const dev=el(`<select><option value="">All computers</option>${devs.map(d=>`<option value="${d.id}">${esc(d.hostname)}</option>`).join("")}</select>`);
  const search=el(`<input placeholder="Search software..." />`);
  const sel=el(`<select><option value="">All</option><option value="block">Blocklisted</option><option value="allow">Allowlisted</option><option value="unknown">Unknown</option></select>`);
  bar.append(dev,search,sel);
  bar.appendChild(exportButtons("software","Export",()=>({device_id:dev.value})));
  main.appendChild(bar);
  const c=el(`<div class="card"></div>`); main.appendChild(c);
  async function load(){
    const rows=await api("/api/software"+qp({device_id:dev.value,q:search.value,list_status:sel.value}));
    c.innerHTML=`<div class="muted" style="margin-bottom:8px">${rows.length} item(s)${dev.value?" on <b>"+esc(dev.selectedOptions[0].text)+"</b>":" on all computers"}</div>`;
    c.appendChild(tableFrom(["Computer","Name","Publisher","Version","Status","Action"],
      rows.map(s=>[esc(s.hostname||"—"),esc(s.name),esc(s.publisher||"—"),esc(s.version||"—"),
        s.list_status==="block"?`<span class="badge b-high">block</span>`:esc(s.list_status),
        `<button class="btn sm ghost" data-block="${s.id}">Block</button> <button class="btn sm ghost" data-allow="${s.id}">Allow</button>`])));
    c.querySelectorAll("[data-block]").forEach(b=>b.onclick=async()=>{await api(`/api/software/${b.dataset.block}/list-status?value=block`,{method:"POST"});toast("Blocklisted");load();});
    c.querySelectorAll("[data-allow]").forEach(b=>b.onclick=async()=>{await api(`/api/software/${b.dataset.allow}/list-status?value=allow`,{method:"POST"});toast("Allowlisted");load();});
  }
  search.oninput=debounce(load,300); sel.onchange=load; dev.onchange=load; load();
};

VIEWS.websites = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Websites — browsing by computer"));
  const devs=await api("/api/tracking/devices"+qp()).catch(()=>[]);
  let params={};
  const bar=filterBar(devs,(p)=>{ params=p; load(); });
  const exp=el(`<div class="toolbar" style="margin:-4px 0 14px;gap:14px;flex-wrap:wrap"></div>`);
  exp.append(exportButtons("web_usage","Websites by computer",()=>params),exportButtons("activity","All visits (detailed)",()=>params));
  const body=el(`<div></div>`); main.append(bar,exp,body);
  const load=async()=>{
    body.innerHTML=`<div class="muted">Loading…</div>`;
    const [s,u]=await Promise.all([api("/api/activity/summary"+qp(params)),api("/api/activity/usage"+qp({...params,by:"domain"}))]);
    body.innerHTML="";
    if(!u.length){ body.appendChild(el(`<div class="notice">No website visits in this period. Website history is collected when <b>Web activity</b> is on in the computer's <a href="#tracking/profiles">tracking profile</a> (or "Website monitoring" on the computer's page). Private/incognito windows never write history — use <b>Block private / incognito browsing</b> in the profile.</div>`)); return; }
    const k=el(`<div class="grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:14px"></div>`);
    [["Time on websites",fmtHM(s.totals.web_seconds)],["Visits",s.totals.web_visits],["Different websites",new Set(u.map(x=>x.name)).size]]
      .forEach(([l,v])=>k.appendChild(el(`<div class="card"><div class="muted" style="font-size:12px">${l}</div><div style="font-size:24px;font-weight:700">${v}</div></div>`)));
    body.appendChild(k);
    body.appendChild(cardTable("Top websites",["Website","Time","Visits","Computers"],s.top_sites.map(x=>[esc(x.name),fmtHM(x.seconds),x.events,x.computers])));
    body.appendChild(el(`<div style="height:14px"></div>`));
    body.appendChild(cardTable("Websites by computer",["Computer","User","Website","Time","Visits","First","Last"],
      u.map(x=>[esc(x.hostname),esc(x.current_user||"—"),esc(x.name),fmtHM(x.seconds),x.events,fmtDate(x.first),fmtDate(x.last)])));
    const ev=await api("/api/activity/events"+qp({...params,kind:"web",limit:300}));
    body.appendChild(el(`<div style="height:14px"></div>`));
    body.appendChild(cardTable(`Visits (${ev.total}${ev.total>ev.events.length?", newest "+ev.events.length:""})`,["When","Computer","User","Website","Page title","Address","Time"],
      ev.events.map(e=>[fmtDate(e.ts),esc(e.hostname),esc(e.user||"—"),esc(e.domain||"—"),esc((e.title||"").slice(0,90)),
        `<span class="muted" style="font-size:11px;word-break:break-all">${esc(e.url||"")}</span>`,fmtHM(e.seconds)])));
  };
  params=bar.values(); await load();
};

VIEWS.email = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Email — sent mail by computer"));
  const devs=await api("/api/tracking/devices"+qp()).catch(()=>[]);
  let params={};
  const bar=filterBar(devs,(p)=>{ params=p; load(); },{extra:`<div class="field" style="margin:0;min-width:200px"><label>Search (from, to, subject, file)</label><input data-mq placeholder="e.g. gmail.com"/></div>`});
  const exp=el(`<div class="toolbar" style="margin:-4px 0 14px;gap:10px"></div>`);
  const csv=el(`<button class="btn sm ghost">⬇ CSV (all details)</button>`);
  csv.onclick=()=>downloadWithAuth("/api/tracking/events.csv"+qp({...params,category:"email",q:bar.querySelector("[data-mq]").value.trim()}),"emails.csv");
  exp.append(csv,exportButtons("email","Email report",()=>params));
  const body=el(`<div></div>`); main.append(bar,exp,body);
  main.appendChild(el(`<div class="muted" style="margin-top:10px;font-size:12px">Outlook (desktop) sends are recorded with From, To, CC, BCC, subject and attachments. Webmail in a browser (Gmail, Outlook web…) only shows tracked files chosen for upload — recipients and subject are not visible to the agent.</div>`));
  const L=(v)=>Array.isArray(v)?v:(v?[v]:[]);
  const load=async()=>{
    body.innerHTML=`<div class="muted">Loading…</div>`;
    const r=await api("/api/tracking/events"+qp({...params,category:"email",q:bar.querySelector("[data-mq]").value.trim(),limit:500}));
    body.innerHTML="";
    if(!r.events.length){ body.appendChild(el(`<div class="notice">No emails in this period. Turn on <b>Track ALL emails</b> (or <b>Email files</b>) in the computer's <a href="#tracking/profiles">tracking profile</a>, or <b>Email monitoring</b> on the computer's page. Outlook must be the mail program on that PC.</div>`)); return; }
    const card=el(`<div class="card"><h3 style="margin:0 0 10px">${r.total} email(s)</h3></div>`);
    card.appendChild(tableFrom(["Sent","Computer","User","From","To","CC","BCC","Subject","Attachments",""],
      r.events.map(e=>{ const m=e.meta||{}, atts=L(m.attachments).filter(a=>!a.inline);
        const list=(v)=>L(v).map(x=>esc(x)).join("<br>")||"—";
        return [fmtDate(e.ts),esc(e.hostname),esc(e.user||"—"),esc(m.from||"—"),list(m.to),list(m.cc),list(m.bcc),
          e.event_type==="email_attached"?`<span class="muted">webmail upload (not confirmed)</span>`:esc(m.subject||"(no subject)"),
          atts.length?atts.map(a=>esc(a.name)+(L(m.tracked_attachments).includes(a.name)?' <span class="badge b-high">tracked</span>':"")).join("<br>"):(e.file_name?esc(e.file_name):"—"),
          `<button class="btn sm ghost" data-mail="${e.id}">View</button>`]; })));
    card.querySelectorAll("[data-mail]").forEach(b=>b.onclick=()=>showMail(r.events.find(x=>x.id===b.dataset.mail)));
    body.appendChild(card);
  };
  bar.querySelector("[data-mq]").addEventListener("keydown",e=>{ if(e.key==="Enter") bar.querySelector(".btn").click(); });
  params=bar.values(); await load();
};

VIEWS.repair = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Agent Repair"));
  const top=el(`<div class="grid" style="grid-template-columns:1fr 1fr;margin-bottom:14px">
    <div class="card"><h3 style="margin:0 0 6px">Repair utility</h3>
      <div class="muted" style="margin-bottom:10px">Download it, copy it to the computer with the problem and <b>double-click it as administrator</b>. It is set up for this server — no typing needed.</div>
      <button class="btn" data-dl>⬇ Download VoyagerAgent_Repair.exe</button>
      <div class="muted" style="margin-top:10px;font-size:12px">The result is shown on the computer and appears below within a minute.</div></div>
    <div class="card"><h3 style="margin:0 0 6px">What it checks and fixes</h3><ol style="margin:0;padding-left:18px;line-height:1.7">
      <li>Can this computer reach the Management Server (address, port 9084)?</li>
      <li>Stops stuck or old agent copies</li>
      <li>Registration: re-registers if the server lost/revoked it or the server address changed</li>
      <li>Reinstalls the agent: Program Files, auto-start for all users, update task, firewall rule, folder access</li>
      <li>Starts the agent and waits until it is <b>Connected</b></li></ol></div></div>`);
  top.querySelector("[data-dl]").onclick=()=>downloadWithAuth("/api/download/agent"+qp({mode:"repair"}),"VoyagerAgent_Repair.exe");
  main.appendChild(top);
  const r=await api("/api/agent-repair"+qp());
  const off=r.computers.filter(c=>c.status!=="active");
  main.appendChild(cardTable(`Computers — ${off.length} not connected`,["Computer","Status","Last check-in","Agent","Current user","IP"],
    r.computers.sort((a,b)=>(a.status==="active")-(b.status==="active")).map(c=>[`<b>${esc(c.hostname)}</b>`,statusBadge(c.status),
      c.last_seen?`${fmtDate(c.last_seen)}<div class="muted" style="font-size:11px">${ago(c.last_seen)}</div>`:"never",
      esc(c.agent_version||"—"),esc(c.current_user||"—"),esc(c.ip_address||"—")])));
  main.appendChild(el(`<div class="muted" style="margin:8px 0 16px;font-size:12px">"Offline" means the agent has not checked in for 5 minutes — the PC itself may still answer ping. Run the repair utility on that PC.</div>`));
  const rc=cardTable("Repair results",["When","Computer","Result","User","Agent",""],
    r.reports.map(x=>[fmtDate(x.ts),esc(x.hostname),x.ok?'<span class="badge b-ok">fixed / OK</span>':'<span class="badge b-high">needs attention</span>',
      esc(x.user||"—"),esc(x.agent_version||"—"),`<button class="btn sm ghost" data-rep="${x.id}">Steps</button>`]));
  rc.querySelectorAll("[data-rep]").forEach(b=>b.onclick=()=>{ const x=r.reports.find(y=>y.id===b.dataset.rep);
    modal(`Repair — ${x.hostname}`,tableFrom(["","Step","Result"],(x.steps||[]).map(s=>[s.ok?'<span class="badge b-ok">✓</span>':'<span class="badge b-high">✗</span>',esc(s.step),esc(s.detail)])),null,"Close"); });
  main.appendChild(rc);
};

VIEWS.deploy = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Domain Deploy"));
  const cap=await api("/api/deploy/capability").catch(()=>({supported:false,reason:"unavailable"}));
  if(!cap.supported){
    main.appendChild(el(`<div class="notice">${esc(cap.reason||"Domain deployment is not available on this server.")}</div>`));
    main.appendChild(el(`<div class="muted" style="margin-top:10px">Domain Deploy installs or repairs the agent on company PCs from here, with no visit to each PC. It runs from a <b>Windows client server</b> that is on the company network with a <b>domain administrator</b> account, and needs File &amp; Printer Sharing (admin shares) and the Remote Scheduled Tasks service reachable on the PCs.</div>`));
    return;
  }
  if(!cap.has_agent){ main.appendChild(el(`<div class="notice">Upload VoyagerAgent.exe first in <a href="#settings">Settings → Agent program</a>.</div>`)); return; }
  main.appendChild(el(`<div class="muted" style="margin-bottom:12px">Install or repair the agent on domain computers from here${cap.domain?` (domain <b>${esc(cap.domain)}</b>)`:""}. Needs a domain admin account; the password is used only for the job and never stored. Requires admin shares (TCP 445) and Remote Scheduled Tasks (RPC) reachable on the PCs.</div>`));
  const bar=el(`<div class="toolbar" style="margin-bottom:10px"><button class="btn sm" id="disc">🔍 Find domain computers</button>
    <input id="manual" placeholder="or type names: PC1, PC2, 192.168.1.20" style="flex:1;min-width:240px"/>
    <button class="btn sm ghost" id="addManual">Add typed</button></div>`);
  main.appendChild(bar);
  const listCard=el(`<div class="card" style="margin-bottom:14px"><div class="muted">Click “Find domain computers”, or type names above.</div></div>`);
  main.appendChild(listCard);
  let comps=[];
  const render=()=>{
    listCard.innerHTML="";
    if(!comps.length){ listCard.appendChild(el(`<div class="muted">No computers yet.</div>`)); return; }
    listCard.appendChild(el(`<div style="margin-bottom:8px"><label><input type="checkbox" id="selAll"/> Select all (${comps.length})</label>
      <span class="muted" style="margin-left:10px">Tip: untick PCs already up to date.</span></div>`));
    const tbl=tableFrom(["","Computer","Agent","Status","Last seen"],
      comps.map((c,i)=>[`<input type="checkbox" data-i="${i}" ${c.sel?"checked":""}/>`,esc(c.host),
        c.enrolled?esc(c.agent_version||"yes"):'<span class="muted">not installed</span>',
        c.status?statusBadge(c.status):"—",c.last_seen?ago(c.last_seen):"—"]));
    listCard.appendChild(tbl);
    listCard.querySelector("#selAll").onchange=(e)=>{ comps.forEach(c=>c.sel=e.target.checked); render(); };
    listCard.querySelectorAll("[data-i]").forEach(cb=>cb.onchange=()=>{ comps[+cb.dataset.i].sel=cb.checked; });
  };
  const merge=(hosts,meta)=>{ hosts.forEach(h=>{ if(!comps.some(c=>c.host.toLowerCase()===h.toLowerCase())) comps.push({host:h,sel:true,...(meta||{})}); }); render(); };
  bar.querySelector("#disc").onclick=async()=>{ bar.querySelector("#disc").textContent="Searching…";
    try{ const r=await api("/api/deploy/computers"); comps=r.computers.map(c=>({...c,sel:!c.enrolled||c.status!=="active"})); render();
      toast(`${r.discovered} computer(s) from Active Directory`); }catch(e){ toast(e.message,true); } finally{ bar.querySelector("#disc").textContent="🔍 Find domain computers"; } };
  bar.querySelector("#addManual").onclick=()=>{ const names=bar.querySelector("#manual").value.split(/[,;\s]+/).filter(Boolean); if(names.length){ merge(names); bar.querySelector("#manual").value=""; } };

  const act=el(`<div class="card"><h3 style="margin:0 0 10px">Run on selected computers</h3>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
      <div class="field" style="margin:0"><label>Action</label><select id="dAct"><option value="install">Install / update agent</option><option value="repair">Repair agent</option></select></div>
      <div class="field" style="margin:0"><label>Domain admin username</label><input id="dUser" placeholder="DOMAIN\\administrator" autocomplete="off"/></div>
      <div class="field" style="margin:0"><label>Password (used once, not stored)</label><input id="dPass" type="password" autocomplete="new-password"/></div>
      <button class="btn" id="dGo">Deploy to selected</button></div>
    <div id="dStatus" class="muted" style="margin-top:10px"></div></div>`);
  main.appendChild(act);
  const prog=el(`<div class="card" style="margin-top:14px;display:none"></div>`); main.appendChild(prog);
  act.querySelector("#dGo").onclick=async()=>{
    const hosts=comps.filter(c=>c.sel).map(c=>c.host);
    if(!hosts.length){ toast("Select at least one computer",true); return; }
    const u=act.querySelector("#dUser").value.trim(), p=act.querySelector("#dPass").value;
    if(!u||!p){ toast("Enter the domain admin username and password",true); return; }
    if(!confirm(`${act.querySelector("#dAct").value==="repair"?"Repair":"Install"} the agent on ${hosts.length} computer(s)?`)) return;
    act.querySelector("#dStatus").textContent="Starting…";
    try{ const r=await api("/api/deploy",{method:"POST",body:{hosts,action:act.querySelector("#dAct").value,admin_user:u,admin_password:p}});
      act.querySelector("#dPass").value=""; act.querySelector("#dStatus").textContent=`Job started for ${r.total} computer(s).`;
      pollJob(r.job_id,prog);
    }catch(e){ act.querySelector("#dStatus").textContent="✗ "+e.message; }
  };
  const jobs=await api("/api/deploy/jobs").catch(()=>[]);
  if(jobs.length){ const h=el(`<h3 style="margin:16px 0 8px">Recent deployments</h3>`); main.appendChild(h);
    main.appendChild(cardTable("",["When","Action","By","Done","OK","Failed","Status"],
      jobs.map(j=>[fmtDate(j.created_at),esc(j.action),esc(j.created_by||"—"),`${j.succeeded+j.failed}/${j.total}`,
        j.succeeded,j.failed?`<span class="badge b-high">${j.failed}</span>`:0,
        j.status==="running"?'<span class="badge b-pending">running</span>':'<span class="badge b-ok">done</span>']))); }
};
function pollJob(jobId, card){
  card.style.display="block";
  const tick=async()=>{
    try{ const j=await api(`/api/deploy/jobs/${jobId}`);
      card.innerHTML=`<h3 style="margin:0 0 8px">Deployment — ${j.succeeded+j.failed} of ${j.total} · ✓ ${j.succeeded} · ✗ ${j.failed} ${j.status==="running"?'<span class="badge b-pending">running…</span>':'<span class="badge b-ok">finished</span>'}</h3>`;
      card.appendChild(tableFrom(["Computer","Result","Detail"],
        (j.targets||[]).map(t=>[esc(t.host),t.status==="ok"?'<span class="badge b-ok">✓ started</span>':'<span class="badge b-high">✗ failed</span>',
          `<span style="white-space:pre-wrap">${esc(t.detail||"")}</span>`])));
      if(j.status==="running") setTimeout(tick,2000);
      else toast(`Deployment finished: ${j.succeeded} ok, ${j.failed} failed`);
    }catch(e){ card.innerHTML=`<div class="muted">${esc(e.message)}</div>`; }
  };
  tick();
}

/* ---------------- period + computer filter shared by Activity and Reports ---------------- */
const ymd=(d)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const fmtHM=(s)=>{ s=Math.round(s||0); const h=Math.floor(s/3600), m=Math.floor(s%3600/60); return h?`${h}h ${String(m).padStart(2,"0")}m`:(m?`${m}m`:`${s}s`); };
function filterBar(devs, onApply, opts={}){
  const st=Object.assign({device_id:"",period:"today",from:"",to:""}, S.actFilter||{});
  const f=el(`<div class="card" style="margin-bottom:14px"><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
    <div class="field" style="margin:0;min-width:200px"><label>Computer</label><select data-f="device_id"><option value="">All computers</option>
      ${devs.map(d=>`<option value="${d.id}" ${d.id===st.device_id?"selected":""}>${esc(d.hostname)}</option>`).join("")}</select></div>
    <div class="field" style="margin:0"><label>Period</label><select data-f="period">
      ${[["today","Today"],["yesterday","Yesterday"],["7","Last 7 days"],["30","Last 30 days"],["custom","Custom…"]].map(([v,t])=>`<option value="${v}" ${v===st.period?"selected":""}>${t}</option>`).join("")}</select></div>
    <div class="field" style="margin:0" data-c><label>From</label><input type="date" data-f="from" value="${st.from}"/></div>
    <div class="field" style="margin:0" data-c><label>To</label><input type="date" data-f="to" value="${st.to}"/></div>
    <button class="btn">Apply</button>${opts.extra||""}</div></div>`);
  const sel=(k)=>f.querySelector(`[data-f="${k}"]`);
  const showCustom=()=>f.querySelectorAll("[data-c]").forEach(x=>x.style.display=sel("period").value==="custom"?"":"none");
  showCustom(); sel("period").onchange=showCustom;
  const values=()=>{
    const p=sel("period").value, now=new Date(); let from, to;
    if(p==="today"){ from=to=ymd(now); }
    else if(p==="yesterday"){ const y=new Date(now); y.setDate(y.getDate()-1); from=to=ymd(y); }
    else if(p==="custom"){ from=sel("from").value; to=sel("to").value||sel("from").value; }
    else { const s=new Date(now); s.setDate(s.getDate()-(+p-1)); from=ymd(s); to=ymd(now); }
    S.actFilter={device_id:sel("device_id").value,period:p,from:sel("from").value,to:sel("to").value};
    return {device_id:sel("device_id").value,date_from:from,date_to:to,tz_offset:new Date().getTimezoneOffset()};
  };
  f.querySelector(".btn").onclick=()=>onApply(values());
  f.values=values; f.setDevice=(id)=>{ sel("device_id").value=id; onApply(values()); };
  return f;
}
function exportButtons(kind, label, params){
  const box=el(`<span style="display:inline-flex;gap:6px;align-items:center"><span class="muted" style="font-size:12px">${esc(label)}:</span></span>`);
  ["csv","xlsx","pdf"].forEach(fmt=>{ const b=el(`<button class="btn sm ghost">${fmt.toUpperCase()}</button>`);
    b.onclick=()=>downloadWithAuth(`/api/reports/${kind}`+qp({...params(),fmt}),`${kind}.${fmt}`); box.appendChild(b); });
  return box;
}

VIEWS.activity = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Web / Application Activity"));
  const devs=await api("/api/tracking/devices"+qp()).catch(()=>[]);
  let params={};
  const bar=filterBar(devs,(p)=>{ params=p; load(); });
  const exp=el(`<div class="toolbar" style="margin:-4px 0 14px;gap:14px;flex-wrap:wrap"></div>`);
  exp.append(exportButtons("computer_summary","Computer-wise summary",()=>params),
             exportButtons("activity","Detailed activity",()=>params),
             exportButtons("app_usage","Apps by computer",()=>params),
             exportButtons("web_usage","Websites by computer",()=>params));
  const body=el(`<div></div>`);
  main.append(bar,exp,body);
  const load=async()=>{
    body.innerHTML=`<div class="muted">Loading…</div>`;
    const r=await api("/api/activity/summary"+qp(params));
    body.innerHTML="";
    const one=params.device_id&&devs.find(d=>d.id===params.device_id);
    if(!r.computers.length){
      body.appendChild(el(`<div class="notice">No activity for ${one?`<b>${esc(one.hostname)}</b>`:"any computer"} in this period.
        Activity is collected only when <b>App activity</b> / <b>Web activity</b> is on in the computer's
        <a href="#tracking/profiles">tracking profile</a> (agent 4.4 or newer).</div>`)); return; }
    const t=r.totals;
    const k=el(`<div class="grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:14px"></div>`);
    [["Application time",fmtHM(t.app_seconds),"#60a5fa"],["Website time",fmtHM(t.web_seconds),"#34d399"],
     ["Idle time",fmtHM(t.idle_seconds),"#94a3b8"],["Website visits",t.web_visits,"#f472b6"],
     [one?"Computer":"Active computers",one?esc(one.hostname):r.computers.length,"#f59e0b"]]
      .forEach(([l,v,c])=>k.appendChild(el(`<div class="card" style="border-left:4px solid ${c}"><div class="muted" style="font-size:12px">${l}</div><div style="font-size:24px;font-weight:700">${v}</div></div>`)));
    body.appendChild(k);
    if(!one){
      const ct=cardTable("By computer",["Computer","Current user","Employee","App time","Web time","Idle","Top application","Top website","Visits","Last activity",""],
        r.computers.map(c=>[`<b>${esc(c.hostname)}</b>`,esc(c.current_user||"—"),esc(c.employee||"—"),fmtHM(c.app_seconds),fmtHM(c.web_seconds),fmtHM(c.idle_seconds),
          esc(c.top_app||"—"),esc(c.top_site||"—"),c.web_visits,fmtUtc(c.last),`<button class="btn sm ghost" data-dev="${c.device_id}">Details</button>`]));
      ct.querySelectorAll("[data-dev]").forEach(b=>b.onclick=()=>bar.setDevice(b.dataset.dev));
      body.appendChild(ct);
    }
    const bars=(rows)=>{ const max=Math.max(1,...rows.map(x=>x.seconds));
      return rows.map(x=>[esc(x.name),`<div style="display:flex;align-items:center;gap:8px"><div style="height:8px;border-radius:4px;background:#60a5fa;width:${Math.max(2,Math.round(x.seconds/max*140))}px"></div>${fmtHM(x.seconds)}</div>`,x.events,one?"":x.computers]); };
    const g=el(`<div class="grid" style="grid-template-columns:1fr 1fr;margin:14px 0"></div>`);
    g.appendChild(cardTable(`Top applications${one?" — "+one.hostname:""}`,["Application","Time","Events",one?"":"Computers"],bars(r.top_apps)));
    g.appendChild(cardTable(`Top websites${one?" — "+one.hostname:""}`,["Website","Time","Visits",one?"":"Computers"],bars(r.top_sites)));
    body.appendChild(g);
    // detailed events with type filter, search and paging
    const det=el(`<div class="card"><div style="display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin-bottom:10px">
      <h3 style="margin:0;flex:1">Detailed activity${one?" — "+esc(one.hostname):""}</h3>
      <select data-k><option value="">All</option><option value="app">Applications</option><option value="web">Websites</option><option value="idle">Idle</option></select>
      <input data-q placeholder="Search app, website, title" style="min-width:220px"/><button class="btn sm">Search</button></div><div data-list></div>
      <button class="btn sm ghost" data-more style="margin-top:10px;display:none">Load more</button></div>`);
    body.appendChild(det);
    let offset=0;
    const list=det.querySelector("[data-list]"), more=det.querySelector("[data-more]");
    const loadEv=async(reset)=>{
      if(reset){ offset=0; list.innerHTML=""; }
      const ev=await api("/api/activity/events"+qp({...params,kind:det.querySelector("[data-k]").value,q:det.querySelector("[data-q]").value.trim(),limit:200,offset}));
      if(reset){ list.innerHTML=`<div class="muted" style="margin-bottom:6px">${ev.total} event(s)</div>`;
        list.appendChild(tableFrom(["When","Computer","User","Type","Application / Website","Title","Duration"],[])); }
      const tb=list.querySelector("tbody");
      if(reset && ev.events.length) tb.innerHTML="";          // drop the "No data" placeholder
      ev.events.forEach(e=>{ const tr=document.createElement("tr");
        tr.innerHTML=`<td>${fmtUtc(e.ts)}</td><td>${esc(e.hostname)}</td><td>${esc(e.user||"—")}</td><td>${e.private?'<span class="badge b-high" title="Private / InPrivate window">🔒 private</span> ':""}${e.type==="web"?'<span class="badge b-ok">web</span>':e.type==="idle"?'<span class="badge b-off">idle</span>':'<span class="badge b-pending">app</span>'}</td>
          <td>${esc(e.domain||e.app||"—")}${e.url?`<div class="muted" style="font-size:11px;word-break:break-all">${esc(e.url)}</div>`:""}</td><td>${esc((e.title||"").slice(0,120))}</td><td>${fmtHM(e.seconds)}</td>`;
        tb.appendChild(tr); });
      offset+=ev.events.length; more.style.display=offset<ev.total?"":"none";
    };
    det.querySelector(".btn.sm:not([data-more])").onclick=()=>loadEv(true).catch(e=>toast(e.message,true));
    det.querySelector("[data-q]").addEventListener("keydown",e=>{ if(e.key==="Enter") loadEv(true); });
    det.querySelector("[data-k]").onchange=()=>loadEv(true);
    more.onclick=()=>loadEv(false).catch(e=>toast(e.message,true));
    await loadEv(true);
  };
  params=bar.values();
  await load();
};

const fmtDur=(s)=>{ s=s||0; if(s<60)return s+"s"; if(s<3600)return Math.floor(s/60)+"m"; return (s/3600).toFixed(1)+"h"; };

/* ---------------- Tracking: logins, network/Wi-Fi, USB and email file events + profiles ---------------- */
const TRK_CAT={login:["Logins","#60a5fa"],network:["Network / Wi-Fi","#34d399"],usb:["USB files","#f59e0b"],email:["Email files","#f472b6"]};
const TRK_EVT={logon:"Logged on",logoff:"Logged off",lock:"Locked",unlock:"Unlocked",startup:"Startup",shutdown:"Shutdown",
  shutdown_initiated:"Shutdown requested",unexpected_shutdown:"Unexpected shutdown",sleep:"Sleep",wake:"Wake",
  adapter_up:"Adapter connected",adapter_down:"Adapter disconnected",ip_changed:"IP changed",internet_lost:"Internet lost",
  internet_restored:"Internet restored",status:"Network status",wifi_connected:"Wi-Fi connected",wifi_changed:"Wi-Fi changed",
  wifi_disconnected:"Wi-Fi disconnected",usb_inserted:"USB inserted",usb_removed:"USB removed",copied_to_usb:"Copied TO USB",
  copied_from_usb:"Copied FROM USB",email_sent:"Email sent (Outlook)",email_attached:"Attached in webmail"};
const fmtUtc=fmtDate;   // same rule everywhere: server times are UTC, shown in the viewer's local time
const fmtSize=(b)=>b==null?"":b<1024?b+" B":b<1048576?(b/1024).toFixed(1)+" KB":(b/1048576).toFixed(1)+" MB";
const TRK_SYNC=[[60,"1 minute"],[120,"2 minutes"],[300,"5 minutes"],[600,"10 minutes"],[900,"15 minutes"],[1800,"30 minutes"],[3600,"1 hour"]];

VIEWS.tracking = async (main, args) => {
  const tab=(args&&args[0])||"events";
  main.innerHTML="";
  const tabs=el(`<div class="toolbar" style="margin-bottom:14px">
    <a class="btn sm ${tab==="events"?"":"ghost"}" href="#tracking/events">Events</a>
    <a class="btn sm ${tab==="profiles"?"":"ghost"}" href="#tracking/profiles">Profiles &amp; computers</a></div>`);
  main.appendChild(topbar("Tracking — logins, network, USB & email"));
  main.appendChild(tabs);
  if(tab==="profiles") return trackingProfiles(main);
  return trackingEvents(main);
};

async function trackingEvents(main){
  const devs=await api("/api/tracking/devices"+qp()).catch(()=>[]);
  const f=el(`<div class="card" style="margin-bottom:14px"><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
    <div class="field" style="margin:0"><label>Category</label><select id="tc"><option value="">All</option>${Object.entries(TRK_CAT).map(([k,v])=>`<option value="${k}">${v[0]}</option>`).join("")}</select></div>
    <div class="field" style="margin:0"><label>Computer</label><select id="td"><option value="">All computers</option>${devs.map(d=>`<option value="${d.id}">${esc(d.hostname)}</option>`).join("")}</select></div>
    <div class="field" style="margin:0"><label>From</label><input id="tf" type="date"/></div>
    <div class="field" style="margin:0"><label>To</label><input id="tt" type="date"/></div>
    <div class="field" style="margin:0;flex:1;min-width:180px"><label>Search (file, user, USB, recipient, Wi-Fi)</label><input id="tq" placeholder="e.g. salary.xlsx"/></div>
    <button class="btn" id="tgo">Apply</button><button class="btn ghost" id="tcsv">⬇ CSV</button></div></div>`);
  const kpi=el(`<div class="grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px"></div>`);
  const list=el(`<div class="card"></div>`);
  main.append(kpi,f,list);
  const params=()=>({category:f.querySelector("#tc").value,device_id:f.querySelector("#td").value,
    date_from:f.querySelector("#tf").value,date_to:f.querySelector("#tt").value,q:f.querySelector("#tq").value.trim()});
  const load=async()=>{
    list.innerHTML=`<div class="muted">Loading…</div>`;
    const r=await api("/api/tracking/events"+qp({...params(),limit:500}));
    kpi.innerHTML="";
    Object.entries(TRK_CAT).forEach(([k,[label,color]])=>kpi.appendChild(el(`<div class="card" style="border-left:4px solid ${color}">
      <div class="muted" style="font-size:12px">${esc(label)} · last 24 h</div><div style="font-size:26px;font-weight:700">${r.last24h[k]||0}</div></div>`)));
    list._events=r.events;
    list.innerHTML=`<h3 style="margin:0 0 10px">${r.total} event${r.total===1?"":"s"}${r.total>r.events.length?` (showing newest ${r.events.length})`:""}</h3>`;
    if(!r.events.length){ list.appendChild(el(`<p class="muted">No events yet. Events arrive from each computer every sync interval set in its tracking profile (see <a href="#tracking/profiles">Profiles</a>).</p>`)); return; }
    list.appendChild(tableFrom(["When","Computer","User","Event","Details","File","Target"],
      r.events.map(e=>{ const c=TRK_CAT[e.category]||[e.category,"#94a3b8"];
        const mailBtn=(e.category==="email"&&e.meta&&(e.meta.to||e.meta.subject))?` <button class="btn sm ghost" data-mail="${e.id}">View</button>`:"";
        return [fmtUtc(e.ts)+mailBtn,esc(e.hostname),esc(e.user||"—"),
          `<span class="badge" style="background:${c[1]}22;color:${c[1]};border:1px solid ${c[1]}66">${esc(TRK_EVT[e.event_type]||e.event_type)}</span>`,
          `<span style="white-space:pre-wrap">${esc(e.detail||"")}</span>`,
          e.file_name?`${esc(e.file_name)}<div class="muted" style="font-size:11px">${fmtSize(e.file_size)}</div>`:"",
          esc(e.target||"")]; })));
  };
  list.addEventListener("click",(ev)=>{ const b=ev.target.closest("[data-mail]"); if(!b) return;
    const e=(list._events||[]).find(x=>x.id===b.dataset.mail); if(e) showMail(e); });
  f.querySelector("#tgo").onclick=()=>load().catch(e=>toast(e.message,true));
  f.querySelector("#tq").addEventListener("keydown",e=>{ if(e.key==="Enter") f.querySelector("#tgo").click(); });
  f.querySelector("#tcsv").onclick=()=>downloadWithAuth("/api/tracking/events.csv"+qp(params()),"tracking_events.csv");
  await load();
}

function showMail(e){
  const m=e.meta||{}, L=(v)=>Array.isArray(v)?v:(v?[v]:[]);
  const row=(k,v)=>`<tr><th style="text-align:left;width:120px;vertical-align:top">${k}</th><td style="word-break:break-word">${v}</td></tr>`;
  const people=(v)=>L(v).length?L(v).map(x=>esc(x)).join("<br>"):'<span class="muted">—</span>';
  const atts=L(m.attachments);
  const body=el(`<div style="max-width:720px"><table>
    ${row("Sent",esc(fmtUtc(e.ts)))}${row("Computer",esc(e.hostname)+" · "+esc(e.user||""))}
    ${row("From",esc(m.from||"—")+(m.from_name?` <span class="muted">(${esc(m.from_name)})</span>`:""))}
    ${row("To",people(m.to))}${row("CC",people(m.cc))}${row("BCC",people(m.bcc))}
    ${row("Subject",esc(m.subject||"(no subject)"))}${m.account?row("Mailbox",esc(m.account)):""}
    ${e.event_type==="email_attached"?row("Note","Attached in webmail — sending not confirmed; recipients are not visible to the agent."):""}
  </table>
  <h4 style="margin:14px 0 6px">Attachments (${atts.filter(a=>!a.inline).length})</h4></div>`);
  body.appendChild(tableFrom(["File","Size","Tracked type"],
    atts.map(a=>[esc(a.name)+(a.inline?' <span class="muted" style="font-size:11px">(inline image)</span>':""),fmtSize(a.size),
      L(m.tracked_attachments).includes(a.name)?'<span class="badge b-high">tracked</span>':""])));
  modal("Email details",body,null,"Close");
}

async function trackingProfiles(main){
  const [r,devs]=await Promise.all([api("/api/tracking/profiles"+qp()),api("/api/tracking/devices"+qp())]);
  const add=el(`<button class="btn sm">+ New profile</button>`); add.onclick=()=>editTrackingProfile(null,r.defaults,main);
  const head=el(`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><div class="muted">A profile sets which trackers run, which file types are watched and how often events are sent. Each computer uses its assigned profile, otherwise the <b>default</b>.</div></div>`);
  head.appendChild(add); main.appendChild(head);
  const on=(v)=>v?`<span class="badge b-ok">on</span>`:`<span class="badge b-off">off</span>`;
  const pc=el(`<div class="card" style="margin-bottom:16px"></div>`);
  pc.appendChild(tableFrom(["Profile","Logins","Network","Wi-Fi","USB files","Email files","All email","Apps","Web","No private","File types","Sync every","Computers","Actions"],
    r.profiles.map(p=>{ const s=p.settings;
      return [`<b>${esc(p.name)}</b>${p.is_default?' <span class="badge b-ok">default</span>':""}${p.description?`<div class="muted" style="font-size:11px">${esc(p.description)}</div>`:""}`,
        on(s.logins),on(s.network),on(s.wifi),on(s.usb_files),on(s.email_files),on(s.email_all),on(s.app_activity),on(s.web_activity),on(s.block_private_browsing),
        `<span class="muted" style="font-size:12px">${esc((s.file_types||[]).join(" "))}</span>`,
        esc((TRK_SYNC.find(x=>x[0]===s.sync_interval)||[0,s.sync_interval+" s"])[1]),p.devices,
        `<button class="btn sm ghost" data-ed="${p.id}">Edit</button> <button class="btn sm ghost" data-as="${p.id}">Assign computers</button>`+
        (p.is_default?"":` <button class="btn sm ghost" data-df="${p.id}">Make default</button> <button class="btn sm danger" data-rm="${p.id}">Delete</button>`)]; })));
  main.appendChild(pc);
  pc.querySelectorAll("[data-ed]").forEach(b=>b.onclick=()=>editTrackingProfile(r.profiles.find(p=>p.id===b.dataset.ed),r.defaults,main));
  pc.querySelectorAll("[data-as]").forEach(b=>b.onclick=()=>assignTrackingProfile(r.profiles.find(p=>p.id===b.dataset.as),devs,main));
  pc.querySelectorAll("[data-df]").forEach(b=>b.onclick=async()=>{ if(!confirm("Make this the company default? Computers without their own profile will use it.")) return;
    try{ await api(`/api/tracking/profiles/${b.dataset.df}/default`+qp(),{method:"POST"}); toast("Default profile changed"); route(); }catch(e){ toast(e.message,true); } });
  pc.querySelectorAll("[data-rm]").forEach(b=>b.onclick=async()=>{ if(!confirm("Delete this profile? Its computers go back to the default profile.")) return;
    try{ await api(`/api/tracking/profiles/${b.dataset.rm}`+qp(),{method:"DELETE"}); toast("Profile deleted"); route(); }catch(e){ toast(e.message,true); } });
  main.appendChild(cardTable("Computers and their profile",["Computer","Department","Location","Last seen","Profile"],
    devs.map(d=>[esc(d.hostname),esc(d.department||"—"),esc(d.location||"—"),fmtUtc(d.last_seen),
      `${esc(d.profile_name)}${d.uses_default?' <span class="muted" style="font-size:11px">(default)</span>':""}`])));
}

function editTrackingProfile(p, defaults, main){
  const s=p?p.settings:defaults;
  const body=el(`<div style="max-width:620px">
    <div class="field"><label>Profile name</label><input id="pn" value="${esc(p?p.name:"")}" placeholder="e.g. Finance – strict"/></div>
    <div class="field"><label>Description (optional)</label><input id="pd" value="${esc(p&&p.description||"")}"/></div>
    <div class="field"><label>Trackers</label><div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 16px">
      ${[["logins","Logins — logon/logoff, lock/unlock, startup/shutdown, sleep/wake"],["network","Network — adapters, IP changes, internet lost/restored"],
         ["wifi","Wi-Fi — connected/disconnected, network name, signal"],["usb_files","USB — drives inserted/removed, tracked files copied to/from USB"],
         ["email_files","Email — tracked files sent from Outlook, or attached in webmail (best effort)"],["email_all","Track ALL emails sent from Outlook (from, to, cc, bcc, subject, attachments) — not only tracked files"],["block_private_browsing","Block private / incognito browsing (Chrome, Edge, Brave, Firefox) so all browsing is recorded — needs the agent installed for all users"],["app_activity","App activity — time spent per application/window (idle excluded)"],["web_activity","Web activity — websites visited (Chrome, Edge, Firefox…; URLs without query string)"],["alert_on_transfer","Raise an alert when a tracked file leaves by USB or email"]]
        .map(([k,t])=>`<label style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" data-k="${k}" ${s[k]?"checked":""}/> <span>${esc(t)}</span></label>`).join("")}</div></div>
    <div class="field"><label>Tracked file types (used by USB and email tracking)</label><input id="pt" value="${esc((s.file_types||[]).join(", "))}"/>
      <div class="muted" style="font-size:11px">Comma-separated, e.g. .xlsx, .pdf, .docx, .zip</div></div>
    <div class="field"><label>Send events to the server every</label><select id="ps">${TRK_SYNC.map(([v,t])=>`<option value="${v}" ${v===s.sync_interval?"selected":""}>${t}</option>`).join("")}</select></div>
    <p class="muted" style="font-size:12px">Only metadata is recorded (file name, type, size, time, USB drive, Wi-Fi name, email recipients and subject) — never file contents or email text. Computers pick up changes at their next check-in (about a minute).</p></div>`);
  modal(p?`Edit profile — ${p.name}`:"New tracking profile",body,async()=>{
    const settings={file_types:body.querySelector("#pt").value,sync_interval:+body.querySelector("#ps").value};
    body.querySelectorAll("[data-k]").forEach(c=>settings[c.dataset.k]=c.checked);
    const payload={name:body.querySelector("#pn").value.trim(),description:body.querySelector("#pd").value.trim()||null,settings};
    if(!payload.name) throw new Error("Enter a profile name");
    await api(p?`/api/tracking/profiles/${p.id}`+qp():"/api/tracking/profiles"+qp(),{method:p?"PUT":"POST",body:payload});
    toast(p?"Profile saved":"Profile created"); location.hash="tracking/profiles"; route();
  },p?"Save":"Create");
}

function assignTrackingProfile(p, devs, main){
  const body=el(`<div style="max-width:620px"><p class="muted">Tick the computers that should use <b>${esc(p.name)}</b>.${p.is_default?" (This is the default profile: ticked computers go back to following the default.)":""}</p>
    <div style="margin-bottom:8px"><label><input type="checkbox" id="all"/> Select all</label></div>
    <div style="max-height:360px;overflow:auto">${devs.length?devs.map(d=>`<label style="display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--border,#334155)">
      <input type="checkbox" value="${d.id}" ${d.profile_id===p.id?"checked":""}/> <span style="flex:1">${esc(d.hostname)} <span class="muted" style="font-size:11px">${esc(d.department||"")}</span></span>
      <span class="muted" style="font-size:12px">now: ${esc(d.profile_name)}</span></label>`).join(""):'<p class="muted">No computers enrolled yet.</p>'}</div></div>`);
  body.querySelector("#all").onchange=(e)=>body.querySelectorAll("input[value]").forEach(c=>c.checked=e.target.checked);
  modal(`Assign computers — ${p.name}`,body,async()=>{
    const ids=[...body.querySelectorAll("input[value]:checked")].map(c=>c.value);
    if(!ids.length) throw new Error("Tick at least one computer");
    const r=await api(`/api/tracking/profiles/${p.id}/assign`+qp(),{method:"POST",body:{device_ids:ids}});
    toast(`${r.assigned} computer(s) now use ${p.name}`); route();
  },"Assign");
}

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
  const devs=await api("/api/tracking/devices"+qp()).catch(()=>[]);
  let params={};
  const bar=filterBar(devs,(p)=>{ params=p; toast(p.device_id?"Reports limited to the selected computer":"Reports for all computers"); });
  main.appendChild(bar);
  main.appendChild(el(`<div class="muted" style="margin:-6px 0 12px">Every report below uses the computer and period selected here (Apply first). Inventory reports (devices, software) ignore the period.</div>`));
  const groups=[
    ["Computer-wise", [["computer_summary","Computer-wise summary","App/web/idle time, top app & website, logons, USB & email file events, alerts — one row per computer"],
                       ["activity","Activity (detailed)","Every application window and website visit with time and duration"],
                       ["app_usage","Application usage by computer","Time per application on each computer"],
                       ["web_usage","Website usage by computer","Time and visits per website on each computer"],
                       ["tracking","Logins, network, USB & email","Logon/logoff, Wi-Fi/network changes, USB and email file events"],
                       ["email","Emails sent","From, To, CC, BCC, subject and attachments of every tracked email"]]],
    ["Inventory & security", [["hardware","Computer hardware","Make, model, serial, CPU, RAM, disks, free space, GPU, OS, MAC — one row per computer"],["devices","Device inventory",""],["software","Software by computer",""],["alerts","Alerts",""],
                       ["assets","Assets",""],["employees","Employees",""],["license","License",""]]],
  ];
  groups.forEach(([gname,kinds])=>{
    main.appendChild(el(`<h3 style="margin:16px 0 8px">${esc(gname)}</h3>`));
    const grid=el(`<div class="grid kpis"></div>`);
    kinds.forEach(([k,label,desc])=>{
      const card=el(`<div class="card"><h3 style="margin:0 0 4px">${esc(label)}</h3>${desc?`<div class="muted" style="font-size:12px;margin-bottom:10px">${esc(desc)}</div>`:""}</div>`);
      const row=el(`<div class="toolbar"></div>`);
      ["csv","xlsx","pdf"].forEach(fmt=>{ const b=el(`<button class="btn sm ghost">${fmt.toUpperCase()}</button>`);
        b.onclick=()=>downloadWithAuth(`/api/reports/${k}`+qp({...params,fmt}),`${k}.${fmt}`); row.appendChild(b); });
      card.appendChild(row); grid.appendChild(card);
    });
    main.appendChild(grid);
  });
  params=bar.values();
};

VIEWS.downloads = async (main) => {
  main.innerHTML=""; main.appendChild(topbar("Software Downloads & 3-Tier Architecture"));

  main.appendChild(el(`<div class="card" style="background: var(--bg-alt,#1e293b); border: 1px solid var(--border,#334155); margin-bottom: 20px;">
    <h3 style="margin:0 0 10px; color: var(--accent,#38bdf8);">🌐 3-Tier Architecture Overview</h3>
    <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-top: 15px;">
      <div style="background: rgba(255,255,255,0.04); padding: 12px; border-radius: 6px;">
        <strong style="color: #60a5fa">Part 1: License Server (Voyager Inc)</strong>
        <p class="muted" style="font-size: 0.85rem; margin-top: 5px;">Central licensing server managed by Voyager Inc. Issues tenant licenses & distributes the Client Server installer package.</p>
      </div>
      <div style="background: rgba(255,255,255,0.04); padding: 12px; border-radius: 6px;">
        <strong style="color: #34d399">Part 2: Client Management Server</strong>
        <p class="muted" style="font-size: 0.85rem; margin-top: 5px;">On-premise or cloud server software installed by customer. Activated via license key; generates pre-configured Endpoint Agents.</p>
      </div>
      <div style="background: rgba(255,255,255,0.04); padding: 12px; border-radius: 6px;">
        <strong style="color: #f472b6">Part 3: Endpoint Client (Agent)</strong>
        <p class="muted" style="font-size: 0.85rem; margin-top: 5px;">Lightweight Windows agent installed on employee PCs. Automatically self-enrolls & streams health, activity, and evidence to Client Server.</p>
      </div>
    </div>
  </div>`));

  if(S.role==="platform_super_admin"){
    main.appendChild(el(`<div class="notice">You are logged in as <b>Platform Super Admin (Voyager Inc)</b>. You can download the Client Server setup package below to distribute to tenant customers.</div>`));
  }

  const grid=el(`<div class="grid" style="grid-template-columns:1fr 1fr"></div>`);

  const s=el(`<div class="card"><h3 style="margin:0 0 8px">1 · Client Management Server Software</h3>
    <p class="muted">Double-click Windows installer (<b>Server_Setup.exe</b>) — Management Server + updater bundled inside. No Python required on the customer server machine.</p></div>`);
  const sb=el(`<button class="btn">⬇ Download Server_Setup.exe</button>`);
  sb.onclick=()=>downloadWithAuth("/api/download/server","Server_Setup.exe");
  s.appendChild(sb);
  s.appendChild(el(`<p class="muted" style="margin-top:10px">Installs service, adds firewall rule, opens admin console. <code>update.exe</code> included for one-click updates from Voyager License Server. <b>For Windows Server 2016+ / Windows 10+</b></p>`));
  const b12=el(`<button class="btn ghost" style="margin-top:8px">⬇ Universal Zip Bundle (Python 3.8 / Server 2012)</button>`);
  b12.onclick=()=>downloadWithAuth("/api/download/server-bundle","EndpointManagementServer-Universal-Windows.zip");
  s.appendChild(b12);
  s.appendChild(el(`<p class="muted" style="margin-top:8px">Universal zip package with <code>SETUP_AND_RUN.bat</code> and <code>UPDATE.bat</code> scripts.</p>`));
  grid.appendChild(s);

  const a=el(`<div class="card"><h3 style="margin:0 0 8px">2 · Pre-Configured Endpoint Client (Agent)</h3>
    <p class="muted">One complete <b>VoyagerAgent.exe</b> — everything bundled, nothing else to install (no Python). Generated by this server and auto-configured for your company: server address, license and enrollment code are built into the file.</p></div>`);
  if(S.role==="platform_super_admin"){
    // agent packages are company-specific: the company owner downloads them on its client server
    a.appendChild(el(`<div class="notice">Agent packages are generated on each customer's <b>client server</b> (company owner → Downloads). Upload <b>VoyagerAgent.exe</b> once in <a href="#settings">Settings → Agent program</a>; client servers fetch it from here automatically.</div>`));
  } else {
    const ab=el(`<button class="btn">⬇ Download VoyagerAgent.exe</button>`);
    ab.onclick=async()=>{ try{ await downloadWithAuth("/api/download/agent", "VoyagerAgent.exe"); }catch(e){ toast(e.message,true); } };
    a.appendChild(ab);
  }
  a.appendChild(el(`<p class="muted" style="margin-top:10px">Copy the file to the employee PC and <b>double-click it</b> (as administrator). It installs to <code>C:\\Program Files\\VoyagerAgent</code>, connects to this server automatically, runs in the background and starts for every user at logon. Running a newer download again updates it.</p>`));
  grid.appendChild(a);

  main.appendChild(grid);
};

VIEWS.licenses = async (main) => {
  main.innerHTML="";
  const isPlatform=S.role==="platform_super_admin";
  const tools=[];
  if(isPlatform){
    const b=el(`<button class="btn sm">+ Customer</button>`); b.onclick=()=>addTenant(main); tools.push(b);
    const dlSrv=el(`<button class="btn sm ghost">⬇ Download Client Server Setup</button>`); dlSrv.onclick=()=>downloadWithAuth("/api/download/server","Server_Setup.exe"); tools.push(dlSrv);
    const dlUni=el(`<button class="btn sm ghost">⬇ Universal Zip (Server 2012+)</button>`); dlUni.onclick=()=>downloadWithAuth("/api/download/server-bundle","EndpointManagementServer-Universal-Windows.zip"); tools.push(dlUni);
    const a=el(`<button class="btn sm">🔑 Activate with .lic file</button>`); a.onclick=()=>activateOnline(main); tools.push(a);
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
  const t=tableFrom(["Company","Registered email","Client Server Version & Update","Status","Retention","Actions"],
    tenants.map(t=>{
      const verBadge = t.client_server_version
        ? `<span class="badge b-ok">${esc(t.client_server_version)}</span>`
        : `<span class="badge b-off">Offline / Not reported</span>`;
      const updStr = t.client_server_updated_at ? `<div class="muted" style="font-size:11px">Updated: ${fmtDate(t.client_server_updated_at)}</div>` : "";
      const syncStr = t.last_sync_at ? `<div class="muted" style="font-size:10px">Synced: ${fmtDate(t.last_sync_at)}</div>` : "";
      const verCol = `<div>${verBadge}${updStr}${syncStr}</div>`;
      const isInactive = (t.status||"active").toLowerCase() === "inactive";
      const statusBtn = isPlatform
        ? (isInactive
            ? ` <button class="btn sm" data-status="${t.id}" data-set="active" style="background:#16a34a;color:#fff;" title="Activate company">Activate</button>`
            : ` <button class="btn sm ghost" data-status="${t.id}" data-set="inactive" style="color:#eab308;border-color:#eab308;" title="Deactivate company">Inactive</button>`)
        : "";
      const removeBtn = isPlatform
        ? ` <button class="btn sm ghost" data-del="${t.id}" data-name="${esc(t.company_name)}" style="color:#ef4444;border-color:#ef4444;" title="Remove company">Remove</button>`
        : "";
      return [
        esc(t.company_name),
        esc(t.contact_email||"—"),
        verCol,
        statusBadge(t.status),
        `${t.evidence_retention_days}d ev / ${t.event_retention_days}d evt`,
        `<button class="btn sm ghost" data-view="${t.id}">View data</button>`+
        (isPlatform?` <button class="btn sm ghost" data-cred="${t.id}">Credentials</button> <button class="btn sm ghost" data-lic="${t.id}">+ License</button> <button class="btn sm ghost" data-lics="${t.id}">Licenses</button>${statusBtn}${removeBtn}`:"")
      ];
    }));
  wrap.appendChild(t);
  wrap.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>{ S.activeTenant=b.dataset.view; localStorage.setItem("emp_active_tenant",S.activeTenant); toast("Tenant selected"); location.hash="dashboard"; });
  wrap.querySelectorAll("[data-cred]").forEach(b=>b.onclick=()=>showCredentials(tenants.find(x=>x.id===b.dataset.cred)));
  wrap.querySelectorAll("[data-lic]").forEach(b=>b.onclick=()=>addLicense(b.dataset.lic));
  wrap.querySelectorAll("[data-lics]").forEach(b=>b.onclick=()=>showLicenses(b.dataset.lics));
  wrap.querySelectorAll("[data-status]").forEach(b=>b.onclick=async()=>{
    const newStatus = b.dataset.set;
    const actionLabel = newStatus === "active" ? "activate" : "deactivate";
    if(!confirm(`Are you sure you want to ${actionLabel} this company?`)) return;
    try {
      await api(`/api/tenants/${b.dataset.status}/status`, {method:"POST", body:{status: newStatus}});
      toast(`Company marked as ${newStatus}`);
      route();
    } catch(err) {
      toast(err.message, true);
    }
  });
  wrap.querySelectorAll("[data-del]").forEach(b=>b.onclick=async()=>{
    const name = b.dataset.name;
    if(!confirm(`Are you sure you want to permanently remove company "${name}" and all its licenses and data?\n\nThis action cannot be undone.`)) return;
    try {
      await api(`/api/tenants/${b.dataset.del}`, {method:"DELETE"});
      toast(`Company "${name}" removed successfully`);
      route();
    } catch(err) {
      toast(err.message, true);
    }
  });
  return wrap;
}

async function showCredentials(tenant){
  const users=await api(`/api/users?tenant_id=${tenant.id}`);
  const body=el(`<div></div>`);
  body.appendChild(el(`<div class="muted" style="margin-bottom:10px">Registered email: <b>${esc(tenant.contact_email||"—")}</b> · Phone: ${esc(tenant.contact_phone||"—")}</div>`));
  body.appendChild(el(`<div class="notice">Passwords are stored one-way (hashed). You can reset password directly or <b>Download Reset Key File (.txt)</b> to import on the Client Server.</div>`));
  const t=tableFrom(["Username / email","Role","Active","Last login","Actions"],
    users.map(u=>[esc(u.email),`<span class="pill">${esc(u.role)}</span>`,u.is_active?`<span class="badge b-ok">yes</span>`:`<span class="badge b-off">no</span>`,
      u.last_login?fmtDate(u.last_login):"never",
      `<button class="btn sm" data-reset="${u.id}" data-email="${esc(u.email)}">Reset password</button> <button class="btn sm ghost" data-dlkey="${u.id}" data-email="${esc(u.email)}">⬇ Reset Key (.txt)</button>`]));
  body.appendChild(t);
  const bg=modal(`Credentials — ${esc(tenant.company_name)}`, body, null, "Close");
  body.querySelectorAll("[data-reset]").forEach(b=>b.onclick=()=>resetPassword(b.dataset.reset, b.dataset.email, tenant));
  body.querySelectorAll("[data-dlkey]").forEach(b=>b.onclick=()=>downloadWithAuth(`/api/users/${b.dataset.dlkey}/download-reset-key`, `RESET_KEY_${(tenant.company_name||"client").replace(/[^A-Za-z0-9_-]+/g,"_")}_${b.dataset.email}.txt`));
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
    const out=el(`<div>
      <p class="muted">Share this with the user. It is shown only once. The client will apply it on its next sync (auto every 10 min, or Settings → Sync from license server now).</p>
      <pre class="json">Company  : ${esc(tenant.company_name||"")}
Username : ${esc(r.email)}
Password : ${esc(r.new_password)}</pre>
      <p>${emailLine}</p></div>`);
    const dl=el(`<button class="btn">⬇ Download credentials (.txt)</button>`);
    dl.onclick=()=>{
      const body=`Voyager Management Server - Admin credentials\r\n`+
        `=============================================\r\n\r\n`+
        `Company  : ${tenant.company_name||""}\r\n`+
        `Username : ${r.email}\r\n`+
        `Password : ${r.new_password}\r\n`+
        `Issued   : ${new Date().toLocaleString()}\r\n\r\n`+
        `The client server applies this automatically on its next sync with the license server\r\n`+
        `(every 10 minutes), or immediately via Settings -> "Sync from license server now".\r\n`+
        `Then sign in to the client console with the username and password above.\r\n`;
      const blob=new Blob([body],{type:"text/plain"});
      const url=URL.createObjectURL(blob);
      const a=document.createElement("a"); a.href=url;
      a.download=`${(tenant.company_name||"client").replace(/[^A-Za-z0-9_-]+/g,"_")}_admin_credentials.txt`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    };
    out.appendChild(dl);
    modal("New password", out, null, "Done");
  }, "Reset");
}
async function activateOnline(main){
  const meta=await api("/api/meta").catch(()=>({}));
  const f=fields([
    {k:"license_server",label:"Cloud license server URL",value:meta.license_server||"http://vmgmt.voyager.co.in:8084"},
    {k:"license_id",label:"License ID"},
    {k:"license_key",label:"License Key",type:"textarea"},
    {k:"owner_password",label:"Company admin password (leave blank = use the password issued on the cloud)",type:"password"},
  ]);
  const ins=f.querySelectorAll("input,textarea");
  const pick=licFilePicker(d=>{ ins[1].value=d.license_id; ins[2].value=d.license_key; });
  const wrap=el(`<div></div>`);
  wrap.appendChild(pick.node);
  wrap.appendChild(f);
  ins[ins.length-1].autocomplete="new-password";   // stop the browser auto-filling a saved password
  modal("Activate this server from the license server",wrap,async()=>{
    const v=f._values();
    if(v.license_key.trim().startsWith("{")) pick.parse(v.license_key);       // whole .lic pasted
    const key=cleanKey(f._values().license_key), lid=cleanKey(f._values().license_id);
    if(!lid||!key) throw new Error("Attach the .lic file, or enter License ID and License Key.");
    const r=await api("/api/license/activate-online",{method:"POST",body:{
      license_server:v.license_server.trim(), license_id:lid,
      license_key:key, owner_password:v.owner_password}});
    const out=el(`<div><p class="muted">${esc(r.message||"Activated.")}</p>
      <pre class="json">Company  : ${esc(r.company_name)}${r.branch_name?`\nBranch   : ${esc(r.branch_name)}`:""}
Devices  : ${esc(r.max_devices??"—")}
Username : ${esc(r.owner_email||"—")}
Password : ${esc(r.owner_password||(r.password_source==="cloud"?"(same password as issued on the cloud license server)":"(unchanged — use the password you set)"))}</pre>
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
    {k:"branch_name",label:"Branch / site name (optional — blank = main office)"},
    {k:"edition",label:"License edition",type:"select",options:["standard","enterprise","demo","custom"],value:"standard"},
    {k:"license_type",label:"License type",type:"select",options:[{v:"subscription_monthly",t:"Subscription (monthly)"},{v:"lifetime",t:"Lifetime (one-time, 1yr support)"}],value:"subscription_monthly"},
    {k:"term_days",label:"Term (days) — ignored for lifetime",type:"number",value:365},
    {k:"max_devices",label:"Max devices",type:"number",value:25},
  ]);
  modal("New customer — create account, license & email key",f,async()=>{
    const v=f._values();
    const body={company_name:v.company_name,contact_email:v.contact_email,deployment_model:v.deployment_model,
      branch_name:v.branch_name||null,
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
async function addLicense(tenantId){
  const existing=await api(`/api/tenants/${tenantId}/licenses`).catch(()=>[]);
  const live=existing.filter(l=>l.status!=="revoked");
  const f=fields([
    {k:"branch_name",label:live.length?"Branch name (required — this company already has a license)":"Branch name (optional — blank = main office)"},
    {k:"edition",label:"Edition",type:"select",options:["demo","standard","custom","enterprise"],value:"standard"},
    {k:"term_days",label:"Term (days)",type:"number",value:365},
    {k:"max_devices",label:"Max devices",type:"number",value:25},
    {k:"max_admins",label:"Max admins",type:"number",value:3},
    {k:"is_demo",label:"Demo?",type:"select",options:[{v:"false",t:"No"},{v:"true",t:"Yes"}],value:"false"}]);
  if(live.length) f.prepend(el(`<div class="notice">This company already has ${live.length} license(s): ${live.map(l=>`<b>${esc(l.branch_name||"Main office")}</b>`).join(", ")}. To add devices, close this and use <b>Licenses → Increase</b>. Create a new license only for a different branch.</div>`));
  modal("Create license",f,async()=>{
    const v=f._values();
    const pkg=await api(`/api/tenants/${tenantId}/licenses`,{method:"POST",body:{branch_name:v.branch_name||null,edition:v.edition,term_days:+v.term_days,max_devices:+v.max_devices,max_admins:+v.max_admins,is_demo:v.is_demo==="true"}});
    const body=el(`<div><p class="muted">Give the .lic file to the customer and attach it on the client server's activation screen.</p>
      <pre class="json">Company:     ${esc(pkg.company_name)}${pkg.branch_name?`\nBranch:      ${esc(pkg.branch_name)}`:""}\nLicense ID:  ${esc(pkg.license_id)}\nLicense Key: ${esc(pkg.activation_token)}</pre></div>`);
    const dl=el(`<button class="btn">⬇ Download license file (.lic)</button>`);
    dl.onclick=()=>downloadWithAuth(pkg.download_url||`/api/licenses/${pkg.license_id}/key`,"license.lic");
    body.appendChild(dl);
    modal("License created",body,null);
  },"Create");
}
async function showLicenses(tenantId, bgPrev){
  if(bgPrev) bgPrev.remove();
  const rows=await api(`/api/tenants/${tenantId}/licenses`);
  const body=el(`<div></div>`);
  if(!rows.length) body.appendChild(el(`<p class="muted">No licenses yet. Use <b>+ License</b> to create one.</p>`));
  body.appendChild(tableFrom(["Branch","Edition","Status","Activated","Expiry","Devices","Admins","Actions"],
    rows.map(l=>[`<b>${esc(l.branch_name||"Main office")}</b>`,
      esc(l.edition)+(l.license_type==="lifetime"?' <span class="badge b-ok">lifetime</span>':""),
      statusBadge(l.status),
      l.activated?`<span class="badge b-ok">yes</span>${l.activated_server_id?`<div class="muted" style="font-size:10px">${esc(l.activated_server_id)}</div>`:""}`:`<span class="badge b-off">not yet</span>`,
      l.license_type==="lifetime"?"—":fmtDate(l.expiry_date),l.max_devices,l.max_admins,
      `<button class="btn sm ghost" data-key="${l.id}">⬇ .lic</button>`+
      (l.status!=="revoked"?` <button class="btn sm" data-inc="${l.id}">Increase</button> <button class="btn sm ghost" data-rev="${l.id}" style="color:#eab308;border-color:#eab308">Revoke</button>`:"")+
      (!l.activated?` <button class="btn sm ghost" data-rm="${l.id}" style="color:#ef4444;border-color:#ef4444">Remove</button>`:"")])));
  const bg=modal("Licenses",body,null,"Close");
  body.querySelectorAll("[data-key]").forEach(b=>b.onclick=()=>downloadWithAuth(`/api/licenses/${b.dataset.key}/key`,"license.lic"));
  body.querySelectorAll("[data-inc]").forEach(b=>b.onclick=()=>increaseLicense(rows.find(x=>x.id===b.dataset.inc),tenantId,bg));
  body.querySelectorAll("[data-rev]").forEach(b=>b.onclick=async()=>{
    if(!confirm("Revoke this license? The client server stops working at its next sync.")) return;
    try{ await api(`/api/licenses/${b.dataset.rev}/revoke`,{method:"POST"}); toast("License revoked"); showLicenses(tenantId,bg); }catch(e){ toast(e.message,true); }
  });
  body.querySelectorAll("[data-rm]").forEach(b=>b.onclick=async()=>{
    if(!confirm("Remove this license? Only possible because it was never activated.")) return;
    try{ await api(`/api/licenses/${b.dataset.rm}`,{method:"DELETE"}); toast("License removed"); showLicenses(tenantId,bg); }catch(e){ toast(e.message,true); }
  });
}
function increaseLicense(l, tenantId, bgList){
  const f=fields([
    {k:"max_devices",label:"Max devices",type:"number",value:l.max_devices},
    {k:"max_admins",label:"Max admins",type:"number",value:l.max_admins},
    {k:"extend_days",label:"Extend validity by (days, 0 = no change)",type:"number",value:0},
    {k:"branch_name",label:"Branch name (blank = main office)",value:l.branch_name||""},
  ]);
  f.appendChild(el(`<p class="muted">The client server applies the new limits automatically on its next sync (every 10 minutes). No new key is needed.</p>`));
  modal(`Increase license — ${esc(l.branch_name||"Main office")}`,f,async()=>{
    const v=f._values();
    await api(`/api/licenses/${l.id}/update`,{method:"POST",body:{max_devices:+v.max_devices,max_admins:+v.max_admins,
      extend_days:+v.extend_days||0,branch_name:v.branch_name}});
    toast("License updated");
    showLicenses(tenantId,bgList);
  },"Save");
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

  // ---- Software updates (platform super admin, and the client admin for their own server) ----
  if(["platform_super_admin","customer_owner"].includes(S.role)){
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

      // ---- Agent program (VoyagerAgent.exe) — what employees install; no Python needed ----
      if(S.role==="platform_super_admin"){
        const ac=el(`<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 6px">Agent program (VoyagerAgent.exe)</h3>
          <div class="notice" style="margin-bottom:10px">${info.has_agent_exe
            ? "✓ VoyagerAgent.exe is available — agent downloads contain the .exe (no Python needed on employee PCs)."
            : "⚠ VoyagerAgent.exe is not on this server yet. Agent downloads will not work until it is uploaded"+(info.role==="client_server"?" here or on the license server.":".")}</div>
          <div class="muted" style="margin-bottom:10px">${info.role==="license_server"
            ? "Upload it once here — every client server downloads it from this license server automatically."
            : "Normally fetched automatically from the license server. Upload it here only if this server cannot reach the license server."}</div>
          <div class="toolbar"><input type="file" accept=".exe" /><button class="btn">⬆ Upload VoyagerAgent.exe</button></div>
          <div class="muted" style="margin-top:8px"></div></div>`);
        const inp=ac.querySelector("input[type=file]"), st=ac.querySelector(".toolbar + .muted");
        ac.querySelector(".toolbar .btn").onclick=async()=>{
          const file=inp.files[0]; if(!file){ toast("Choose VoyagerAgent.exe first",true); return; }
          const fd=new FormData(); fd.append("file",file);
          st.textContent="Uploading "+file.name+" ("+Math.round(file.size/1048576)+" MB)…";
          try{ const r=await api("/api/system/agent-exe",{method:"POST",body:fd});
            st.textContent="✓ Uploaded ("+Math.round(r.size/1048576)+" MB). Agent downloads now include VoyagerAgent.exe.";
            toast("VoyagerAgent.exe uploaded");
          }catch(e){ st.textContent="✗ "+e.message; }
        };
        main.appendChild(ac);
      }
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

  // ---- Agent updates: versions per computer, approve updates (no reinstall needed) ----
  if(["customer_owner","it_admin"].includes(S.role)){
    const uc=el(`<div class="card" style="margin-bottom:16px"><h3 style="margin:0 0 6px">Agent updates</h3></div>`);
    main.appendChild(uc);
    const render=(u)=>{
      uc.querySelectorAll(":scope > :not(h3)").forEach(n=>n.remove());
      uc.appendChild(el(`<div class="muted" style="margin-bottom:10px">Latest agent on this server: <b>${esc(u.available_version||"none")}</b>
        · ${u.outdated} of ${u.devices.length} computer(s) need an update. Approved updates install on their own within about
        10 minutes (computers installed for all users) or 1 minute (per-user installs) — no reinstall, no logoff.
        Computers that still run an agent older than 4.3 need one last reinstall to get automatic updates.</div>`));
      if(u.note) uc.appendChild(el(`<div class="notice">${esc(u.note)}</div>`));
      const bar=el(`<div class="toolbar" style="margin-bottom:10px;align-items:center;gap:14px">
        <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="auau" ${u.auto?"checked":""}/> Update agents automatically when a new version is available</label>
        <button class="btn" id="auall" ${u.available_version&&u.outdated?"":"disabled"}>⬆ Update all agents now</button></div>`);
      uc.appendChild(bar);
      bar.querySelector("#auau").onchange=async(e)=>{ try{ render(await api("/api/agent-updates"+qp(),{method:"PUT",body:{auto:e.target.checked}})); toast(e.target.checked?"Automatic agent updates on":"Automatic agent updates off"); }catch(err){ toast(err.message,true); } };
      bar.querySelector("#auall").onclick=async()=>{ if(!confirm(`Update every computer to agent ${u.available_version}?`)) return;
        try{ render(await api("/api/agent-updates/approve"+qp(),{method:"POST",body:{all:true}})); toast("Update approved for all computers"); }catch(err){ toast(err.message,true); } };
      uc.appendChild(tableFrom(["Computer","Agent version","Last seen","Update"],
        u.devices.map(d=>[esc(d.hostname),esc(d.agent_version||"—"),fmtUtc(d.last_seen),
          d.up_to_date?`<span class="badge b-ok">up to date</span>`
          :d.pending?`<span class="badge b-pending">updating…</span>`
          :u.available_version?`<button class="btn sm" data-upd="${d.id}">Update to ${esc(u.available_version)}</button>`:"—"])));
      uc.querySelectorAll("[data-upd]").forEach(b=>b.onclick=async()=>{
        try{ render(await api("/api/agent-updates/approve"+qp(),{method:"POST",body:{device_ids:[b.dataset.upd]}})); toast("Update approved for this computer"); }catch(err){ toast(err.message,true); } });
    };
    try{ render(await api("/api/agent-updates"+qp())); }catch(e){ uc.appendChild(el(`<div class="muted">${esc(e.message)}</div>`)); }
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
      {k:"use_tls",label:"Use STARTTLS (port 587) — port 465 always uses SSL automatically",type:"select",options:[{v:"true",t:"Yes"},{v:"false",t:"No"}],value:String(e.use_tls)},
    ]);
    ec.appendChild(ef);
    const result=el(`<div id="emailResult" style="margin-top:12px"></div>`);
    const showResult=(okMsg,errMsg)=>{ result.innerHTML=""; result.appendChild(el(
      errMsg?`<div class="notice" style="background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.5);color:#fca5a5">✗ ${esc(errMsg)}</div>`
            :`<div class="notice" style="background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.5);color:#86efac">✓ ${esc(okMsg)}</div>`)); };
    const busy=(b,on,label)=>{ b.disabled=on; b.textContent=on?"Testing…":label; };
    const save=el(`<button class="btn">Save &amp; verify</button>`);
    save.onclick=async()=>{ busy(save,true); try{ const v=ef._values(); v.use_tls=(v.use_tls==="true"); if(v.password==="") delete v.password;
      const r=await api("/api/settings/email"+(scope?`?scope=${scope}`:""),{method:"PUT",body:v});
      const vr=r.verify||{};
      if(vr.ok){ showResult("Settings saved and SMTP login verified. Email delivery is working.",null); toast("Saved — SMTP verified ✓"); }
      else { showResult(null, "Settings saved, but SMTP check failed at: "+((vr.diagnostics||{}).failed_step||"unknown step")+" — see the details window. Emails fall back to the server outbox until this is fixed."); toast("Saved, but SMTP failed",true); }
      if(vr.diagnostics) showSmtpReport(vr.diagnostics,"Save & verify — SMTP check");
    }catch(err){ showResult(null, err.message); toast(err.message,true); } finally{ busy(save,false,"Save & verify"); } };
    const test=el(`<button class="btn ghost" style="margin-left:8px">Send test email</button>`);
    test.onclick=async()=>{ const to=prompt("Send test email to:", e.from_addr||""); if(!to) return;
      busy(test,true);
      try{ const r=await api("/api/settings/email/test"+(scope?`?scope=${scope}`:""),{method:"POST",body:{to}});
        if(r.delivered){ showResult("Test email sent to "+to+" via SMTP.",null); toast("Email sent ✓"); }
        else { showResult(null, "Email NOT sent — failed at: "+((r.diagnostics||{}).failed_step||r.via)+". See the details window."); toast("Email not sent",true); }
        if(r.diagnostics) showSmtpReport(r.diagnostics,"Send test email — details");
      }catch(err){ showResult(null, err.message); toast(err.message,true); } finally{ busy(test,false,"Send test email"); } };
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
  let stop=false, fails=0, lastTag=null;
  const body=el(`<div>
    <div class="notice">Live screen — the employee is not notified. This session is recorded in the audit log. Quality and speed adjust to the connection.</div>
    <div id="frameWrap" style="position:relative;background:#000;border-radius:10px;min-height:300px;display:grid;place-items:center">
      <span class="muted" id="frameStatus">Connecting to the computer…</span>
      <div id="liveBadge" style="position:absolute;top:8px;left:8px;background:#dc2626;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;display:none">● LIVE</div>
    </div>
    <div class="toolbar" style="margin-top:10px;align-items:center;gap:12px">
      <label class="muted" style="display:flex;align-items:center;gap:6px">Quality
        <select id="liveQ"><option value="35">Low (fast)</option><option value="55" selected>Medium</option><option value="80">High (sharp)</option></select></label>
      <label class="muted" style="display:flex;align-items:center;gap:6px">Speed
        <select id="liveS"><option value="1500">Slow</option><option value="800" selected>Normal</option><option value="400">Fast</option></select></label>
      <span class="muted" id="liveInfo" style="margin-left:auto;font-size:12px"></span>
      <button class="btn sm ghost" id="liveSnap">Save snapshot</button>
    </div></div>`);
  const bg=modal(`Live screen — ${esc(hostname||deviceId.slice(0,8))}`, body, null, "Close");
  const mo=new MutationObserver(()=>{ if(!document.body.contains(bg)){ stop=true; mo.disconnect(); api(`/api/live/${deviceId}/stop`,{method:"POST"}).catch(()=>{}); } });
  mo.observe(document.body,{childList:true});
  const statusEl=()=>body.querySelector("#frameStatus"), wrap=body.querySelector("#frameWrap");
  const badge=body.querySelector("#liveBadge"), info=body.querySelector("#liveInfo");
  const img=el(`<img style="max-width:100%;max-height:72vh;border-radius:10px;display:none"/>`); wrap.appendChild(img);
  let curURL=null;
  const opts=()=>({quality:+body.querySelector("#liveQ").value,max_width:body.querySelector("#liveQ").value==="80"?1600:1280,
    interval_ms:+body.querySelector("#liveS").value,monitor:0});
  body.querySelector("#liveSnap").onclick=()=>{ if(img.src){ const a=document.createElement("a"); a.href=img.src; a.download=`${(hostname||"screen").replace(/[^A-Za-z0-9_-]+/g,"_")}_${Date.now()}.jpg`; a.click(); } };
  // keepalive: tell the server we are watching (also carries current quality/speed)
  async function keepalive(){ if(stop) return; try{ const r=await api(`/api/live/${deviceId}/start`,{method:"POST",body:opts()});
      if(!r.online && statusEl()) statusEl().textContent="This computer is offline — it will stream when it reconnects."; }catch(e){ if(statusEl())statusEl().textContent=e.message; } setTimeout(keepalive, stop?0:8000); }
  // frame loop: fetch newest JPEG as fast as the chosen speed
  async function frames(){
    while(!stop){
      const t0=Date.now();
      try{
        const res=await fetch(API+`/api/live/${deviceId}/frame`,{headers:{Authorization:"Bearer "+S.token},cache:"no-store"});
        if(res.status===200){
          const age=res.headers.get("X-Frame-Age-Ms"); const tag=res.headers.get("Content-Length")+":"+age;
          if(tag!==lastTag){ lastTag=tag; const blob=await res.blob();
            if(curURL) URL.revokeObjectURL(curURL); curURL=URL.createObjectURL(blob); img.src=curURL;
            img.style.display="block"; if(statusEl())statusEl().remove(); badge.style.display="block";
            info.textContent=`${Math.round(blob.size/1024)} KB · ${age} ms behind`; }
          fails=0;
        } else if(res.status===204){ badge.style.display=img.src?"block":"none"; if(statusEl())statusEl().textContent="Waiting for the first frame… (the agent starts streaming within a few seconds)"; }
        else if(res.status===401){ stop=true; }
      }catch(e){ if(++fails>5 && statusEl()) statusEl().textContent="Connection lost — retrying…"; }
      const wait=Math.max(250,(+body.querySelector("#liveS").value)-(Date.now()-t0));
      await new Promise(r=>setTimeout(r,wait));
    }
  }
  keepalive(); frames();
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
    <div class="sub" style="margin-top:14px;display:flex;justify-content:space-between;align-items:center;">
      <a href="#" id="forgotUser">Forgot username?</a>
      <a href="#" id="forgotPw" style="color:var(--accent,#38bdf8);font-weight:600;">📁 Reset via .txt File / Password</a>
    </div>
    <p class="sub" style="margin-top:12px">First-time sign-in: <code>superadmin@platform.local</code> with the default password (see <code>FIRST_RUN.txt</code>). Change it after login.</p>
    <div id="loginVerBox" class="sub" style="margin-top:14px;padding-top:10px;border-top:1px solid rgba(255,255,255,0.08);text-align:center;font-size:0.8rem;color:var(--muted)">
      Current Version: <b style="color:var(--accent,#38bdf8)">v4.1.0</b> · Last Updated: <b>—</b>
    </div>
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

  api("/api/meta").then(m => {
    if(!m) return;
    const vStr = esc(m.version_display || ("v" + m.version));
    const uStr = m.updated_at ? fmtDate(m.updated_at) : "—";
    const vb = card.querySelector("#loginVerBox");
    if(vb){
      vb.innerHTML = `Current Version: <b style="color:var(--accent,#38bdf8)">${vStr}</b> · Last Updated: <b>${uStr}</b>`;
    }
  }).catch(()=>{});
}

function resetChooser(){
  let loadedToken = "";
  let detectedEmail = "";
  const body=el(`<div>
    <div style="display:flex;gap:8px;margin-bottom:16px;border-bottom:1px solid var(--border,#334155);padding-bottom:10px;">
      <button class="btn sm" id="tabFile" style="background:#0284c7;color:#fff;">📁 Attach .txt Reset File</button>
      <button class="btn sm ghost" id="tabEmail">✉ Send Reset Email</button>
      <button class="btn sm ghost" id="tabCode">🔑 Reset Code / Token</button>
    </div>

    <!-- TAB 1: Attach .txt File -->
    <div id="secFile">
      <div class="notice" style="margin-bottom:12px;">
        Enter your registered email address and attach the <b>RESET_KEY_...txt</b> file downloaded from the License Server (or <b>PASSWORD_RESET.txt</b>).
      </div>
      <div class="field" style="margin-bottom:12px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">1. Registered Email Address <span style="color:#ef4444">*</span></label>
        <input id="fileEmailInput" type="email" placeholder="owner@demo.local" style="width:100%" autocomplete="email" />
        <div class="muted" style="font-size:11px;margin-top:2px">Must match the registered administrator email for this account.</div>
      </div>
      <div class="field" style="margin-bottom:12px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">2. Attach .txt Reset Key File <span style="color:#ef4444">*</span></label>
        <input type="file" id="resetFilePicker" accept=".txt" style="width:100%;padding:10px;border:1px dashed #0284c7;background:rgba(2,132,199,0.06);border-radius:6px;cursor:pointer;" />
        <div id="fileAttachStatus" style="margin-top:6px;font-size:12px;"></div>
      </div>
      <div class="field" style="margin-bottom:12px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">3. New Password <span style="color:#ef4444">*</span></label>
        <input id="fileNewPw" type="password" placeholder="Enter new password" style="width:100%" autocomplete="new-password" />
      </div>
      <div class="field" style="margin-bottom:14px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">4. Confirm New Password <span style="color:#ef4444">*</span></label>
        <input id="fileConfirmPw" type="password" placeholder="Confirm new password" style="width:100%" autocomplete="new-password" />
      </div>
      <button class="btn" id="btnSubmitFile" style="width:100%;background:#0284c7;color:#fff;font-weight:600;padding:10px;">Apply .txt Reset File & Reset Password</button>
    </div>

    <!-- TAB 2: Send Email -->
    <div id="secEmail" style="display:none">
      <div class="notice" style="margin-bottom:12px;">Enter your account email address to receive a password reset link.</div>
      <div class="field" style="margin-bottom:14px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">Account Email</label>
        <input id="emailResetInput" type="email" placeholder="owner@demo.local" style="width:100%" />
      </div>
      <button class="btn" id="btnSendEmail" style="width:100%">Send Reset Link</button>
    </div>

    <!-- TAB 3: Manual Token -->
    <div id="secCode" style="display:none">
      <div class="field" style="margin-bottom:12px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">Registered Email Address</label>
        <input id="codeEmailInput" type="email" placeholder="owner@demo.local" style="width:100%" />
      </div>
      <div class="field" style="margin-bottom:12px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">Reset Token / Code</label>
        <textarea id="codeTokenInput" placeholder="Paste reset code or .txt payload" style="width:100%;height:70px;font-family:monospace;font-size:0.8rem;"></textarea>
      </div>
      <div class="field" style="margin-bottom:12px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">New Password</label>
        <input id="codeNewPw" type="password" placeholder="Enter new password" style="width:100%" />
      </div>
      <div class="field" style="margin-bottom:14px;">
        <label style="display:block;font-weight:600;margin-bottom:4px">Confirm Password</label>
        <input id="codeConfirmPw" type="password" placeholder="Confirm new password" style="width:100%" />
      </div>
      <button class="btn" id="btnSubmitCode" style="width:100%">Update Password</button>
    </div>
  </div>`);

  const bg=modal("Reset Admin Password", body, null, "Cancel");

  const tabFile = body.querySelector("#tabFile");
  const tabEmail = body.querySelector("#tabEmail");
  const tabCode = body.querySelector("#tabCode");
  const secFile = body.querySelector("#secFile");
  const secEmail = body.querySelector("#secEmail");
  const secCode = body.querySelector("#secCode");

  const switchTab = (activeTab) => {
    tabFile.className = activeTab==='file' ? "btn sm" : "btn sm ghost";
    tabFile.style.background = activeTab==='file' ? "#0284c7" : "";
    tabFile.style.color = activeTab==='file' ? "#fff" : "";

    tabEmail.className = activeTab==='email' ? "btn sm" : "btn sm ghost";
    tabEmail.style.background = activeTab==='email' ? "#0284c7" : "";
    tabEmail.style.color = activeTab==='email' ? "#fff" : "";

    tabCode.className = activeTab==='code' ? "btn sm" : "btn sm ghost";
    tabCode.style.background = activeTab==='code' ? "#0284c7" : "";
    tabCode.style.color = activeTab==='code' ? "#fff" : "";

    secFile.style.display = activeTab==='file' ? "block" : "none";
    secEmail.style.display = activeTab==='email' ? "block" : "none";
    secCode.style.display = activeTab==='code' ? "block" : "none";
  };

  tabFile.onclick = () => switchTab('file');
  tabEmail.onclick = () => switchTab('email');
  tabCode.onclick = () => switchTab('code');

  body.querySelector("#resetFilePicker").onchange = (e) => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = (evt) => {
        loadedToken = evt.target.result;
        detectedEmail = "";
        // Look for username/account in text
        const m = loadedToken.match(/(?:registered\s*email|username|account|email)\s*:\s*([^\r\n]+)/i);
        if (m) {
          detectedEmail = m[1].trim();
        } else if (loadedToken.includes("--- BEGIN RESET PAYLOAD ---")) {
          try {
            const raw = loadedToken.split("--- BEGIN RESET PAYLOAD ---")[1].split("--- END RESET PAYLOAD ---")[0].trim();
            const b64 = raw.split(".")[0];
            const parsed = JSON.parse(atob(b64));
            if (parsed && parsed.email) detectedEmail = parsed.email;
          } catch(err){}
        }
        const emailInp = body.querySelector("#fileEmailInput");
        const statusBadge = body.querySelector("#fileAttachStatus");
        if (detectedEmail) {
          if (!emailInp.value) emailInp.value = detectedEmail;
          statusBadge.innerHTML = `<span style="color:#22c55e">✓ Attached: <b>${esc(file.name)}</b> (Key for: <b>${esc(detectedEmail)}</b>)</span>`;
          toast("✓ Loaded reset key for " + detectedEmail);
        } else {
          statusBadge.innerHTML = `<span style="color:#22c55e">✓ Attached file: <b>${esc(file.name)}</b></span>`;
          toast("✓ Attached: " + file.name);
        }
      };
      reader.readAsText(file);
    }
  };

  body.querySelector("#btnSubmitFile").onclick = async () => {
    const email = (body.querySelector("#fileEmailInput").value || "").trim();
    const new_password = body.querySelector("#fileNewPw").value;
    const confirm = body.querySelector("#fileConfirmPw").value;

    if (!email) { toast("Please enter your registered email address", true); return; }
    if (!loadedToken) { toast("Please select and attach a .txt Reset Key file first", true); return; }
    if (!new_password) { toast("Please enter a new password", true); return; }
    if (new_password !== confirm) { toast("Passwords do not match", true); return; }

    try {
      await api("/api/auth/reset-password", {
        method: "POST",
        body: { email, token: loadedToken, new_password }
      });
      bg.remove();
      toast("✓ Password updated successfully! Please sign in.");
      const emailField = document.querySelector("#loginCard input[type='email']") || document.querySelector("#loginCard input");
      if (emailField) emailField.value = email;
    } catch(err) {
      toast(err.message, true);
    }
  };

  body.querySelector("#btnSendEmail").onclick = async () => {
    const email = (body.querySelector("#emailResetInput").value || "").trim();
    if (!email) { toast("Please enter your account email", true); return; }
    try {
      const r = await api("/api/auth/forgot-password", { method: "POST", body: { email } });
      bg.remove();
      toast(r.message || "Reset link sent to your email.");
    } catch(err) {
      toast(err.message, true);
    }
  };

  body.querySelector("#btnSubmitCode").onclick = async () => {
    const email = (body.querySelector("#codeEmailInput").value || "").trim();
    const token = (body.querySelector("#codeTokenInput").value || "").trim();
    const new_password = body.querySelector("#codeNewPw").value;
    const confirm = body.querySelector("#codeConfirmPw").value;

    if (!token) { toast("Please enter a reset token or code", true); return; }
    if (!new_password) { toast("Please enter a new password", true); return; }
    if (new_password !== confirm) { toast("Passwords do not match", true); return; }

    try {
      await api("/api/auth/reset-password", {
        method: "POST",
        body: { email: email || undefined, token, new_password }
      });
      bg.remove();
      toast("✓ Password updated successfully! Please sign in.");
    } catch(err) {
      toast(err.message, true);
    }
  };
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
    {k:"token",label:"Reset code or .txt payload",type:"textarea"},
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
  if(kind === "password" || kind === "pw"){
    resetChooser();
    return;
  }
  const f=fields([{k:"email",label:"Your company's registered email",type:"email"}]);
  modal("Forgot username", f, async()=>{
    const email=(f._values().email||"").trim();
    if(!email) throw new Error("Enter an email");
    const r=await api("/api/auth/forgot-username",{method:"POST",body:{email}});
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
