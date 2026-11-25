/* RFP Responder UI - plain JavaScript, no build step. */
"use strict";

const S = { settings: null, kb: null, projects: [], project: null, tab: "projects", preview: null,
            sheet: null, filter: "all", open: new Set(), checked: new Set(), job: null, kbView: null };

const COLS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").concat(["AA", "AB", "AC", "AD", "AE", "AF"]);
const ICON = n => `icons/${n}.png`;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, el = document) => el.querySelector(sel);

// ------------------------------------------------------------------ API
async function api(path, body = {}) {
  for (let attempt = 0; ; attempt++) {
    let r;
    try {
      r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Token": window.TOKEN }, body: JSON.stringify(body) });
    } catch (e) {
      // the server is not reachable (PC slept, app restarting): wait for it, then try once more
      if (attempt >= 1) throw new Error("No connection to RFP Responder");
      await waitOnline();
      continue;
    }
    if (r.status === 403) { staleWindow(); throw new Error("This window belongs to an earlier session"); }
    const d = await r.json().catch(() => ({ error: "Bad response" }));
    if (!r.ok || d.error) throw new Error(d.error || r.statusText);
    return d;
  }
}

// ------------------------------------------------------------------ connection
let reconnecting = null;
function offlineBar(text) {
  let el = $("#offline");
  if (!text) { el && el.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = "offline"; el.className = "msgbar warning offline"; document.body.appendChild(el); }
  el.innerHTML = `<span>${text}</span>`;
}
async function pingOnce() {
  try {
    const r = await fetch("/api/ping", { method: "POST", headers: { "X-Token": window.TOKEN } });
    if (r.status === 403) { staleWindow(); return false; }
    return r.ok;
  } catch { return false; }
}
function waitOnline() {
  if (!reconnecting) reconnecting = (async () => {
    offlineBar("Connection to RFP Responder lost. Reconnecting… Nothing you typed is lost. If the PC was restarted, start RFP Responder again; this window reconnects by itself.");
    while (!(await pingOnce())) await sleep(3000);
    offlineBar(""); reconnecting = null;
    msg("Connected again", "success", 3000);
    refreshState().catch(() => {});
  })();
  return reconnecting;
}
function staleWindow() {
  offlineBar("This window belongs to an earlier RFP Responder session. Close it and start RFP Responder again (your work is saved).");
}
async function safe(fn) { try { return await fn(); } catch (e) { msg(e.message, "error"); } }

function msg(text, kind = "info", ms = 6000) {
  const el = document.createElement("div");
  el.className = `msgbar ${kind}`;
  el.innerHTML = `<span>${esc(text)}</span><span class="x" title="Dismiss">&#x2715;</span>`;
  el.querySelector(".x").onclick = () => el.remove();
  $("#msgs").appendChild(el);
  if (ms) setTimeout(() => el.remove(), ms);
}

async function browse(kind, types = "", title = "") {
  const d = await api("/api/browse", { kind, types, title });
  return d.path;
}

// ------------------------------------------------------------------ state + saving
async function refreshState() {
  const d = await api("/api/state");
  S.settings = d.settings; S.kb = d.kb; S.projects = d.projects; S.version = d.version; S.view = d.view;
  S.dataDir = d.data_dir; S.libraries = d.libraries || []; S.platform = d.platform;
  const ini = (S.settings.user_initials || S.settings.user_name || "?").split(/[\s.]+/).map(x => x[0] || "").join("").slice(0, 3).toUpperCase();
  $("#avatar").textContent = S.settings.user_initials || ini;
  $("#avatar").title = S.settings.user_name || "Set your name in Settings";
  const chip = $("#kbchip");
  chip.classList.toggle("err", !!S.kb.error);
  $("#kbtext").textContent = S.kb.error ? "Knowledge areas unavailable" : `${S.kb.areas} areas · ${S.kb.documents} documents · ${S.kb.answers} earlier answers`;
  chip.title = S.kb.error ? `${S.kb.root}\n${S.kb.error}` : (S.kb.root || "");
}
async function loadAreas() {
  const d = await safe(() => api("/api/kb/areas"));
  if (d) { S.areas = d.areas; S.legacy = d.legacy; S.kbRoot = d.root; S.kbAnswers = d.answers; }
  return d;
}
const areaName = slug => (S.areas || []).find(a => a.slug === slug)?.name || slug;
const projAreas = p => p.areas == null ? (S.areas || []).map(a => a.slug) : p.areas;

let saveTimer = null;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveProject, 500);
}
async function saveProject() {
  if (!S.project) return;
  const p = { ...S.project }; delete p.rows; delete p.runs;
  await safe(() => api("/api/project/save", p));
}
function setPath(obj, path, val) {
  const ks = path.split("."); let o = obj;
  ks.slice(0, -1).forEach(k => { o[k] = o[k] ?? {}; o = o[k]; });
  o[ks[ks.length - 1]] = val;
}
function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj); }

// inputs with data-p bind to the project, data-s to settings
document.addEventListener("input", e => {
  const el = e.target;
  if (el.dataset.p && S.project) {
    setPath(S.project, el.dataset.p, el.type === "checkbox" ? el.checked : (el.type === "number" ? +el.value : el.value));
    scheduleSave();
    if (el.dataset.p === "name") $("#tb-project").textContent = "· " + el.value;
    if (el.dataset.rerender) render();
  }
  if (el.dataset.s && S.settings) {
    let v = el.type === "checkbox" ? el.checked : (el.type === "number" ? +el.value : el.value);
    if (el.dataset.s === "web_domains") v = el.value.split(/[\s,]+/).filter(Boolean);
    if (el.dataset.s === "extra_libraries") v = el.value.split(/\n/).map(x => x.trim()).filter(Boolean);
    if (el.dataset.s === "responsible_parties") v = el.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean);
    S.settings[el.dataset.s] = v;
    clearTimeout(S._st); S._st = setTimeout(() => safe(() => api("/api/settings", S.settings)), 600);
  }
});
document.addEventListener("change", e => {
  if (e.target.dataset.p === "req_source.docx_mode" && S.project) { saveProject().then(() => loadPreview(false)); return; }
  if (e.target.dataset.rerender) render();
});

document.addEventListener("input", e => {
  if (e.target.id === "themes" && S.project) { S.project.profile.themes = e.target.value.split(",").map(x => x.trim()).filter(Boolean); scheduleSave(); }
});

// reporting filters
document.addEventListener("change", e => {
  const k = e.target.dataset.rf; if (!k) return;
  const R = RF(); const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  if (v === "" || v === false) delete R.filters[k]; else R.filters[k] = v;
  loadReport();
});

// answers being edited are saved while typing, so a break (or an enforced shutdown) loses nothing
const rowTimers = {};
document.addEventListener("input", e => {
  const el = e.target;
  if (!el.dataset.row || !S.project || el.tagName === "SELECT") return;
  const r = S.project.rows.find(x => x.key === el.dataset.row); if (!r) return;
  r[el.dataset.f] = el.value;
  if (r.status === "approved") r.status = "draft";
  const k = el.dataset.row + "|" + el.dataset.f;
  clearTimeout(rowTimers[k]);
  rowTimers[k] = setTimeout(() => { delete rowTimers[k]; pushRows([{ key: r.key, [el.dataset.f]: el.value, status: r.status }]); }, 1200);
});

// click actions: data-act="name" data-arg="..."
document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  const fn = ACT[el.dataset.act];
  if (fn) { e.preventDefault(); fn(el.dataset.arg, el, e); }
});

// ------------------------------------------------------------------ stages
const STAGES = [
  { id: "requirements", n: 1, label: "Requirements" },
  { id: "sources", n: 2, label: "Sources" },
  { id: "strategy", n: 3, label: "Product & strategy" },
  { id: "output", n: 4, label: "Output" },
  { id: "respond", n: 5, label: "Respond & review" },
];
function stageDone(id) {
  const p = S.project; if (!p) return false;
  if (id === "requirements") return p.rows.length > 0;
  if (id === "sources") return p.folders.length > 0 || p.websites.length > 0 || p.use_kb;
  if (id === "strategy") return p.products.length > 0;
  if (id === "output") return (p.output.excel.enabled && (p.output.excel.compliance_col || Object.values(p.output.excel.marks || {}).some(Boolean))) || (p.output.word.enabled && p.output.word.template);
  if (id === "respond") return p.rows.length > 0 && p.rows.every(r => !r.selected || r.status === "approved");
  return false;
}
function renderStages() {
  const has = !!S.project;
  $("#stages").innerHTML =
    `<button class="stage ${S.tab === "projects" ? "on" : ""}" data-act="tab" data-arg="projects"><img src="${ICON("list")}" alt="">Responses</button>` +
    STAGES.map(s => `<button class="stage ${S.tab === s.id ? "on" : ""} ${stageDone(s.id) ? "done" : ""}" data-act="tab" data-arg="${s.id}" ${has ? "" : "disabled"}>
      <span class="num">${stageDone(s.id) && S.tab !== s.id ? "&#10003;" : s.n}</span>${s.label}</button>`).join("") +
    `<span class="sep"></span>
     <button class="stage ${S.tab === "report" ? "on" : ""}" data-act="tab" data-arg="report"><img src="${ICON("star-up")}" alt="">Reporting</button>
     <button class="stage ${S.tab === "kb" ? "on" : ""}" data-act="tab" data-arg="kb"><img src="${ICON("book")}" alt="">Knowledge base</button>
     <button class="stage ${S.tab === "settings" ? "on" : ""}" data-act="tab" data-arg="settings"><img src="${ICON("settings")}" alt="">Settings</button>
     <button class="stage" data-act="help"><span class="num">?</span>Help</button>`;
  $("#tb-project").textContent = S.project ? "· " + S.project.name : "";
}

function render() {
  renderStages();
  const v = VIEWS[S.tab] || VIEWS.projects;
  $("#main").innerHTML = `<div class="view">${v()}</div>`;
  if (S.tab === "kb") loadKbView();
  if (S.tab === "report") loadReport();
  clearTimeout(S._vt);
  S._vt = setTimeout(() => api("/api/view", { project: S.project?.id || "", tab: S.tab, open: [...S.open][0] || "" }).catch(() => {}), 800);
}
const fmtN = n => (n ?? 0).toLocaleString("en-US");

function pane(title, icon, body, right = "") {
  return `<section class="pane"><header>${icon ? `<img src="${ICON(icon)}" alt="">` : ""}${title}<span class="right">${right}</span></header><div class="body">${body}</div></section>`;
}
function hero(t, p) { return `<div class="hero"><div class="bar"></div><h1>${t}</h1>${p ? `<p>${p}</p>` : ""}</div>`; }
function toggle(label, desc, attr) {
  return `<div class="switch"><div><div class="t">${label}</div>${desc ? `<div class="d">${desc}</div>` : ""}</div><label class="tgl"><input type="checkbox" ${attr}><span></span></label></div>`;
}
function colSelect(path, val, allowNone = true, extra = "") {
  return `<select class="input" data-p="${path}" ${extra}>${allowNone ? `<option value="">—</option>` : ""}${COLS.map(c => `<option ${c === val ? "selected" : ""}>${c}</option>`).join("")}</select>`;
}
function scale() { return S.settings.scale; }
function compPill(code) {
  const s = scale().find(x => x.code === code);
  return code ? `<span class="comp" style="background:${esc(s?.color || "#ccc")}">${esc(s?.label || code)}</span>` : `<span class="muted small">—</span>`;
}

// ------------------------------------------------------------------ views
const VIEWS = {};

VIEWS.projects = () => {
  const list = S.projects.length ? `<div class="cards">${S.projects.map(p => `
    <div class="card" data-act="openProject" data-arg="${esc(p.id)}">
      <h3>${esc(p.name)}</h3>
      <div class="muted small">${esc(p.customer || "No customer set")}</div>
      <div class="inline small"><span class="tag info">${p.rows} requirements</span><span class="tag ${p.answered === p.rows && p.rows ? "ok" : "y"}">${p.answered} answered</span>
      <span class="muted" style="margin-left:auto">${esc(p.updated || "")}</span></div>
    </div>`).join("")}</div>` : `<div class="empty">No responses yet. Start one, point it at your requirements and your sources.</div>`;
  return `<div class="cols">${pane("Compliance responses", "list", `
      ${hero("Ask, and the responder answers", "Every tender requirement is a question sent out. RFP Responder answers it from your sources, with the evidence attached, and learns from every response you approve.")}
      <div class="field"><label>New response name</label><div class="inline"><input class="input lg" id="newname" placeholder="e.g. Lufthansa CCTV Frankfurt – Annex 1"><button class="btn primary lg" data-act="newProject">Start</button></div></div>
      <hr class="sep">${list}`)}
    ${pane("How it works", "star-up", `
      <ol style="margin:0;padding-left:20px;display:grid;gap:8px">
        <li><b>Requirements</b>: an Excel compliance sheet or a Word specification. Keep the customer's numbering.</li>
        <li><b>Sources</b>: the knowledge areas to use (XProtect, BriefCam, Husky IVO, …), plus folders that belong to this tender.</li>
        <li><b>Product &amp; strategy</b>: the product(s) to offer and the sales team's win strategy.</li>
        <li><b>Output</b>: which Excel columns to fill, and/or a Word template.</li>
        <li><b>Respond &amp; review</b>: Claude Code answers each requirement and quotes its evidence. Every quote and link is checked automatically; you approve before anything is exported.</li>
      </ol>
      <div class="msgbar info"><span>Answers can be legally binding. Nothing is marked compliant unless a quote from your sources supports it; rows without verified evidence are flagged for review.</span></div>
      <div class="footnote"><img src="${ICON("book")}" width="16" alt="">Approved answers go to the shared knowledge, so the next response starts from what the team already agreed.</div>`)}
  </div>`;
};

VIEWS.requirements = () => {
  const p = S.project, rs = p.req_source;
  const kind = rs.path ? (rs.path.toLowerCase().endsWith(".docx") ? "word" : "excel") : "";
  let mapping = "";
  if (kind === "excel" && S.preview) {
    const sh = S.preview.find(x => x.name === rs.sheet) || S.preview[0];
    const hr = +rs.header_row || 1;
    mapping = `
      <div class="row2">
        <div class="field"><label>Sheet</label><select class="input" data-p="req_source.sheet" data-rerender="1">${S.preview.map(s => `<option ${s.name === sh.name ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select></div>
        <div class="row3"><div class="field"><label>Header row</label><input class="input" type="number" min="1" data-p="req_source.header_row" value="${rs.header_row}" data-rerender="1"></div>
        <div class="field"><label>First requirement row</label><input class="input" type="number" min="1" data-p="req_source.first_row" value="${rs.first_row}"></div>
        <div class="field"><label>Last requirement row</label><input class="input" type="number" min="1" data-p="req_source.last_row" value="${esc(rs.last_row ?? "")}" placeholder="End of sheet" title="Leave empty to read to the end of the sheet"></div></div>
      </div>
      <div class="row4">
        <div class="field"><label>Requirement text</label>${colSelect("req_source.text_col", rs.text_col, false, 'data-rerender="1"')}</div>
        <div class="field"><label>ID / number</label>${colSelect("req_source.id_col", rs.id_col, true, 'data-rerender="1"')}</div>
        <div class="field"><label>Section (optional)</label>${colSelect("req_source.section_col", rs.section_col)}</div>
        <div class="field"><label>Customer guidance (optional)</label>${colSelect("req_source.context_col", rs.context_col, true, 'data-rerender="1"')}</div>
      </div>
      <div class="lbl">Preview of “${esc(sh.name)}” (${sh.max_row} rows)</div>
      <div class="tablewrap" style="max-height:300px"><table class="apollo grid"><thead><tr><th></th>${sh.cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>
      ${sh.grid.map((r, i) => `<tr class="${i + 1 === hr ? "hdr" : ""}"><td class="rn">${i + 1}</td>${r.map((c, j) => `<td class="${sh.cols[j] === rs.text_col ? "hl-text" : sh.cols[j] === rs.id_col ? "hl-id" : sh.cols[j] === rs.context_col ? "hl-ctx" : ""}" title="${esc(c)}">${esc(c)}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  } else if (kind === "word") {
    const m = rs.docx_mode || "lists";
    mapping = `<div class="lbl">What counts as a requirement?</div><div class="radios" style="grid-template-columns:1fr 1fr 1fr">
      ${[["lists", "Lists & shall/must", "Bullets, numbered items and sentences with shall, must, muss, soll …"], ["paragraphs", "Every paragraph", "Each body paragraph becomes a row"], ["tables", "Paragraphs + tables", "Also each table row"]]
        .map(([v, t, d]) => `<label class="radio ${m === v ? "on" : ""}"><input type="radio" name="dm" value="${v}" data-p="req_source.docx_mode" data-rerender="1" ${m === v ? "checked" : ""}><b>${t}</b><span>${d}</span></label>`).join("")}</div>
      <div class="hint">Section numbers come from the headings, so the customer's numbering is kept (e.g. 4.1.3 = third item under heading 4.1).</div>
      ${wordRange(rs)}`;
  }
  const rows = p.rows;
  const sel = rows.filter(r => r.selected).length;
  const table = rows.length ? `<div class="toolbar"><span class="tag info">${rows.length} requirements</span><span class="tag ok">${sel} selected</span><span class="grow"></span>
      <button class="btn sm tertiary" data-act="selAll" data-arg="1">Select all</button><button class="btn sm tertiary" data-act="selAll" data-arg="0">Select none</button></div>
    <div class="tablewrap"><table class="apollo"><thead><tr><th style="width:36px"></th><th style="width:90px">ID</th><th>Requirement</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><input type="checkbox" class="rowsel" data-key="${esc(r.key)}" ${r.selected ? "checked" : ""}></td><td>${esc(r.id)}</td><td class="req"><div class="small muted">${esc(r.section || "")}</div><div class="clip">${esc(r.text)}</div></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">Load a requirements file to see the rows here.</div>`;
  return `<div class="cols">${pane("Project & requirements", "document-upload", `
      ${hero("What are we answering?", "Pick the customer's compliance sheet or specification. Rows keep the customer's numbering.")}
      <div class="row2"><div class="field"><label>Response name</label><input class="input" data-p="name" value="${esc(p.name)}"></div>
      <div class="field"><label>Customer</label><input class="input" data-p="customer" value="${esc(p.customer)}" placeholder="e.g. Lufthansa Group"></div></div>
      <div class="row2"><div class="field"><label>Our role in this tender</label><select class="input" data-p="role">${["Vendor", "System integrator", "Prime contractor", "Distributor", "Consultant"].map(x => `<option ${x === p.role ? "selected" : ""}>${x}</option>`).join("")}</select></div>
      <div class="field"><label>Response language</label><select class="input" data-p="language">${["English", "German", "Danish", "French", "Dutch", "Italian", "Spanish", "Swedish", "Arabic"].map(x => `<option ${x === p.language ? "selected" : ""}>${x}</option>`).join("")}</select></div></div>
      <div class="field"><label>Requirements file (.xlsx, .xlsm or .docx)</label><div class="inline"><input class="input" id="reqpath" value="${esc(rs.path)}" readonly placeholder="No file selected"><button class="btn tertiary" data-act="pickReq">Browse…</button></div></div>
      ${mapping}
      <div class="footer-actions"><span class="hint">${rs.path ? "Loading again keeps answers for identical requirement text." : ""}</span><button class="btn primary lg" data-act="loadReq" ${rs.path ? "" : "disabled"}>Load requirements</button></div>`)}
    ${pane("Requirements", "list", table, rows.length ? `<button class="btn sm primary" data-act="tab" data-arg="sources">Next: sources →</button>` : "")}</div>`;
};

VIEWS.sources = () => {
  const p = S.project;
  const kbCols = (S.projCols || []).reduce((m, c) => (m[c.collection] = c, m), {});
  const folders = p.folders.map((f, i) => {
    const c = kbCols[f.path];
    return `<div class="lrow"><img src="${ICON("folder")}" alt=""><div class="path" title="${esc(f.path)}">&lrm;${esc(f.path)}</div>
      <input class="input sm" placeholder="Label, e.g. A&E specs" data-p="folders.${i}.label" value="${esc(f.label || "")}">
      <label class="check small" title="Index the folder, so its content can be searched and quoted"><input type="checkbox" data-p="folders.${i}.index" ${f.index !== false ? "checked" : ""}>Index</label>
      <span class="tag ${c ? "ok" : "y"}">${c ? c.docs + " docs indexed" : "not indexed"}</span>
      <button class="iconbtn" title="Remove" data-act="rmFolder" data-arg="${i}">&#x2715;</button></div>`;
  }).join("");
  const sites = p.websites.map((w, i) => {
    const c = Object.values(kbCols).find(x => w.url.startsWith(x.collection) || x.collection.startsWith(w.url.replace(/[^/]*$/, "")));
    return `<div class="lrow site"><img src="${ICON("world")}" alt=""><input class="input sm" data-p="websites.${i}.url" value="${esc(w.url)}" placeholder="https://...">
      <input class="input sm" type="number" min="1" max="5000" data-p="websites.${i}.max_pages" value="${w.max_pages || 200}" title="Max pages">
      <button class="btn sm secondary" data-act="crawl" data-arg="${i}">${c ? "Update" : "Read site"}</button>
      <button class="iconbtn" title="Remove" data-act="rmSite" data-arg="${i}">&#x2715;</button></div>`;
  }).join("");
  const chosen = projAreas(p);
  const areaChips = (S.areas || []).map(a => `<button class="chip ${chosen.includes(a.slug) ? "on" : ""}" data-act="toggleArea" data-arg="${esc(a.slug)}" title="${esc(a.description || "")}">${chosen.includes(a.slug) ? "&#10003; " : ""}${esc(a.name)}<span class="cnt">${a.published ? fmtN(a.documents) + " docs" : "empty"}</span></button>`).join("");
  return `<div class="cols">${pane("Knowledge areas", "book", `
      ${hero("Which knowledge applies?", "Choose the areas this response may draw on. Offering XProtect only? Leave BriefCam and Arcules out, so their documents cannot creep into the answers.")}
      ${toggle("Use the shared knowledge areas", "Search the chosen areas and the team's earlier approved answers for every requirement.", `data-p="use_kb" data-rerender="1" ${p.use_kb ? "checked" : ""}`)}
      <div class="chips ${p.use_kb ? "" : "hidden"}">${areaChips || `<span class="hint">Loading…</span>`}</div>
      <div class="hint">Areas are built and kept up to date on the <a href="#" data-act="tab" data-arg="kb">Knowledge base</a> tab. Earlier answers are only offered from the chosen areas.</div>
      <hr class="sep">
      ${toggle("Live web lookups", "Claude Code may also open pages on these domains while answering:", `data-s="allow_web_lookup" ${S.settings.allow_web_lookup ? "checked" : ""}`)}
      <textarea class="input" style="min-height:60px" data-s="web_domains">${esc((S.settings.web_domains || []).join("\n"))}</textarea>`)}
    ${pane("Material for this response only", "folder", `
      <div class="hint">Customer documents, A&amp;E specifications, site surveys: material that belongs to this tender. It is indexed on your PC for this response only. Material the whole team should reuse belongs in a knowledge area.</div>
      <div class="lbl">Folders</div>${folders || `<div class="hint">No folders yet.</div>`}
      <button class="add" data-act="addFolder"><span class="plus">+</span>Add folder</button>
      <div class="footer-actions"><span class="hint">Unchanged files are skipped.</span>
      <button class="btn primary" data-act="indexAll" ${p.folders.length ? "" : "disabled"}>Index folders now</button></div>
      <hr class="sep">
      <div class="lbl">Websites</div>${sites || `<div class="hint">No websites yet.</div>`}
      <button class="add" data-act="addSite"><span class="plus">+</span>Add website</button>
      <div class="footer-actions"><span></span><button class="btn primary" data-act="tab" data-arg="strategy">Next: product &amp; strategy →</button></div>`)}</div>`;
};

VIEWS.strategy = () => {
  const p = S.project;
  const chips = S.settings.products.map((pr, i) => `<button class="chip ${p.products.includes(pr.name) ? "on" : ""}" data-act="toggleProduct" data-arg="${i}" title="${esc(pr.notes || "")}">${p.products.includes(pr.name) ? "&#10003; " : ""}${esc(pr.name)}</button>`).join("");
  const files = (p.strategy_files || []).map((f, i) => `<div class="lrow site" style="grid-template-columns:28px 1fr 32px"><img src="${ICON("document")}" alt=""><div class="path" title="${esc(f)}">&lrm;${esc(f)}</div><button class="iconbtn" data-act="rmStratFile" data-arg="${i}">&#x2715;</button></div>`).join("");
  return `<div class="cols">${pane("Product", "checkmark-ok", `
      ${hero("What do we offer?", "Choose the product(s) the responder may use. Each answer states which product, module or licence meets the requirement.")}
      <div class="chips">${chips}</div>
      <div id="addprod" class="hidden"><div class="row2"><input class="input" id="pname" placeholder="Product name and version"><input class="input" id="pnotes" placeholder="Short note (optional)"></div>
      <div class="inline" style="margin-top:8px;justify-content:flex-end"><button class="btn secondary sm" data-act="cancelProduct">Cancel</button><button class="btn primary sm" data-act="saveProduct">Add product</button></div></div>
      <button class="add" data-act="addProduct"><span class="plus">+</span>Add product</button>
      <div class="field"><label>Product rules</label><textarea class="input" data-p="product_rules" placeholder="e.g. Answer for XProtect only. Do not offer BriefCam, even where analytics are asked for.">${esc(p.product_rules)}</textarea></div>
      <div class="field"><label>Extra instructions</label><textarea class="input" data-p="instructions" placeholder="e.g. Milestone is the vendor, not the integrator. Keep answers short. Use the 2026 R2 documentation.">${esc(p.instructions)}</textarea></div>`)}
    ${pane("Win strategy", "star-up", `
      <div class="field"><label>What the sales team wants to win on</label><textarea class="input" style="min-height:170px" data-p="strategy" placeholder="e.g. Customer worries about lock-in: stress open platform and ONVIF. Competitor is Genetec; highlight no per-client licences. Keep the cloud backup answer honest, it is our weak spot.">${esc(p.strategy)}</textarea></div>
      <div class="lbl">Sales input files (mails, notes, account plans)</div>${files}
      <button class="add" data-act="addStratFile"><span class="plus">+</span>Add sales input</button>
      <div class="msgbar info"><span>The strategy steers emphasis and wording. It never changes what the evidence supports.</span></div>
      <div class="footer-actions"><span></span><button class="btn primary" data-act="tab" data-arg="output">Next: output →</button></div>`)}</div>
    ${profilePane(p)}`;
};

function profilePane(p) {
  const pr = p.profile || {}, M = S.reportMeta || {};
  const sel = (path, val, list) => `<select class="input" data-p="${path}"><option value="">—</option>${(list || []).map(x => `<option ${x === val ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>`;
  return `<div style="margin-top:20px">${pane("Tender profile (for reporting)", "list", `
    <div class="hint">Colleagues in product management, security and marketing report on the collected requirements. This profile tells them what kind of tender it is, who evaluates it and why the customer runs it.</div>
    <div class="row4"><div class="field"><label>Vertical</label>${sel("profile.vertical", pr.vertical, M.verticals)}</div>
      <div class="field"><label>Country</label><input class="input" data-p="profile.country" value="${esc(pr.country || "")}"></div>
      <div class="field"><label>Region</label>${sel("profile.region", pr.region, M.regions)}</div>
      <div class="field"><label>Type of end customer</label><input class="input" data-p="profile.end_user_type" value="${esc(pr.end_user_type || "")}" placeholder="e.g. international airport"></div></div>
    <div class="row4"><div class="field"><label>Consultant / evaluator</label><input class="input" data-p="profile.consultant" value="${esc(pr.consultant || "")}"></div>
      <div class="field"><label>Partner / integrator</label><input class="input" data-p="profile.partner" value="${esc(pr.partner || "")}"></div>
      <div class="field"><label>Submission deadline</label><input class="input" type="date" data-p="profile.due_date" value="${esc(pr.due_date || "")}"></div>
      <div class="field"><label>Outcome</label>${sel("profile.outcome", pr.outcome || "Open", M.outcomes)}</div></div>
    <div class="row2"><div class="field"><label>Why the customer runs this tender</label><textarea class="input" data-p="profile.purpose" placeholder="e.g. replace an end-of-life VMS after a merger; new terminal">${esc(pr.purpose || "")}</textarea></div>
      <div class="field"><label>What concerns the customer most</label><textarea class="input" data-p="profile.concerns" placeholder="e.g. cybersecurity after an incident; vendor lock-in">${esc(pr.concerns || "")}</textarea></div></div>
    <div class="field"><label>Key themes (comma separated)</label><input class="input" id="themes" value="${esc((pr.themes || []).join(", "))}" placeholder="cybersecurity, open platform, redundancy"></div>
    <div class="footer-actions">${toggle("Share in the requirements register", "Off for confidential tenders: nothing from this response is shared for reporting.", `data-p="register_share" ${p.register_share !== false ? "checked" : ""}`)}
      <button class="btn secondary" data-act="suggestProfile" ${p.rows.length ? "" : "disabled"}>Suggest with Claude</button></div>`)}</div>`;
}

VIEWS.output = () => {
  const p = S.project, x = p.output.excel, w = p.output.word;
  const src = x.path || p.req_source.path;
  const marks = scale().map(s => `<div class="field"><label>${esc(s.label)} (${esc(s.code)})</label>${colSelect("output.excel.marks." + s.code, (x.marks || {})[s.code])}</div>`).join("");
  const scaleRows = scale().map((s, i) => `<tr><td><input class="input sm" style="width:50px" data-sc="${i}.code" value="${esc(s.code)}"></td><td><input class="input sm" data-sc="${i}.label" value="${esc(s.label)}"></td>
      <td><input class="input sm" data-sc="${i}.meaning" value="${esc(s.meaning || "")}"></td><td><input type="color" data-sc="${i}.color" value="${esc(s.color || "#ffffff")}"></td><td><button class="iconbtn" data-act="rmScale" data-arg="${i}">&#x2715;</button></td></tr>`).join("");
  return `<div class="cols even">${pane("Write back into Excel", "list", `
      ${toggle("Fill the customer's workbook", "Answers go into the columns you choose. The original file is not changed; a new version is saved.", `data-p="output.excel.enabled" data-rerender="1" ${x.enabled ? "checked" : ""}`)}
      <div class="${x.enabled ? "" : "hidden"}" style="display:grid;gap:14px">
      <div class="field"><label>Workbook</label><div class="inline"><input class="input" readonly value="${esc(src)}" placeholder="Same as the requirements file"><button class="btn tertiary" data-act="pickOutXlsx">Browse…</button></div></div>
      <div class="row2"><div class="field"><label>Sheet</label><input class="input" data-p="output.excel.sheet" value="${esc(x.sheet || p.req_source.sheet || "")}"></div>
      <div class="field"><label>Rows</label><input class="input" readonly value="Same rows as the requirements"></div></div>
      <div class="lbl">How is compliance marked?</div>
      <div class="radios"><label class="radio ${x.mode !== "marks" ? "on" : ""}"><input type="radio" name="xm" value="text" data-p="output.excel.mode" data-rerender="1" ${x.mode !== "marks" ? "checked" : ""}><b>Text in one column</b><span>e.g. “Compliant” in column E</span></label>
      <label class="radio ${x.mode === "marks" ? "on" : ""}"><input type="radio" name="xm" value="marks" data-p="output.excel.mode" data-rerender="1" ${x.mode === "marks" ? "checked" : ""}><b>A mark per level</b><span>e.g. an X under Fully / Widely / Not yet / Not supported</span></label></div>
      ${x.mode === "marks" ? `<div class="row4">${marks}</div><div class="row4"><div class="field"><label>Mark</label><input class="input" data-p="output.excel.mark_char" value="${esc(x.mark_char || "X")}"></div></div>`
        : `<div class="row2"><div class="field"><label>Compliance column</label>${colSelect("output.excel.compliance_col", x.compliance_col)}</div>
           <div class="field"><label>Write</label><select class="input" data-p="output.excel.use_label"><option value="true" ${x.use_label !== false && x.use_label !== "false" ? "selected" : ""}>Label (Compliant)</option><option value="" ${x.use_label === false || x.use_label === "" ? "selected" : ""}>Code (C)</option></select></div></div>`}
      <div class="row3">
        <div class="field"><label>Explanation</label>${colSelect("output.excel.explanation_col", x.explanation_col)}</div>
        <div class="field"><label>Product considered</label>${colSelect("output.excel.product_col", x.product_col)}</div>
        <div class="field"><label>Links</label>${colSelect("output.excel.links_col", x.links_col)}</div>
        <div class="field"><label>Evidence quotes</label>${colSelect("output.excel.evidence_col", x.evidence_col)}</div>
        <div class="field"><label>Responsible (Milestone / Contractor …)</label>${colSelect("output.excel.owner_col", x.owner_col)}</div>
        <div class="field"><label>Confidence</label>${colSelect("output.excel.confidence_col", x.confidence_col)}</div>
        <div class="field"><label>Initials</label>${colSelect("output.excel.initials_col", x.initials_col)}</div>
        <div class="field"><label>Considerations (own column)</label>${colSelect("output.excel.considerations_col", x.considerations_col)}</div>
      </div>
      <div class="hint">No product column? The product is only named in front of an answer that relies on something other than the products chosen for the tender. Review notes are added as cell comments on rows that are not approved.</div>
      ${toggle("Add the considerations to the answers", "“Consider: …” on what the project should take into account. Without its own column it follows the explanation as a short paragraph (also in Word).", `data-p="include_considerations" ${p.include_considerations !== false ? "checked" : ""}`)}
      ${toggle("Overwrite cells that already have content", "", `data-p="output.excel.overwrite" ${x.overwrite ? "checked" : ""}`)}
      <div class="field"><label>Save as</label><div class="inline"><input class="input" data-p="output.excel.out_path" value="${esc(x.out_path || "")}" placeholder="Automatic: <workbook>_Response_v1.xlsx next to the original"><button class="btn tertiary" data-act="saveAs" data-arg="excel">Browse…</button></div></div>
      </div>`)}
    <div style="display:grid;gap:20px">${pane("Word compliance document", "document", `
      ${toggle("Create a Word response from a template", "For example the Milestone compliance matrix template.", `data-p="output.word.enabled" data-rerender="1" ${w.enabled ? "checked" : ""}`)}
      <div class="${w.enabled ? "" : "hidden"}" style="display:grid;gap:14px">
      <div class="field"><label>Template (.docx)</label><div class="inline"><input class="input" readonly value="${esc(w.template || "")}" placeholder="No template selected"><button class="btn tertiary" data-act="pickTemplate">Browse…</button></div></div>
      <div class="field"><label>Save as</label><div class="inline"><input class="input" data-p="output.word.out_path" value="${esc(w.out_path || "")}" placeholder="Automatic: next to the template"><button class="btn tertiary" data-act="saveAs" data-arg="word">Browse…</button></div></div>
      <details><summary class="small" style="cursor:pointer;color:var(--primary-hover)">Placeholders you can put in the template</summary><div class="hint" style="margin-top:8px">
      Anywhere: <code>{{project}}</code> <code>{{customer}}</code> <code>{{date}}</code> <code>{{author}}</code> <code>{{products}}</code> <code>{{summary}}</code><br>
      In one table row, repeated for every requirement: <code>{{id}}</code> <code>{{section}}</code> <code>{{requirement}}</code> <code>{{compliance_label}}</code> <code>{{explanation}}</code> <code>{{product}}</code> <code>{{responsible}}</code> <code>{{links}}</code> <code>{{evidence}}</code> <code>{{gaps}}</code> <code>{{question}}</code> <code>{{considerations}}</code><br>
      Without such a row, a compliance table is added at the end of the template.</div></details></div>`)}
    ${pane("Compliance levels", "checkmark-ok", `<div class="hint">Shared by all responses. Match them to the customer's scale if needed (e.g. Fully / Widely / Not yet / Not supported).</div>
      <table class="apollo"><thead><tr><th>Code</th><th>Label</th><th>Meaning for the responder</th><th>Colour</th><th></th></tr></thead><tbody>${scaleRows}</tbody></table>
      <button class="add" data-act="addScale"><span class="plus">+</span>Add level</button>
      <div class="field"><label>Responsible parties</label><input class="input" data-s="responsible_parties" value="${esc(parties().join(", "))}">
        <div class="hint">Comma-separated. Each answer names one of these as responsible, e.g. "Contractor" for installation, cabling or hardware. Map the Responsible column above to write it into Excel.</div></div>
      <div class="footer-actions"><span></span><button class="btn primary" data-act="tab" data-arg="respond">Next: respond →</button></div>`)}</div></div>`;
};

function wordRange(rs) {
  const o = S.outline;
  if (!o) return `<div class="hint">Reading the document outline…</div>`;
  if (!o.headings.length) return `<div class="hint">No headings found, so the whole document is read (${o.total} requirements).</div>`;
  const st = +(rs.start_heading ?? -1), sp = +(rs.stop_heading ?? -1);
  const label = h => `${"  ".repeat(Math.max(0, h.level - 1))}${esc(h.id)} ${esc(h.title)} (${h.count})`;
  const count = o.headings.filter(h => (st < 0 || h.idx >= st) && (sp < 0 || h.idx < sp)).reduce((a, h) => a + h.count, 0) + (st < 0 ? o.before_first : 0);
  return `<div class="lbl">Which part of the document?</div>
    <div class="row2"><div class="field"><label>Start at heading</label><select class="input" data-p="req_source.start_heading" data-rerender="1">
      <option value="-1">Beginning of the document</option>${o.headings.map(h => `<option value="${h.idx}" ${h.idx === st ? "selected" : ""}>${label(h)}</option>`).join("")}</select></div>
    <div class="field"><label>Stop before heading</label><select class="input" data-p="req_source.stop_heading" data-rerender="1">
      <option value="-1">End of the document</option>${o.headings.map(h => `<option value="${h.idx}" ${h.idx === sp ? "selected" : ""} ${st >= 0 && h.idx <= st ? "disabled" : ""}>${label(h)}</option>`).join("")}</select></div></div>
    <div class="hint">${count} of ${o.total} requirements are in this part. The number after each heading is how many requirements sit directly under it.</div>`;
}

function parties() {
  return (S.settings && S.settings.responsible_parties && S.settings.responsible_parties.length) ? S.settings.responsible_parties
    : ["Milestone", "Contractor", "Customer", "Third party", "Milestone + Contractor"];
}

function filtered() {
  const f = S.filter, rows = S.project.rows;
  let out;
  if (f === "all") out = rows;
  else if (f === "selected") out = rows.filter(r => r.selected);
  else if (f === "unanswered") out = rows.filter(r => !r.compliance);
  else if (f === "style") out = rows.filter(r => (r.style_flags || []).length);
  else if (f.startsWith("c:")) out = rows.filter(r => r.compliance === f.slice(2));
  else out = rows.filter(r => r.status === f);
  return sortRows(out);
}

// Sort the review table by a column. Document order is the default; rows without a value always go last.
const SORTS = {
  conf: r => r.confidence != null && r.confidence !== "" ? +r.confidence : null,
  comp: r => { const i = scale().findIndex(s => s.code === r.compliance); return i < 0 ? null : i; },
  status: r => ({ needs_review: 0, draft: 1, approved: 2 })[r.status] ?? null,
  resp: r => (r.responsible || "").toLowerCase() || null,
};
function sortRows(rows) {
  const s = S.sort;
  if (!s || !SORTS[s.key]) return rows;
  const key = SORTS[s.key], dir = s.dir === "desc" ? -1 : 1;
  return rows.map((r, i) => [r, key(r), i]).sort((a, b) => {
    if (a[1] === null || b[1] === null) return (a[1] === null) - (b[1] === null) || a[2] - b[2];
    return (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) * dir || a[2] - b[2];
  }).map(x => x[0]);
}
function sortTh(key, label, width, tip) {
  const on = S.sort && S.sort.key === key, arrow = on ? (S.sort.dir === "desc" ? " ▼" : " ▲") : "";
  return `<th style="width:${width};cursor:pointer;user-select:none" data-act="sortBy" data-arg="${key}" title="${esc(tip)}">${label}${arrow}</th>`;
}

VIEWS.respond = () => {
  const p = S.project, rows = p.rows;
  const cnt = k => rows.filter(r => r.status === k).length;
  const sel = rows.filter(r => r.selected);
  const pending = sel.filter(r => !r.compliance).length;
  const chips = [["all", `All ${rows.length}`], ["unanswered", `Unanswered ${rows.filter(r => !r.compliance).length}`], ["needs_review", `Needs review ${cnt("needs_review")}`], ["draft", `Draft ${cnt("draft")}`], ["approved", `Approved ${cnt("approved")}`], ["style", `Style issues ${rows.filter(r => (r.style_flags || []).length).length}`]]
    .concat(scale().map(s => ["c:" + s.code, `${s.label} ${rows.filter(r => r.compliance === s.code).length}`]))
    .map(([k, l]) => `<button class="chip ${S.filter === k ? "on" : ""}" data-act="filter" data-arg="${k}">${l}</button>`).join("");
  const list = filtered();
  const body = list.map(r => {
    const ev = r.evidence || [], ok = ev.filter(e => e.verified).length;
    const st = { approved: ["ok", "Approved"], draft: ["info", "Draft"], needs_review: ["warn", "Needs review"] }[r.status] || ["", "—"];
    const open = S.open.has(r.key);
    let html = `<tr class="${open ? "open" : ""}"><td><input type="checkbox" class="rowchk" data-key="${esc(r.key)}" ${S.checked.has(r.key) ? "checked" : ""}></td>
      <td>${esc(r.id)}</td><td class="req"><div class="clip">${esc(r.text)}</div></td><td>${compPill(r.compliance)}</td>
      <td class="small">${esc(r.responsible || "")}</td><td class="req small"><div class="clip">${esc(r.explanation || "")}</div></td>
      <td>${r.compliance ? `<span class="tag ${ok ? "ok" : "err"}" title="Verified quotes / quotes">${ok}/${ev.length}</span>` : ""}</td>
      <td class="small">${r.confidence != null ? r.confidence + "%" : ""}</td><td><span class="tag ${st[0]}">${st[1]}</span></td>
      <td class="nowrap"><button class="iconbtn" data-act="read" data-arg="${esc(r.key)}" title="Open in the reading pane">&#x2922;</button><button class="iconbtn" data-act="toggleOpen" data-arg="${esc(r.key)}" title="Details">${open ? "&#9652;" : "&#9662;"}</button></td></tr>`;
    if (open) html += `<tr class="detail"><td colspan="10">${detail(r)}</td></tr>`;
    return html;
  }).join("");
  const last = (p.runs || []).slice(-1)[0];
  return `${pane("Respond & review", "search-text", `
      <div class="toolbar">
        <button class="btn primary lg" data-act="run" data-arg="pending" ${pending ? "" : "disabled"}>Answer ${pending} unanswered</button>
        <button class="btn secondary" data-act="run" data-arg="checked">Answer checked again</button>
        <select class="input" style="width:150px" data-s="model">${["opus", "sonnet", "haiku"].map(m => `<option ${S.settings.model === m ? "selected" : ""}>${m}</option>`).join("")}</select>
        <span class="grow"></span>
        <button class="btn tertiary" data-act="showBrief" title="What Claude understood about the whole document before answering">Tender brief</button>
        <button class="btn tertiary" data-act="approveChecked">Approve checked</button>
        <button class="btn secondary" data-act="exportExcel" ${p.output.excel.enabled ? "" : "disabled"}><img src="${ICON("download")}" width="16" alt="">Write Excel</button>
        <button class="btn secondary" data-act="exportWord" ${p.output.word.enabled ? "" : "disabled"}><img src="${ICON("document")}" width="16" alt="">Write Word</button>
        <button class="btn accent" data-act="saveKb" title="Store approved answers in the knowledge base">Save approved to knowledge base</button>
      </div>
      <div class="chips">${chips}</div>
      ${last ? `<div class="hint">Last run ${esc(last.at)}: ${last.rows} requirements, model ${esc(last.model)}, cost $${last.cost_usd}${last.errors ? `, <span style="color:var(--W68)">${last.errors} failed batch(es)</span>` : ""}.</div>` : ""}
      <div class="tablewrap" style="max-height:calc(100vh - 330px)"><table class="apollo"><thead><tr><th style="width:34px"><input type="checkbox" data-act="checkAll"></th><th style="width:80px;cursor:pointer;user-select:none" data-act="sortBy" data-arg="" title="Document order">ID${S.sort ? "" : " ▲"}</th><th>Requirement</th>${sortTh("comp", "Compliance", "150px", "Sort by compliance level")}${sortTh("resp", "Responsible", "120px", "Sort by responsible party")}<th>Response</th><th style="width:70px">Evidence</th>${sortTh("conf", "Conf.", "70px", "Sort by confidence, least sure first. Click again to reverse, a third time for document order.")}${sortTh("status", "Status", "110px", "Sort by status, needs review first")}<th style="width:72px"></th></tr></thead>
      <tbody>${body || `<tr><td colspan="10"><div class="empty">Nothing here.</div></td></tr>`}</tbody></table></div>
      <datalist id="parties-list">${parties().map(x => `<option value="${esc(x)}">`).join("")}</datalist>`)}`;
};

function detail(r) {
  const ev = (r.evidence || []).map(e => `<div class="ev ${e.verified ? "ok" : "bad"}"><span class="tag ${e.verified ? "ok" : "warn"}">${e.verified ? "Quote verified" : "Not found in source"}</span>
      <q>${esc(e.quote)}</q><div class="src">${e.source && e.source.startsWith("http") ? `<a href="${esc(e.source)}" target="_blank" rel="noopener">${esc(e.source)}</a>` : `<a href="#" data-act="openPath" data-arg="${esc(e.source)}" title="${esc(e.source)}">${esc(e.source.split(/[\\/]/).pop())}</a>`}${e.where ? " · " + esc(e.where) : ""}</div></div>`).join("") || `<div class="hint">No evidence given.</div>`;
  const links = (r.links || []).map(l => `<div class="small"><span class="tag ${l.ok ? "ok" : "err"}">${l.ok ? "OK" : "Broken"}</span> <a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a></div>`).join("");
  const opts = scale().map(s => `<option value="${esc(s.code)}" ${s.code === r.compliance ? "selected" : ""}>${esc(s.label)}</option>`).join("");
  return `<div class="detail-grid"><div style="display:grid;gap:10px">
      <div class="small muted">${esc(r.section || "")}</div><div class="reqtext">${esc(r.text)}</div>
      ${r.context ? `<div class="hint"><b>Customer guidance:</b> ${esc(r.context)}</div>` : ""}
      <div class="row3"><div class="field"><label>Compliance</label><select class="input" data-row="${esc(r.key)}" data-f="compliance"><option value="">—</option>${opts}</select></div>
        <div class="field"><label>Product considered</label><input class="input" data-row="${esc(r.key)}" data-f="product" value="${esc(r.product || "")}"></div>
        <div class="field"><label>Responsible</label><input class="input" list="parties-list" data-row="${esc(r.key)}" data-f="responsible" value="${esc(r.responsible || "")}"></div></div>
      <div class="field"><label>Response</label><textarea class="input" data-row="${esc(r.key)}" data-f="explanation">${esc(r.explanation || "")}</textarea></div>
      <div class="field"><label>Consider (what the project should take into account, and why it matters)</label><textarea class="input" data-row="${esc(r.key)}" data-f="considerations">${esc(r.considerations || "")}</textarea></div>
      <div class="field"><label>Gaps / why not fully compliant</label><textarea class="input" data-row="${esc(r.key)}" data-f="gaps">${esc(r.gaps || "")}</textarea></div>
      <div class="field"><label>Question to the customer</label><textarea class="input" data-row="${esc(r.key)}" data-f="question">${esc(r.question || "")}</textarea></div>
      ${r.review_notes ? `<div class="msgbar warning"><span>${esc(r.review_notes)}</span></div>` : ""}
      ${(r.style_flags || []).length ? `<div class="hint">Style: ${(r.style_flags || []).map(f => `<span class="tag y">${esc({ restates: "repeats the requirement", same_opening: "same opening as others", product_opening: "opens with the product name" }[f] || f)}</span>`).join(" ")} · tick the row and use <b>Answer checked again</b>, or edit the text.</div>` : ""}
      <div class="inline"><button class="btn primary" data-act="setStatus" data-arg="${esc(r.key)}|approved">Approve</button><button class="btn secondary" data-act="setStatus" data-arg="${esc(r.key)}|draft">Back to draft</button>
        <button class="btn tertiary" data-act="runOne" data-arg="${esc(r.key)}">Answer again</button>${S.reading ? "" : `<button class="btn tertiary" data-act="read" data-arg="${esc(r.key)}">Reading pane</button>`}<span class="grow"></span>${r.confidence != null ? `<span class="muted small">Confidence ${r.confidence}%</span>` : ""}</div>
    </div><div><div class="lbl" style="margin-bottom:6px">Evidence</div>${ev}${links ? `<div class="lbl" style="margin:12px 0 6px">Links for the customer</div>${links}` : ""}</div></div>`;
}

VIEWS.kb = () => `<div class="cols">${pane("Knowledge areas", "book", `
    ${hero("What the responder knows", "One area per product or topic. Each area is built from its folders and websites and published to the shared folder; everybody searches the latest published version.")}
    <div class="hint">Shared folder: <code>${esc(S.kbRoot || S.settings.kb_root || "")}</code> · change it in Settings.</div>
    <div id="kbmigrate"></div>
    <div id="kbareas" class="areas"><div class="hint">Loading…</div></div>
    <div class="inline"><input class="input" id="newarea" placeholder="New area, e.g. XProtect Rapid REVIEW"><button class="btn secondary" data-act="kbNewArea">Add area</button></div>`)}
  ${pane("Search", "search-text", `
    <div class="chips" id="kbsareas"></div>
    <div class="inline"><input class="input" id="kbq" placeholder="Try a requirement, e.g. 'redundant management server failover'"><button class="btn primary" data-act="kbSearch">Search</button></div>
    <div id="kbhits"></div>
    <hr class="sep"><div class="toolbar"><div class="lbl">Earlier answers</div><span class="grow"></span><input class="input sm" style="max-width:240px" id="kbrq" placeholder="Filter"><button class="btn sm tertiary" data-act="kbResponses">Show</button></div>
    <div id="kbresp"></div>`)}</div>`;

function areaCard(a) {
  const lk = a.lock;
  const busy = lk && !lk.stale;
  const srcs = a.sources.map((s, i) => s.type === "sql"
    ? `<div class="lrow"><img src="${ICON("list")}" alt=""><div class="path" title="${esc(s.query)}">&lrm;<b>${esc(s.name)}</b> · ${esc(s.server)}/${esc(s.database)} · ${s.auth === "sql" ? esc(s.user) : "Windows sign-in"}</div>
        <button class="btn sm tertiary" data-act="kbSqlForm" data-arg="${a.slug}|${i}" ${busy ? "disabled" : ""}>Edit</button><button class="iconbtn" title="Remove source and its rows" data-act="kbRmSource" data-arg="${a.slug}|${i}">&#x2715;</button></div>`
    : s.type === "folder"
    ? `<div class="lrow"><img src="${ICON("folder")}" alt=""><div class="path" title="${esc(s.path)}">&lrm;${esc(s.path)}</div>
        ${s.exists ? "" : `<span class="tag warn" title="Not found on this PC. Sync the SharePoint library, or ask whoever added it.">not on this PC</span>`}<button class="iconbtn" title="Remove source and its documents" data-act="kbRmSource" data-arg="${a.slug}|${i}">&#x2715;</button></div>`
    : `<div class="lrow site"><img src="${ICON("world")}" alt=""><div class="path" title="${esc(s.url)}">&lrm;${esc(s.url)}</div>
        <input class="input sm" type="number" min="1" max="20000" value="${s.max_pages || 200}" title="Max pages" data-src="${a.slug}|${i}"><button class="iconbtn" title="Remove source and its pages" data-act="kbRmSource" data-arg="${a.slug}|${i}">&#x2715;</button></div>`).join("");
  return `<div class="area ${busy ? "locked" : ""}">
    <div class="area-h"><b>${esc(a.name)}</b>
      ${a.published ? `<span class="tag info">v${a.version} · ${fmtN(a.documents)} docs · ${fmtN(a.chunks)} passages · ${a.size_mb ?? "?"} MB</span>` : `<span class="tag y">not built yet</span>`}
      ${a.built_at ? `<span class="muted small">${esc(a.built_by || "")} · ${esc(a.built_at)}</span>` : ""}
      ${lk ? `<span class="tag ${busy ? "warn" : "y"}">${busy ? `${esc(lk.user)} is ${esc(lk.what || "changing it")} since ${esc(lk.since)}` : "abandoned lock"}</span>
        <button class="textbtn" data-act="kbUnlock" data-arg="${a.slug}" title="Only if you are sure nobody is working on it">Release lock</button>` : ""}
      <span class="grow"></span>
      <button class="btn sm primary" data-act="kbUpdate" data-arg="${a.slug}" ${busy || !a.sources.length ? "disabled" : ""} title="Read the sources again; changed files are re-indexed, deleted files removed">Update</button>
      <button class="btn sm secondary" data-act="kbRebuild" data-arg="${a.slug}" ${busy || !a.sources.length ? "disabled" : ""} title="Start from an empty area, e.g. after a new release">Rebuild</button>
      <button class="btn sm tertiary" data-act="kbDocs" data-arg="${a.slug}" ${a.published ? "" : "disabled"}>Documents</button>
      <button class="btn sm tertiary" data-act="kbRollback" data-arg="${a.slug}" ${a.has_prev && !busy ? "" : "disabled"} title="Go back to the version before the last change">Roll back</button></div>
    ${a.description ? `<div class="hint">${esc(a.description)}</div>` : ""}
    ${srcs || `<div class="hint">No sources yet. Add the folders and documentation websites that hold the facts for ${esc(a.name)}.</div>`}
    <div class="addrow"><button class="btn sm tertiary" data-act="kbAddFolder" data-arg="${a.slug}" ${busy ? "disabled" : ""}><img src="${ICON("folder")}" width="14" alt=""> Add folder</button>
      <input class="input sm" id="url-${a.slug}" placeholder="https://doc.milestonesys.com/…/2026r2/en/"><input class="input sm" id="max-${a.slug}" type="number" value="300" title="Max pages">
      <button class="btn sm tertiary" data-act="kbAddSite" data-arg="${a.slug}" ${busy ? "disabled" : ""}><img src="${ICON("world")}" width="14" alt=""> Add website</button></div>
    <div><button class="btn sm tertiary" data-act="kbSqlForm" data-arg="${a.slug}" ${busy ? "disabled" : ""}><img src="${ICON("list")}" width="14" alt=""> Add database (MS SQL)</button></div>
  </div>`;
}

function migrateBox(lg) {
  if (!lg || !lg.exists || !(lg.collections || []).length && !lg.responses) return "";
  const opts = sel => `<option value="">Do not import</option>` + (S.areas || []).map(a => `<option value="${esc(a.slug)}" ${a.slug === sel ? "selected" : ""}>${esc(a.name)}</option>`).join("");
  return `<div class="migrate"><b>Import from the old knowledge base</b>
    <div class="hint">Found <code>${esc(lg.path)}</code> with ${lg.collections.length} collections and ${lg.responses} earlier answers. Pick an area for each collection. The content is copied (nothing is read again), and each folder or website becomes a source of its area, so later updates work.</div>
    <div class="tablewrap" style="max-height:260px"><table class="apollo"><thead><tr><th>Collection</th><th style="width:90px">Docs</th><th style="width:220px">Area</th></tr></thead><tbody>
    ${lg.collections.map((c, i) => `<tr><td class="small"><img src="${ICON(c.kind === "web" ? "world" : "folder")}" width="14" alt=""> ${esc(c.collection)}</td><td class="small">${c.docs}</td><td><select class="input sm" data-mig="${i}">${opts(c.suggested)}</select></td></tr>`).join("")}
    </tbody></table></div>
    <label class="check small"><input type="checkbox" id="migans" checked>Import the ${lg.responses} earlier answers (area guessed from the product)</label>
    <div class="inline" style="justify-content:flex-end"><button class="textbtn" data-act="kbMigDismiss">Not now, hide this</button><button class="btn primary" data-act="kbMigrate">Import</button></div></div>`;
}

function sqlFormValues() {
  return { type: "sql", name: $("#sq-name").value.trim(), server: $("#sq-server").value.trim(), database: $("#sq-database").value.trim(),
    auth: $("#sq-auth").value, user: $("#sq-user").value.trim(), query: $("#sq-query").value.trim(),
    title_col: $("#sq-title").value, key_col: $("#sq-key").value, trust_cert: true };
}

async function loadKbView() {
  const d = await loadAreas();
  if (!d) return;
  $("#kbmigrate") && ($("#kbmigrate").innerHTML = migrateBox(S.legacy));
  const el = $("#kbareas");
  if (el) el.innerHTML = S.areas.map(areaCard).join("");
  S.kbSearch = S.kbSearch || new Set(S.areas.map(a => a.slug));
  const sa = $("#kbsareas");
  if (sa) sa.innerHTML = S.areas.map(a => `<button class="chip ${S.kbSearch.has(a.slug) ? "on" : ""}" data-act="kbSearchArea" data-arg="${esc(a.slug)}">${S.kbSearch.has(a.slug) ? "&#10003; " : ""}${esc(a.name)}</button>`).join("");
}

// ------------------------------------------------------------------ reporting
const RF = () => (S.rep = S.rep || { preset: null, filters: {}, rows: [], offset: 0, open: new Set() });

VIEWS.report = () => {
  const R = RF(), M = S.reportMeta;
  if (!M) return `<div class="empty">Loading…</div>`;
  const P = M.presets, f = R.filters, o = M.options;
  const sel = (k, label, list, val) => `<div class="field"><label>${label}</label><select class="input sm" data-rf="${k}"><option value="">All</option>${(list || []).map(x => typeof x === "object" ? `<option value="${esc(x.id)}" ${x.id === val ? "selected" : ""}>${esc(x.name)}${x.customer ? " · " + esc(x.customer) : ""}</option>` : `<option ${x === val ? "selected" : ""}>${esc(x)}</option>`).join("")}</select></div>`;
  const compOpts = `<option value="">Any</option><option value="gaps" ${f.compliance === "gaps" ? "selected" : ""}>Not fully compliant</option>` + scale().map(x => `<option value="${esc(x.code)}" ${f.compliance === x.code ? "selected" : ""}>${esc(x.label)}</option>`).join("");
  const presets = Object.entries(P).map(([k, v]) => `<button class="chip ${R.preset === k ? "on" : ""}" data-act="repPreset" data-arg="${k}" title="${esc(v.description)}">${esc(v.label)}</button>`).join("");
  return `${pane("Reporting", "star-up", `
    <div class="toolbar"><div class="chips">${presets}</div><span class="grow"></span>
      <button class="btn sm tertiary" data-act="repRefresh" title="Read the latest published responses">Refresh</button>
      <button class="btn sm secondary" data-act="repExport" data-arg="xlsx"><img src="${ICON("download")}" width="14" alt=""> Excel</button>
      <button class="btn sm tertiary" data-act="repExport" data-arg="csv">CSV</button><button class="btn sm tertiary" data-act="repExport" data-arg="json">JSON</button></div>
    <div class="hint">${esc(P[R.preset]?.description || "")}</div>
    <div class="repfilters">
      <div class="field"><label>Search requirements</label><input class="input sm" data-rf="q" value="${esc(f.q || "")}" placeholder="e.g. encryption at rest"></div>
      <div class="field"><label>Compliance</label><select class="input sm" data-rf="compliance">${compOpts}</select></div>
      ${sel("topic", "Topic", o.topics, f.topic)}${sel("product", "Product", o.products, f.product)}
      ${sel("vertical", "Vertical", o.verticals, f.vertical)}${sel("region", "Region", o.regions, f.region)}
      ${sel("country", "Country", o.countries, f.country)}${sel("outcome", "Outcome", o.outcomes, f.outcome)}
      ${sel("tender", "Tender", o.tenders, f.tender)}
      <div class="field"><label>From</label><input class="input sm" type="date" data-rf="since" value="${esc(f.since || "")}"></div>
      <div class="field"><label>To</label><input class="input sm" type="date" data-rf="until" value="${esc(f.until || "")}"></div>
      <div class="field"><label>&nbsp;</label><label class="check small"><input type="checkbox" data-rf="security" ${f.security ? "checked" : ""}>Security-relevant only</label></div>
      <div class="field"><label>&nbsp;</label><label class="check small"><input type="checkbox" data-rf="approved_only" ${f.approved_only ? "checked" : ""}>Approved answers only</label></div>
      <div class="field"><label>&nbsp;</label><button class="textbtn" data-act="repClear">Clear filters</button></div>
    </div>
    <div id="repbody"><div class="hint">Loading…</div></div>`)}
  <div class="cols even" style="margin-top:20px">${pane("Build a report with Claude", "search-text", `
    <div class="hint">Claude reads the requirements that match the filters above and writes a report for the chosen audience, with counts and examples. It is saved for colleagues in the shared folder (Word and Markdown).</div>
    <div class="field"><label>What do you want to know? (optional)</label><textarea class="input" id="repq" placeholder="${esc(({ product: "e.g. Which missing capabilities cost us most in airport tenders this year?", security: "e.g. Which security standards do customers ask for, and where are we not covered?", marketing: "e.g. Why do city surveillance customers tender, and what worries them?" })[R.preset] || "e.g. Summarise what customers ask about cloud and hosting.")}"></textarea></div>
    <div class="footer-actions"><span class="hint">Uses your own Claude Code sign-in, like answering.</span><button class="btn primary" data-act="repClaude">Build report with Claude</button></div>`)}
    ${pane("Reports", "document", `<div id="replist">${reportList(M.reports)}</div>`)}</div>`;
};

function reportList(list) {
  return (list || []).length ? (list || []).map(r => `<div class="lrow site" style="grid-template-columns:28px minmax(0,1fr) auto auto"><img src="${ICON("document")}" alt=""><div class="path" title="${esc(r.name)}">&lrm;${esc(r.name)}</div>
    <button class="btn sm tertiary" data-act="repView" data-arg="${esc(r.md)}">View</button>${r.docx ? `<button class="btn sm tertiary" data-act="openPath" data-arg="${esc(r.docx)}">Word</button>` : ""}</div>`).join("")
    : `<div class="hint">No reports yet.</div>`;
}

async function loadReport(keepRows) {
  const R = RF();
  if (!S.reportMeta) { const m = await safe(() => api("/api/report/meta")); if (!m) return; S.reportMeta = m; if (R.preset) return render(); }
  if (!R.preset) { R.preset = S.settings.report_role || "product"; R.filters = { ...(S.reportMeta.presets[R.preset]?.filters || {}) }; return render(); }
  const d = await safe(() => api("/api/report/summary", { preset: "all", filters: R.filters, refresh: R.refresh }));
  R.refresh = false;
  if (!d) return;
  R.summary = d;
  if (!keepRows) { const rr = await safe(() => api("/api/report/rows", { preset: "all", filters: R.filters, limit: 200 })); R.rows = rr ? rr.rows : []; R.offset = R.rows.length; }
  const el = $("#repbody"); if (el) el.innerHTML = reportBody();
}

function bars(items, key, opts = {}) {
  // one series, one hue: magnitude by length; value printed at the end; click filters
  const list = (items || []).filter(x => x.n);
  if (!list.length) return `<div class="hint">No data.</div>`;
  const max = Math.max(...list.map(x => x.n));
  return `<div class="hbars">${list.map(x => `<div class="hbar ${key ? "click" : ""}" ${key && x.k !== "(not set)" ? `data-act="repSet" data-arg="${key}|${esc(x.k)}"` : ""} title="${esc(x.k)}: ${x.n} requirements${x.tenders != null ? ` in ${x.tenders} tenders` : ""}">
    <span class="lab">${esc(x.k)}</span><span class="track"><i style="width:${Math.max(2, Math.round(x.n / max * 100))}%"></i></span><span class="val">${x.n}${x.tenders != null && opts.tenders ? ` <span class="muted">/ ${x.tenders}</span>` : ""}</span></div>`).join("")}</div>`;
}

function compMix(counts, label) {
  // stacked bar of the compliance scale, segment colours from the scale, labels in the legend
  const tot = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!tot) return "";
  const seg = scale().filter(x => counts[x.code]).map(x => `<i style="flex:${counts[x.code]};background:${esc(x.color)}" title="${esc(x.label)}: ${counts[x.code]} (${Math.round(counts[x.code] / tot * 100)}%)"></i>`).join("");
  return `<div class="mixrow" title="${esc(label || "")}">${label != null ? `<span class="lab">${esc(label)}</span>` : ""}<span class="mix">${seg}</span><span class="val">${tot}</span></div>`;
}
function compLegend() {
  return `<div class="legend">${scale().map(x => `<span><i style="background:${esc(x.color)}"></i>${esc(x.label)}</span>`).join("")}</div>`;
}

function reportBody() {
  const R = RF(), d = R.summary;
  if (!d.total) return `<div class="empty">No collected requirements match. Responses appear here once they have answers and are shared in the register (Product &amp; strategy → Tender profile).</div>`;
  const cc = Object.fromEntries(d.compliance.map(x => [x.k, x.n]));
  const gapCodes = scale().filter(x => !/^(compliant|ack|fully|not applicable)/i.test(x.label)).map(x => x.code);
  const gaps = gapCodes.reduce((a, c) => a + (cc[c] || 0), 0);
  const tiles = `<div class="stat"><div><b>${fmtN(d.total)}</b><span>requirements</span></div><div><b>${fmtN(d.tenders_n)}</b><span>tenders</span></div>
    <div><b>${Math.round(gaps / d.total * 100)}%</b><span>not fully compliant</span></div><div><b>${fmtN(d.security)}</b><span>security-relevant</span></div></div>`;
  const topicMix = Object.entries(d.topic_mix).sort((a, b) => Object.values(b[1]).reduce((x, y) => x + y, 0) - Object.values(a[1]).reduce((x, y) => x + y, 0))
    .slice(0, 14).map(([t, c]) => `<div class="click" data-act="repSet" data-arg="topic|${esc(t)}">${compMix(c, t)}</div>`).join("");
  const themes = {};
  d.tenders.forEach(t => JSON.parse(t.themes || "[]").forEach(x => { const k = x.toLowerCase(); themes[k] = (themes[k] || 0) + 1; }));
  const themeItems = Object.entries(themes).sort((a, b) => b[1] - a[1]).slice(0, 15).map(([k, n]) => ({ k, n }));
  const box = (t, body, w = "") => `<div class="repbox ${w}"><div class="lbl">${t}</div>${body}</div>`;
  let charts;
  if (R.preset === "security") charts = box("Standards and regulations requested", bars(d.standards, "q")) + box("Compliance by topic", topicMix + compLegend()) + box("Products", bars(d.products, "product")) + box("Why not compliant", bars(d.gap_types.filter(x => x.k !== "(not set)"), "gap_type"));
  else if (R.preset === "marketing") charts = box("Verticals", bars(d.verticals, "vertical", { tenders: true })) + box("Regions", bars(d.regions, "region")) + box("Countries", bars(d.countries, "country")) + box("Consultants / evaluators", bars(d.consultants)) + box("Partners / integrators", bars(d.partners)) + box("Outcomes", bars(d.outcomes, "outcome")) + box("Recurring themes (tenders)", bars(themeItems)) + box("What is asked for (topics)", bars(d.topics, "topic"));
  else charts = box("Compliance by topic", topicMix + compLegend(), "wide") + box("Why not compliant", bars(d.gap_types.filter(x => x.k !== "(not set)"), "gap_type")) + box("Products", bars(d.products, "product")) + box("Verticals", bars(d.verticals, "vertical", { tenders: true }));
  const tenders = R.preset === "marketing" ? `<div class="lbl" style="margin-top:14px">Tenders</div><div class="tablewrap" style="max-height:340px"><table class="apollo"><thead><tr><th>Tender</th><th>Vertical / country</th><th>Consultant · partner</th><th>Why the tender</th><th>Customer concerns</th><th>Outcome</th></tr></thead><tbody>
    ${d.tenders.map(t => `<tr><td class="small"><b>${esc(t.name)}</b><br><span class="muted">${esc(t.customer || "")} · ${esc(t.owner || "")}</span></td><td class="small">${esc(t.vertical || "—")}<br><span class="muted">${esc([t.country, t.region].filter(Boolean).join(", "))}</span></td><td class="small">${esc(t.consultant || "—")}<br><span class="muted">${esc(t.partner || "")}</span></td><td class="req small"><div class="clip">${esc(t.purpose || "")}</div></td><td class="req small"><div class="clip">${esc(t.concerns || "")}</div></td><td class="small">${esc(t.outcome || "")}</td></tr>`).join("")}</tbody></table></div>` : "";
  const rows = R.rows.map((r, i) => {
    const k = r.tender_id + "|" + r.key, open = R.open.has(k);
    return `<tr class="${open ? "open" : ""}"><td class="small">${esc(r.tender)}<br><span class="muted">${esc(r.rid || "")}</span></td><td class="req small"><div class="clip">${esc(r.text)}</div></td><td>${compPill(r.compliance)}</td>
      <td class="small">${esc(r.product || "")}</td><td class="small">${esc(r.topic || "")}${r.security ? ` <span class="tag warn" title="Security-relevant">sec</span>` : ""}</td><td class="req small"><div class="clip">${esc(r.gap_type ? r.gap_type + ": " : "")}${esc(r.gaps || "")}</div></td>
      <td><button class="iconbtn" data-act="repRow" data-arg="${esc(k)}">${open ? "&#9652;" : "&#9662;"}</button></td></tr>` +
      (open ? `<tr class="detail"><td colspan="7"><div class="detail-grid"><div style="display:grid;gap:8px"><div class="small muted">${esc(r.section || "")} · ${esc(r.customer || "")} · ${esc(r.vertical || "")} ${esc(r.country || "")}</div><div class="reqtext">${esc(r.text)}</div>
        ${JSON.parse(r.standards || "[]").length ? `<div class="small">Standards: ${JSON.parse(r.standards).map(x => `<span class="tag info">${esc(x)}</span>`).join(" ")}</div>` : ""}</div>
        <div style="display:grid;gap:8px"><div><div class="lbl">Response</div><div class="small">${esc(r.explanation || "")}</div></div>${r.gaps ? `<div><div class="lbl">Gaps / why not fully compliant</div><div class="small">${esc(r.gaps)}</div></div>` : ""}${r.question ? `<div><div class="lbl">Question to the customer</div><div class="small">${esc(r.question)}</div></div>` : ""}
        <div class="small muted">${esc(r.status || "")}${r.approved_by ? " by " + esc(r.approved_by) : ""} · ${esc(r.answered_at || "")}</div></div></div></td></tr>` : "");
  }).join("");
  return `${tiles}<div class="lbl">Compliance</div>${compMix(cc)}${compLegend()}
    <div class="repgrid">${charts}</div>${tenders}
    <div class="toolbar" style="margin-top:14px"><div class="lbl">Requirements</div><span class="muted small">${R.rows.length} of ${fmtN(d.total)} shown · click a bar to filter</span></div>
    <div class="tablewrap" style="max-height:560px"><table class="apollo"><thead><tr><th style="width:170px">Tender / ID</th><th>Requirement</th><th style="width:140px">Compliance</th><th style="width:130px">Product</th><th style="width:150px">Topic</th><th>Why not fully compliant</th><th style="width:40px"></th></tr></thead><tbody>${rows}</tbody></table></div>
    ${R.rows.length < d.total ? `<div class="inline" style="justify-content:center;margin-top:8px"><button class="btn sm secondary" data-act="repMore">Show more</button></div>` : ""}`;
}

function mdHtml(md) {
  const inline = t => esc(t).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [], L = md.split(/\r?\n/);
  for (let i = 0; i < L.length; i++) {
    const l = L[i];
    if (/^\s*\|/.test(l)) {
      const b = []; while (i < L.length && /^\s*\|/.test(L[i])) b.push(L[i++]); i--;
      const rows = b.filter(x => !/^[\s|:\-]+$/.test(x)).map(x => x.trim().replace(/^\||\|$/g, "").split("|").map(c => inline(c.trim())));
      out.push(`<table class="apollo"><thead><tr>${rows[0].map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    } else if (/^#{1,4}\s/.test(l)) { const n = l.match(/^#+/)[0].length; out.push(`<h${n + 1}>${inline(l.replace(/^#+\s*/, ""))}</h${n + 1}>`); }
    else if (/^\s*[-*•]\s+/.test(l)) out.push(`<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`);
    else if (/^\s*\d+[.)]\s+/.test(l)) out.push(`<li class="num">${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
    else if (l.trim()) out.push(`<p>${inline(l)}</p>`);
  }
  return `<div class="mdview">${out.join("")}</div>`;
}

VIEWS.settings = () => {
  const s = S.settings;
  return `<div class="cols even">${pane("You and the knowledge base", "settings", `
      <div class="row2"><div class="field"><label>Your name</label><input class="input" data-s="user_name" value="${esc(s.user_name)}"></div>
      <div class="field"><label>Initials</label><input class="input" data-s="user_initials" value="${esc(s.user_initials)}"></div></div>
      <div class="field"><label>Shared knowledge folder</label><div class="inline"><input class="input" readonly value="${esc(s.kb_root)}"><button class="btn tertiary" data-act="pickKbRoot">Choose folder…</button></div>
      <div class="hint">For a team: a folder in the Global Bid Desk SharePoint library, synced to this PC with OneDrive. Everybody who points to the same folder uses the same knowledge areas and earlier answers. The databases are never opened on SharePoint itself: each area is published as one file and read from a local copy, so syncing is safe.</div></div>
      <details><summary class="small" style="cursor:pointer">SharePoint libraries synced to this ${S.platform === "mac" ? "Mac" : "PC"} (${(S.libraries || []).length})</summary>
        <div class="hint" style="margin-top:6px">Files in these libraries are stored by library name, so a source added on a Windows PC is found on a Mac and the other way round. A library that is missing here: sync it with OneDrive (on a Mac: Sync, or Add shortcut to My files), or add its folder below.</div>
        <div class="small" style="max-height:160px;overflow:auto;margin:6px 0">${(S.libraries || []).map(l => `<div><b>${esc(l.name)}</b> <span class="muted">${esc(l.path)}</span></div>`).join("") || `<span class="muted">None found.</span>`}</div>
        <div class="field"><label>Extra library folders (one per line)</label><textarea class="input" style="min-height:50px" data-s="extra_libraries">${esc((s.extra_libraries || []).join("\n"))}</textarea></div></details>
      <div class="field"><label>My reporting focus</label><select class="input" data-s="report_role">${[["product", "Product management"], ["security", "Security & compliance"], ["marketing", "Marketing"], ["all", "All requirements"]].map(([v, l]) => `<option value="${v}" ${s.report_role === v ? "selected" : ""}>${l}</option>`).join("")}</select><div class="hint">The Reporting tab opens on this view.</div></div>
      <hr class="sep">
      ${toggle("Strict evidence", "Flag every answer whose quotes cannot be found word for word in the cited source, or whose links do not resolve.", `data-s="strict_evidence" ${s.strict_evidence ? "checked" : ""}`)}
      <div class="row2"><div class="field"><label>Knowledge base passages per requirement</label><input class="input" type="number" min="0" max="30" data-s="kb_hits" value="${s.kb_hits}"></div>
      <div class="field"><label>Earlier answers per requirement</label><input class="input" type="number" min="0" max="10" data-s="precedent_hits" value="${s.precedent_hits}"></div></div>`)}
    ${pane("Claude Code", "star-up", `
      <div class="field"><label>Claude Code executable</label><div class="inline"><input class="input" data-s="claude_path" value="${esc(s.claude_path)}"><button class="btn tertiary" data-act="testClaude">Test</button></div><div class="hint" id="claudetest">Uses your own Claude Code sign-in. "claude" finds it automatically.</div></div>
      <div class="row2"><div class="field"><label>Model</label><select class="input" data-s="model">${["opus", "sonnet", "haiku"].map(m => `<option ${s.model === m ? "selected" : ""}>${m}</option>`).join("")}</select></div>
      <div class="field"><label>Effort</label><select class="input" data-s="effort">${["low", "medium", "high", "xhigh", "max"].map(m => `<option ${s.effort === m ? "selected" : ""}>${m}</option>`).join("")}</select></div></div>
      <div class="row3"><div class="field"><label>Requirements per call</label><input class="input" type="number" min="1" max="20" data-s="batch_size" value="${s.batch_size}"></div>
      <div class="field"><label>Calls in parallel</label><input class="input" type="number" min="1" max="6" data-s="parallel" value="${s.parallel}"></div>
      <div class="field"><label>Timeout per call (min)</label><input class="input" type="number" min="2" max="90" data-s="timeout_min" value="${s.timeout_min}"></div></div>
      <div class="hint">Claude Code runs with read-only tools (Read, Grep, Glob) on your source folders and, if allowed, web fetch on the listed domains. It cannot change files. Prompts and answers of every run are kept under <code>${esc(S.dataDir || "")}</code> (runs) for traceability.</div>
      <div class="hint">Version ${esc(S.version)}</div>`)}</div>`;
};

// ------------------------------------------------------------------ actions
const ACT = {
  tab: t => { S.tab = t; render(); },
  help: topic => showHelp(topic || HELP_FOR_TAB[S.tab] || "start"),
  helpTopic: t => showHelp(t),
  newProject: async () => {
    const name = $("#newname").value.trim() || "New response";
    const d = await safe(() => api("/api/project/new", { name }));
    if (d) { S.project = d.project; S.preview = null; S.projCols = []; S.tab = "requirements"; await refreshState(); render(); }
  },
  openProject: async id => {
    const d = await safe(() => api("/api/project/get", { id }));
    if (!d) return;
    S.project = d.project; S.preview = null; S.open.clear(); S.checked.clear();
    S.tab = d.project.rows.length ? "respond" : "requirements";
    S.outline = null;
    if (/\.(xls[xm]|docx)$/i.test(d.project.req_source.path || "")) await loadPreview(false);
    await loadProjectSources();
    if (!S.reportMeta) S.reportMeta = await api("/api/report/meta").catch(() => null);
    render();
  },
  pickReq: async () => {
    const p = await safe(() => browse("file", "requirements", "Requirements file"));
    if (!p) return;
    S.project.req_source.path = p; S.project.req_source.kind = p.toLowerCase().endsWith(".docx") ? "word" : "excel";
    await saveProject();
    S.outline = null; S.project.req_source.start_heading = -1; S.project.req_source.stop_heading = -1;
    await loadPreview(S.project.req_source.kind === "excel");
  },
  loadReq: async () => {
    await saveProject();
    const d = await safe(() => api("/api/req/load", { id: S.project.id, spec: S.project.req_source }));
    if (d) { S.project = d.project; msg(`${d.project.rows.length} requirements loaded`, "success"); render(); }
  },
  selAll: v => { S.project.rows.forEach(r => r.selected = v === "1"); pushRows(S.project.rows.map(r => ({ key: r.key, selected: r.selected }))); render(); },
  addFolder: async () => {
    const p = await safe(() => browse("folder", "", "Folder with background material"));
    if (p && !S.project.folders.some(f => f.path === p)) { S.project.folders.push({ path: p, label: "", index: true }); await saveProject(); render(); }
  },
  rmFolder: async i => { S.project.folders.splice(+i, 1); await saveProject(); render(); },
  indexAll: async () => {
    await saveProject();
    for (const f of S.project.folders.filter(f => f.index !== false)) {
      const d = await safe(() => api("/api/project/ingest", { id: S.project.id, path: f.path }));
      if (d) await watchJob(d.job);
    }
    await loadProjectSources(); render();
  },
  addSite: async () => { S.project.websites.push({ url: "", max_pages: 200 }); await saveProject(); render(); },
  rmSite: async i => { S.project.websites.splice(+i, 1); await saveProject(); render(); },
  crawl: async i => {
    const w = S.project.websites[+i];
    if (!w.url) return msg("Enter the website address first", "warning");
    await saveProject();
    const d = await safe(() => api("/api/project/crawl", { id: S.project.id, url: w.url, max_pages: w.max_pages || 200 }));
    if (d) { await watchJob(d.job); await loadProjectSources(); render(); }
  },
  toggleArea: async slug => {
    const a = [...projAreas(S.project)];
    const k = a.indexOf(slug); k >= 0 ? a.splice(k, 1) : a.push(slug);
    S.project.areas = a; await saveProject(); render();
  },
  toggleProduct: async i => {
    const n = S.settings.products[+i].name, a = S.project.products;
    const k = a.indexOf(n); k >= 0 ? a.splice(k, 1) : a.push(n);
    await saveProject(); render();
  },
  addProduct: () => { $("#addprod").classList.remove("hidden"); $("#pname").focus(); },
  cancelProduct: () => $("#addprod").classList.add("hidden"),
  saveProduct: async () => {
    const name = $("#pname").value.trim(); if (!name) return;
    S.settings.products.push({ name, notes: $("#pnotes").value.trim() });
    S.project.products.push(name);
    await safe(() => api("/api/settings", S.settings)); await saveProject(); render();
  },
  addStratFile: async () => {
    const ps = await safe(() => browse("files", "any", "Sales input"));
    if (ps && ps.length) { S.project.strategy_files = [...new Set([...(S.project.strategy_files || []), ...ps])]; await saveProject(); render(); }
  },
  rmStratFile: async i => { S.project.strategy_files.splice(+i, 1); await saveProject(); render(); },
  pickOutXlsx: async () => { const p = await safe(() => browse("file", "excel", "Workbook to fill")); if (p) { S.project.output.excel.path = p; await saveProject(); render(); } },
  pickTemplate: async () => { const p = await safe(() => browse("file", "word", "Word template")); if (p) { S.project.output.word.template = p; await saveProject(); render(); } },
  saveAs: async kind => {
    const p = await safe(() => browse("save", kind === "excel" ? "excel" : "word", "Save response as"));
    if (p) {
      const ext = kind === "excel" ? (/\.xls[xm]$/i.test(p) ? "" : ".xlsx") : (/\.docx$/i.test(p) ? "" : ".docx");
      S.project.output[kind].out_path = p + ext; await saveProject(); render();
    }
  },
  addScale: async () => { S.settings.scale.push({ code: "?", label: "New level", meaning: "", color: "#EEEEEE" }); await safe(() => api("/api/settings", S.settings)); render(); },
  rmScale: async i => { S.settings.scale.splice(+i, 1); await safe(() => api("/api/settings", S.settings)); render(); },
  filter: f => { S.filter = f; render(); },
  sortBy: k => {
    const s = S.sort;
    if (!k) S.sort = null;
    else if (!s || s.key !== k) S.sort = { key: k, dir: "asc" };
    else S.sort = s.dir === "asc" ? { key: k, dir: "desc" } : null;
    render();
  },
  toggleOpen: k => { S.open.has(k) ? S.open.delete(k) : S.open.add(k); render(); },
  checkAll: (_, el) => { filtered().forEach(r => el.checked ? S.checked.add(r.key) : S.checked.delete(r.key)); render(); },
  run: async mode => {
    let keys;
    if (mode === "pending") keys = S.project.rows.filter(r => r.selected && !r.compliance).map(r => r.key);
    else keys = [...S.checked];
    if (!keys.length) return msg("Tick the rows to answer first", "warning");
    if (!S.project.products.length && !confirm("No product chosen. Answer anyway?")) return;
    await saveProject();
    const d = await safe(() => api("/api/run", { id: S.project.id, keys }));
    if (d) { await watchJob(d.job, true); await reloadProject(); }
  },
  runOne: async k => { const d = await safe(() => api("/api/run", { id: S.project.id, keys: [k] })); if (d) { await watchJob(d.job, true); await reloadProject(); renderReader(); } },
  setStatus: async arg => {
    const [k, st] = arg.split("|");
    const r = S.project.rows.find(x => x.key === k);
    if (st === "approved" && r.status === "needs_review" && !confirm("This answer has evidence that could not be verified. Approve anyway?")) return;
    r.status = st; await pushRows([{ key: k, status: st }]); render(); renderReader();
  },
  approveChecked: async () => {
    const rows = S.project.rows.filter(r => S.checked.has(r.key) && r.compliance);
    if (!rows.length) return msg("Tick answered rows first", "warning");
    const weak = rows.filter(r => r.status === "needs_review").length;
    if (weak && !confirm(`${weak} of the ticked rows need review (evidence not verified). Approve them all?`)) return;
    rows.forEach(r => r.status = "approved");
    await pushRows(rows.map(r => ({ key: r.key, status: "approved" }))); render();
  },
  exportExcel: async () => {
    const d = await safe(() => api("/api/export/excel", { id: S.project.id }));
    if (d) msgOpen(`${d.written} rows written to ${d.path}`, d.path);
  },
  exportWord: async () => {
    const d = await safe(() => api("/api/export/word", { id: S.project.id }));
    if (d) msgOpen(`${d.written} requirements written to ${d.path}`, d.path);
  },
  saveKb: async () => {
    const d = await safe(() => api("/api/kb/save-responses", { id: S.project.id, approved_only: true }));
    if (d) { msg(d.saved ? `${d.saved} approved answers saved to the knowledge base` : "No approved answers to save yet", d.saved ? "success" : "warning"); refreshState(); }
  },
  openPath: p => safe(() => api("/api/open", { path: p })),
  kbSqlForm: arg => {
    const [slug, idx] = arg.split("|");
    const a = S.areas.find(x => x.slug === slug);
    const src = idx != null ? a.sources[+idx] : { type: "sql", name: "", server: "", database: "", auth: "windows", user: "", query: "", title_col: "", key_col: "", trust_cert: true };
    S.sqlForm = { slug, idx: idx != null ? +idx : null, src: { ...src }, cols: [] };
    const v = k => esc(src[k] || "");
    const fixed = idx != null ? "readonly" : "";
    modal(`${idx != null ? "Edit" : "Add"} database source · ${esc(a.name)}`, `
      <div class="hint">Every row the query returns becomes one document in ${esc(a.name)}, written as “Column: value” lines, so answers can quote it and the quote is checked. Use an account that can only read. Only SELECT queries are accepted.</div>
      <div class="row2"><div class="field"><label>Name</label><input class="input" id="sq-name" value="${v("name")}" ${fixed} placeholder="e.g. Husky IVO certificates"></div>
        <div class="field"><label>Server</label><input class="input" id="sq-server" value="${v("server")}" ${fixed} placeholder="e.g. sql01.milestone.local\\PROD"></div></div>
      <div class="row2"><div class="field"><label>Database</label><input class="input" id="sq-database" value="${v("database")}" ${fixed} placeholder="e.g. ProductData"></div>
        <div class="field"><label>Sign in with</label><select class="input" id="sq-auth"><option value="windows" ${src.auth !== "sql" ? "selected" : ""}>My Windows account (integrated)</option><option value="sql" ${src.auth === "sql" ? "selected" : ""}>SQL Server login</option></select></div></div>
      <div class="row2" id="sq-login" ${src.auth === "sql" ? "" : 'style="display:none"'}><div class="field"><label>SQL login</label><input class="input" id="sq-user" value="${v("user")}"></div>
        <div class="field"><label>Password (kept on this PC only)</label><input class="input" id="sq-pw" type="password" placeholder="${idx != null ? "Leave empty to keep the stored one" : ""}"></div></div>
      <div class="field"><label>Table, view or SELECT query</label><textarea class="input" id="sq-query" style="min-height:90px;font-family:monospace" placeholder="dbo.HuskyCertificates   or   SELECT p.Model, c.Certificate, c.ValidUntil FROM dbo.Products p JOIN dbo.Certificates c ON c.ProductId = p.Id">${v("query")}</textarea></div>
      <div class="inline"><button class="btn secondary" data-act="kbSqlTest">Test and preview</button><span class="hint" id="sq-msg"></span></div>
      <div id="sq-tables"></div>
      <div class="row2"><div class="field"><label>Title column (names each row)</label><select class="input" id="sq-title"><option value="">—</option>${src.title_col ? `<option selected>${v("title_col")}</option>` : ""}</select></div>
        <div class="field"><label>Key column (unique id per row)</label><select class="input" id="sq-key"><option value="">Row number</option>${src.key_col ? `<option selected>${v("key_col")}</option>` : ""}</select></div></div>
      <div id="sq-preview"></div>
      <div class="inline" style="justify-content:flex-end"><button class="btn secondary" data-act="closeModal">Cancel</button><button class="btn primary" data-act="kbSqlSave">${idx != null ? "Save" : "Add source"}</button></div>`);
    $("#sq-auth").onchange = e => { $("#sq-login").style.display = e.target.value === "sql" ? "" : "none"; };
  },
  kbSqlTest: async () => {
    const f = sqlFormValues();
    $("#sq-msg").textContent = "Connecting…";
    let d;
    try { d = await api("/api/kb/sql/test", { source: f, password: $("#sq-pw").value }); }
    catch (e) { $("#sq-msg").innerHTML = `<span class="tag err">Failed</span> ${esc(e.message)}`; return; }
    $("#sq-msg").innerHTML = `<span class="tag ok">Connected</span> ${d.preview.columns.length} columns`;
    const cols = d.preview.columns;
    const opt = (sel, first) => `<option value="">${first}</option>` + cols.map(c => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("");
    const guess = re => cols.find(c => re.test(c)) || "";
    $("#sq-title").innerHTML = opt(f.title_col || guess(/name|model|title|product/i), "—");
    $("#sq-key").innerHTML = opt(f.key_col || guess(/^id$|_id$|key|number|^no$/i), "Row number");
    $("#sq-preview").innerHTML = `<div class="lbl">First rows</div><div class="tablewrap" style="max-height:220px"><table class="apollo grid"><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${d.preview.rows.map(r => `<tr>${r.map(x => `<td title="${esc(x)}">${esc(x)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    if (d.tables.length) $("#sq-tables").innerHTML = `<details><summary class="small" style="cursor:pointer">${d.tables.length} tables and views in this database</summary><div class="chips" style="margin-top:6px;max-height:140px;overflow:auto">${d.tables.map(t => `<button class="chip" data-act="kbSqlPick" data-arg="${esc(t.name)}">${esc(t.name)}${t.type === "VIEW" ? " (view)" : ""}</button>`).join("")}</div></details>`;
  },
  kbSqlPick: name => { $("#sq-query").value = name; ACT.kbSqlTest(); },
  kbSqlSave: async () => {
    const f = sqlFormValues(), F = S.sqlForm, pw = $("#sq-pw").value;
    const d = F.idx == null
      ? await safe(() => api("/api/kb/area/add-source", { slug: F.slug, source: f, password: pw }))
      : await safe(() => api("/api/kb/area/set-source", { slug: F.slug, index: F.idx, changes: f, password: pw }));
    if (d) { ACT.closeModal(); loadKbView(); msg("Database source saved. Click Update to load the rows.", "info"); }
  },
  kbNewArea: async () => {
    const name = $("#newarea").value.trim(); if (!name) return;
    if (await safe(() => api("/api/kb/area/create", { name }))) { await refreshState(); render(); }
  },
  kbAddFolder: async slug => {
    const p = await safe(() => browse("folder", "", `Folder for ${areaName(slug)}`)); if (!p) return;
    if (await safe(() => api("/api/kb/area/add-source", { slug, source: { type: "folder", path: p } }))) { loadKbView(); msg("Folder added. Click Update to index it.", "info"); }
  },
  kbAddSite: async slug => {
    const url = $(`#url-${slug}`).value.trim(); if (!url) return msg("Enter the website address first", "warning");
    const ok = await safe(() => api("/api/kb/area/add-source", { slug, source: { type: "web", url, max_pages: +$(`#max-${slug}`).value || 300 } }));
    if (ok) { loadKbView(); msg("Website added. Click Update to read it.", "info"); }
  },
  kbRmSource: async arg => {
    const [slug, i] = arg.split("|"), a = S.areas.find(x => x.slug === slug), s = a.sources[+i];
    if (!confirm(`Remove ${s.path || s.url} from ${a.name}?\n\nIts documents are removed from the published area too. The current version is kept for roll back.`)) return;
    const d = await safe(() => api("/api/kb/area/remove-source", { slug, index: +i }));
    if (d && d.job) await watchJob(d.job);
    await refreshState(); render();
  },
  kbUpdate: async slug => {
    const d = await safe(() => api("/api/kb/area/update", { slug })); if (d) { await watchJob(d.job); await refreshState(); render(); }
  },
  kbRebuild: async slug => {
    if (!confirm(`Rebuild ${areaName(slug)} from scratch?\n\nAll sources are read again into an empty area. Documents that are no longer in the sources (an old release, last year's annual report) disappear. The current version is kept for roll back, and nothing changes for your colleagues until the rebuild has finished.`)) return;
    const d = await safe(() => api("/api/kb/area/update", { slug, full: true })); if (d) { await watchJob(d.job, true); await refreshState(); render(); }
  },
  kbRollback: async slug => {
    if (!confirm(`Roll ${areaName(slug)} back to the version before the last change?`)) return;
    if (await safe(() => api("/api/kb/area/rollback", { slug }))) { msg(`${areaName(slug)} rolled back`, "success"); await refreshState(); render(); }
  },
  kbUnlock: async slug => {
    if (!confirm("Release the lock? Only do this if you are sure nobody is updating this area right now (for example after a crash).")) return;
    await safe(() => api("/api/kb/area/unlock", { slug })); loadKbView();
  },
  kbDocs: async slug => { S.docsArea = slug; S.docsSel = new Set(); modal(`Documents in ${esc(areaName(slug))}`, `
      <div class="inline"><input class="input" id="docq" placeholder="Filter by title or path, e.g. 2024"><button class="btn secondary" data-act="kbDocsFilter">Filter</button></div>
      <div class="hint">Removed documents come back on the next update if the file is still in a source folder; delete or move the file there too.</div>
      <div id="doclist" class="hint">Loading…</div>
      <div class="inline" style="justify-content:flex-end"><button class="btn primary" data-act="kbDocsRemove">Remove selected</button></div>`); ACT.kbDocsFilter(); },
  kbDocsFilter: async () => {
    const d = await safe(() => api("/api/kb/area/docs", { slug: S.docsArea, q: $("#docq")?.value || "" })); if (!d) return;
    S.docsList = d.docs;
    $("#doclist").innerHTML = d.docs.length ? `<div class="tablewrap" style="max-height:50vh"><table class="apollo"><thead><tr><th style="width:30px"></th><th>Document</th><th style="width:80px">Passages</th><th style="width:150px">Added</th></tr></thead><tbody>
      ${d.docs.map((x, i) => `<tr><td><input type="checkbox" class="docsel" data-i="${i}"></td><td class="small"><b>${esc(x.title)}</b><div class="muted clip">${esc(x.location)}</div></td><td class="small">${x.chunks}</td><td class="small">${esc(x.added_at || "")}<br><span class="muted">${esc(x.added_by || "")}</span></td></tr>`).join("")}
      </tbody></table></div>${d.docs.length >= 500 ? `<div class="hint">First 500 shown. Use the filter.</div>` : ""}` : `<div class="hint">No documents match.</div>`;
  },
  kbDocsRemove: async () => {
    const locs = [...document.querySelectorAll(".docsel:checked")].map(el => S.docsList[+el.dataset.i].location);
    if (!locs.length) return msg("Tick the documents to remove", "warning");
    if (!confirm(`Remove ${locs.length} document(s) from ${areaName(S.docsArea)}? A new version is published; the current one is kept for roll back.`)) return;
    ACT.closeModal();
    const d = await safe(() => api("/api/kb/area/remove-docs", { slug: S.docsArea, locations: locs })); if (d) { await watchJob(d.job); await refreshState(); render(); }
  },
  kbMigrate: async () => {
    const assign = {};
    document.querySelectorAll("[data-mig]").forEach(el => { assign[S.legacy.collections[+el.dataset.mig].collection] = el.value; });
    const d = await safe(() => api("/api/kb/migrate", { assign, answers: $("#migans").checked }));
    if (d) { await watchJob(d.job, true); await refreshState(); render(); }
  },
  kbMigDismiss: async () => { await safe(() => api("/api/kb/legacy-dismiss")); S.legacy = null; await refreshState(); render(); },
  kbSearchArea: slug => { S.kbSearch.has(slug) ? S.kbSearch.delete(slug) : S.kbSearch.add(slug); loadKbView(); },
  kbSearch: async () => {
    const d = await safe(() => api("/api/kb/search", { q: $("#kbq").value, areas: [...S.kbSearch] })); if (!d) return;
    $("#kbhits").innerHTML = (d.hits.map(h => `<div class="ev"><div class="src"><span class="tag info">${esc(areaName(h.area))}</span> ${h.kind === "web" ? `<a href="${esc(h.location)}" target="_blank" rel="noopener">${esc(h.title || h.location)}</a>` : `<a href="#" data-act="openPath" data-arg="${esc(h.location)}">${esc(h.title)}</a>`} · ${esc(h.heading || "")}</div><div class="clip small">${esc(h.text)}</div></div>`).join("") || `<div class="hint">No passages found.</div>`)
      + (d.precedents.length ? `<div class="lbl" style="margin:10px 0 6px">Earlier answers</div>` + d.precedents.map(pr => `<div class="ev"><div class="src">${esc(pr.project)} · ${esc(pr.req_id)} · ${esc(pr.status)}</div>${compPill(pr.compliance)} <span class="small">${esc(pr.explanation)}</span></div>`).join("") : "");
  },
  kbResponses: async () => {
    const d = await safe(() => api("/api/kb/responses", { q: $("#kbrq").value })); if (!d) return;
    $("#kbresp").innerHTML = d.responses.length ? `<div class="tablewrap" style="max-height:340px"><table class="apollo"><thead><tr><th>Project</th><th>Requirement</th><th>Answer</th><th></th></tr></thead><tbody>${d.responses.map(r => `<tr><td class="small">${esc(r.project)}<br><span class="muted">${esc(r.author || "")} · ${esc(r.updated_at)}</span><br>${(r.areas || []).map(a => `<span class="tag info">${esc(areaName(a))}</span>`).join(" ")}</td><td class="req small"><div class="clip">${esc(r.requirement)}</div></td><td class="req small">${compPill(r.compliance)} <span class="tag ${r.status === "approved" ? "ok" : "y"}">${esc(r.status)}</span><div class="clip">${esc(r.explanation)}</div></td><td><button class="iconbtn" data-act="kbDelResp" data-arg="${esc(r.fp)}" title="Delete">&#x2715;</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="hint">No answers stored yet.</div>`;
  },
  kbDelResp: async fp => { if (confirm("Delete this answer from the shared knowledge?")) { await safe(() => api("/api/kb/delete-response", { fp })); ACT.kbResponses(); } },
  pickKbRoot: async () => {
    const p = await safe(() => browse("folder", "", "Shared knowledge folder (e.g. in the synced SharePoint library)")); if (!p) return;
    S.settings.kb_root = p; await safe(() => api("/api/settings", S.settings)); await refreshState(); render();
    msg("Shared knowledge folder changed.", "info");
  },
  testClaude: async () => {
    await safe(() => api("/api/settings", S.settings));
    const d = await safe(() => api("/api/claude/test"));
    if (d) $("#claudetest").innerHTML = d.ok ? `<span class="tag ok">OK</span> ${esc(d.version)} · ${esc(d.exe)}` : `<span class="tag err">Not found</span> ${esc(d.version)}`;
  },
  repPreset: k => { const R = RF(); R.preset = k; R.filters = { ...(S.reportMeta.presets[k]?.filters || {}) }; R.open.clear(); render(); },
  repSet: arg => { const i = arg.indexOf("|"), k = arg.slice(0, i), v = arg.slice(i + 1); const R = RF(); R.filters[k] = v; render(); },
  repClear: () => { const R = RF(); R.filters = {}; render(); },
  repRefresh: async () => { const R = RF(); R.refresh = true; S.reportMeta = null; render(); },
  repRow: k => { const R = RF(); R.open.has(k) ? R.open.delete(k) : R.open.add(k); $("#repbody").innerHTML = reportBody(); },
  repMore: async () => {
    const R = RF();
    const d = await safe(() => api("/api/report/rows", { preset: "all", filters: R.filters, limit: 200, offset: R.offset }));
    if (d) { R.rows = R.rows.concat(d.rows); R.offset = R.rows.length; $("#repbody").innerHTML = reportBody(); }
  },
  repExport: async fmt => {
    const p = await safe(() => browse("save", fmt === "xlsx" ? "excel" : "", `Save the ${fmt.toUpperCase()} export as`)); if (!p) return;
    const path = p.toLowerCase().endsWith("." + fmt) ? p : p + "." + fmt;
    const d = await safe(() => api("/api/report/export", { preset: "all", filters: RF().filters, path }));
    if (d) msgOpen(`${fmtN(d.rows)} requirements from ${d.tenders} tenders exported to ${d.path}`, d.path);
  },
  repClaude: async () => {
    const R = RF();
    const d = await safe(() => api("/api/report/claude", { preset: R.preset, filters: R.filters, question: $("#repq").value }));
    if (!d) return;
    const j = await watchJob(d.job, true);
    if (j && j.status === "done") { S.reportMeta = null; await loadReport(true); showReport(j.result.title, j.result.markdown, j.result.docx); }
  },
  repView: async path => { const d = await safe(() => api("/api/report/read", { path })); if (d) showReport(path.split(/[\\/]/).pop().replace(/\.md$/, ""), d.markdown, path.replace(/\.md$/, ".docx")); },
  suggestProfile: async () => {
    await saveProject();
    const d = await safe(() => api("/api/project/suggest-profile", { id: S.project.id })); if (!d) return;
    const j = await watchJob(d.job);
    if (!j || j.status !== "done") return;
    const sug = j.result.profile, pr = S.project.profile;
    Object.entries(sug).forEach(([k, v]) => { if (v && (Array.isArray(v) ? v.length : String(v).trim())) pr[k] = v; });
    await saveProject(); render(); msg("Profile filled in from the requirements. Check it and correct what is wrong.", "success");
  },
  showBrief: () => {
    const b = S.project.brief;
    modal("Tender brief", b && b.text ? `<div class="hint">Written ${esc(b.at || "")} by reading all ${S.project.rows.length} requirements. Every batch of answers gets this brief, the outline of the document and the related answers already given, so answers refer to each other instead of repeating. It is written again when the requirements change.</div>
      <div class="mdview" style="white-space:pre-wrap">${esc(b.text)}</div>
      <div class="inline" style="justify-content:flex-end"><button class="btn secondary" data-act="briefAgain">Write it again</button><button class="btn primary" data-act="closeModal">Close</button></div>`
      : `<div class="hint">No brief yet. It is written automatically when you start answering: Claude reads all requirements once and notes what the tender is about, recurring themes and which facts to state only once.</div><div class="inline" style="justify-content:flex-end"><button class="btn primary" data-act="briefAgain">Write it now</button></div>`);
  },
  briefAgain: async () => {
    ACT.closeModal();
    const d = await safe(() => api("/api/project/brief", { id: S.project.id })); if (!d) return;
    const j = await watchJob(d.job); await reloadProject(); if (j && j.status === "done") ACT.showBrief();
  },
  cancelJob: id => safe(() => api("/api/job/cancel", { id })),
  toggleLog: () => $("#job .log")?.classList.toggle("hidden"),
};

function showReport(title, md, docx) {
  modal(esc(title), `${mdHtml(md)}<div class="inline" style="justify-content:flex-end">${docx ? `<button class="btn secondary" data-act="openPath" data-arg="${esc(docx)}">Open in Word</button>` : ""}<button class="btn primary" data-act="closeModal">Close</button></div>`);
  $("#modal .modal").classList.add("wide");
}

// ------------------------------------------------------------------ help (F1)
const HELP_FOR_TAB = { projects: "start", requirements: "requirements", sources: "sources", strategy: "strategy",
  output: "output", respond: "review", kb: "areas", report: "reporting", settings: "settings" };
const HELP = [
  ["start", "Getting started", `
    <p><b>RFP Responder</b> answers tender requirements the way the Global Bid Desk does: from evidence, for the product you offer, in the customer's own numbering. Every claim is checked against its source before anyone approves it.</p>
    <h4>First time</h4>
    <ol><li>Open <a href="#" data-act="helpTopic" data-arg="settings">Settings</a>. Add your name and initials, and click <b>Test</b> next to Claude Code.</li>
      <li>Under <b>Shared knowledge folder</b>, choose the RFP Responder folder in the synced Global Bid Desk SharePoint library, so you share knowledge areas, earlier answers and reports with the team.</li>
      <li>Click <b>Start</b> on the Responses page, then work through steps 1 to 5 in the bar at the top.</li></ol>
    <h4>The five steps</h4>
    <ol><li><a href="#" data-act="helpTopic" data-arg="requirements">Requirements</a>: the customer's Excel sheet or Word specification, and which part of it to answer.</li>
      <li><a href="#" data-act="helpTopic" data-arg="sources">Sources</a>: the knowledge areas to use, plus material for this tender only.</li>
      <li><a href="#" data-act="helpTopic" data-arg="strategy">Product &amp; strategy</a>: what you offer, rules, the win strategy and the tender profile.</li>
      <li><a href="#" data-act="helpTopic" data-arg="output">Output</a>: the Excel columns to fill and/or a Word template.</li>
      <li><a href="#" data-act="helpTopic" data-arg="review">Respond &amp; review</a>: Claude answers. You check, edit and approve, then write the files.</li></ol>
    <div class="msgbar info"><span>Answers can become legally binding. Nothing is marked compliant unless a quote from your sources supports it, and answers without verified evidence are flagged <b>Needs review</b>.</span></div>`],
  ["requirements", "1 · Requirements", `
    <p>Pick the customer's file. Answers keep the customer's numbering.</p>
    <h4>Excel (.xlsx, .xlsm)</h4>
    <ul><li>Choose the <b>sheet</b>, the <b>header row</b> and the columns: requirement text, ID, section (optional) and customer guidance (optional). The preview highlights the columns you picked.</li>
      <li><b>First requirement row</b> and <b>Last requirement row</b> limit the part that is answered. Leave the last row empty to read to the end of the sheet.</li></ul>
    <h4>Word (.docx)</h4>
    <ul><li>Choose what counts as a requirement: <b>lists &amp; shall/must</b> sentences, <b>every paragraph</b>, or <b>paragraphs + tables</b>.</li>
      <li>Section numbers come from the headings (4.1.3 = the third item under heading 4.1).</li>
      <li><b>Start at heading</b> and <b>Stop before heading</b> limit the part that is answered. The number after each heading is how many requirements sit under it, and the line below shows how many fall inside your range.</li></ul>
    <p>Click <b>Load requirements</b>. Loading again keeps the answers for requirements whose text has not changed. Untick the rows you do not want answered.</p>`],
  ["sources", "2 · Sources", `
    <h4>Knowledge areas</h4>
    <p>Choose which <a href="#" data-act="helpTopic" data-arg="areas">knowledge areas</a> this response may draw on: XProtect, BriefCam, Arcules, Husky IVO hardware, Technology partners, Milestone company info and any others the team has added. Offering XProtect only? Leave BriefCam and Arcules out, so their documents cannot creep into the answers. Earlier approved answers are only offered from the chosen areas.</p>
    <h4>Material for this response only</h4>
    <p>Customer documents, A&amp;E specifications and site surveys belong to this tender. Add the folders and click <b>Index folders now</b>, or add a website and click <b>Read site</b>. This material stays on your computer, for this response only. Material the whole team should reuse belongs in a knowledge area.</p>
    <h4>Live web lookups</h4>
    <p>Claude may also open pages on the listed domains (e.g. doc.milestonesys.com) while answering. Every link it gives is checked.</p>`],
  ["strategy", "3 · Product, strategy & tender profile", `
    <ul><li><b>Product</b>: choose the product(s) to offer. Each answer states which product, module or licence meets the requirement. Add products with <b>+</b>.</li>
      <li><b>Product rules</b>: e.g. "Answer for XProtect only. Do not offer BriefCam."</li>
      <li><b>Extra instructions</b>: e.g. "Milestone is the vendor, not the integrator."</li>
      <li><b>Win strategy</b> and <b>sales input files</b> (mails, notes): these steer emphasis and wording, never what the evidence supports.</li></ul>
    <h4>Tender profile (for reporting)</h4>
    <p>Vertical, country and region, end customer type, consultant or evaluator, partner, deadline, outcome, why the customer runs the tender, what concerns them, and key themes. Product management, security and marketing use it on the <a href="#" data-act="helpTopic" data-arg="reporting">Reporting</a> tab. <b>Suggest with Claude</b> fills it in from the requirements: check the result, as fields the text does not support are left empty.</p>
    <p>Turn off <b>Share in the requirements register</b> for a confidential tender. Nothing from it is then shared for reporting.</p>`],
  ["output", "4 · Output", `
    <h4>Excel</h4>
    <ul><li>Answers go into the customer's workbook, saved as a new file. The original is never changed.</li>
      <li>Compliance as <b>text in one column</b> (label or code), or as <b>a mark per level</b> (e.g. an X under Fully / Widely / Not supported).</li>
      <li>Columns for the explanation, product, links, evidence, responsible, confidence, initials and considerations. Rows that are not approved get the review notes as cell comments.</li>
      <li>The product is only named in front of an answer when it differs from the products chosen for the tender. The <b>Consider</b> note follows the answer as a short paragraph, or goes into its own column. You can switch it off.</li></ul>
    <h4>Word</h4>
    <p>Fill a template with placeholders such as <code>{{project}}</code> and <code>{{customer}}</code>, and one table row with <code>{{id}}</code>, <code>{{requirement}}</code>, <code>{{compliance_label}}</code>, <code>{{explanation}}</code> and so on, repeated per requirement. A template without such a row gets a compliance table at the end.</p>
    <h4>Compliance levels</h4>
    <p>Shared by all responses. Match them to the customer's scale if needed. The meaning text tells Claude when to use each level.</p>`],
  ["review", "5 · Respond & review", `
    <ul><li><b>Answer unanswered</b> sends the selected rows to Claude in batches. <b>Answer checked again</b> re-answers ticked rows. Choose the model next to the button.</li>
      <li>Filter with the chips: unanswered, needs review, draft, approved, or a compliance level.</li>
      <li>Open a row with <b>&#9662;</b> to see the quotes (verified or not), the links, why it is not fully compliant, the question to the customer and the product considered.</li>
      <li><b>&#x2922; Reading pane</b>: one requirement at a time, full size, with large fields. <b>Alt+&#8592; / Alt+&#8594;</b> step through the current filter. Filter on "Partially compliant" and go through them one by one.</li>
      <li><b>Writing quality.</b> Before answering, Claude reads the whole document once and writes a <b>tender brief</b> (button above the table): what the tender is about, recurring themes, and which facts to state only once. Every batch gets the brief, the outline and the related answers already given, so answers do not repeat the requirement or each other, refer to earlier answers (“see 4.2.1”), and do not all open with the product name. Each answer can carry a short <b>Consider</b> note: what the project should take into account and why it matters. Answers that still repeat the requirement or open like others are flagged. Filter on <b>Style issues</b>, tick them and use <b>Answer checked again</b>.</li>
      <li>Edit and <b>Approve</b>. Edits are saved while you type, and editing an approved answer sets it back to draft. The register records who approved each answer.</li>
      <li><b>Write Excel</b> / <b>Write Word</b> create the files. <b>Save approved to knowledge base</b> makes the answers precedents for the next tender.</li></ul>
    <h4>The evidence check</h4>
    <p>Each answer must quote its source word for word. The quote is searched for in the cited file, web page or database row, and every link is opened. Answers that fail are marked <span class="tag warn">Needs review</span>. Approving them asks for confirmation.</p>
    <h4>Word, Excel, PowerPoint and Outlook files</h4>
    <p>Claude cannot read these formats directly, so before each run the app makes readable text copies of them. Claude reads the copy and cites the original file.</p>`],
  ["areas", "Knowledge areas", `
    <p>The shared knowledge is split into <b>areas</b>, one per product or topic. Each area has its own sources: folders, documentation websites and databases. Everybody searches the latest published version of each area.</p>
    <ul><li><b>Update</b>: reads the sources again. Changed files are re-indexed, and deleted files and removed sources disappear.</li>
      <li><b>Rebuild</b>: starts from an empty area. Use it after a new release (point the website source to the new version first) or when old content must go, such as last year's annual report.</li>
      <li><b>Documents</b>: lists the content, so you can remove single documents. A removed file comes back on the next update if it is still in a source folder.</li>
      <li><b>Roll back</b>: restores the version before the last change.</li>
      <li><b>Add area</b> at the bottom creates a new one.</li></ul>
    <p>While someone changes an area it is locked, and the card shows who is changing it. <b>Release lock</b> is only for when you are sure nobody is working on it, for example after a crash. Your colleagues keep the old version until the new one is published.</p>
    <h4>Databases (MS SQL Server)</h4>
    <p><b>Add database (MS SQL)</b>: enter the server, the database and a table, view or SELECT query, then click <b>Test and preview</b>. Every row becomes a document that answers can quote. Only read queries are accepted. Use <b>My Windows account</b> where possible. A SQL login password is kept on your computer only (Windows encryption / Mac Keychain), never in the shared folder.</p>
    <h4>Old knowledge base</h4>
    <p>An old single-file knowledge base is offered for import at the top of the tab. Pick an area per collection, and the earlier answers come along.</p>`],
  ["reporting", "Reporting", `
    <p>Every response feeds a shared <b>requirements register</b>: the answered requirements with their compliance, product, gaps and who approved them, plus the <a href="#" data-act="helpTopic" data-arg="strategy">tender profile</a>. While answering, Claude tags each requirement with a topic, whether it is security-relevant, the standards it names, and why it is not fully compliant.</p>
    <h4>Views</h4>
    <ul><li><b>Product management</b>: where and why we are not compliant, by topic, product and gap type.</li>
      <li><b>Security &amp; compliance</b>: security-relevant requirements, the standards and regulations customers ask for, and where we fall short.</li>
      <li><b>Marketing</b>: verticals, regions, consultants, partners, outcomes, why customers tender and what concerns them.</li>
      <li><b>All requirements</b>: everything, with free filtering.</li></ul>
    <p>Filter by text, compliance, topic, product, vertical, region, country, outcome, tender and period. <b>Click a bar</b> to filter by it, and open a requirement to read the customer's wording, our answer and the gap. <a href="#" data-act="helpTopic" data-arg="settings">My reporting focus</a> in Settings chooses the view the tab opens on.</p>
    <h4>Getting the data out</h4>
    <ul><li><b>Excel / CSV / JSON</b> export the filtered requirements and tender profiles, for your own analysis or knowledge hub.</li>
      <li><b>Build report with Claude</b>: optionally ask a question, e.g. "Which missing capabilities cost us most in airport tenders?" Claude reads only the filtered data and writes a report with counts and examples, keeping facts apart from interpretation. It is saved as Word and Markdown in the shared folder's reports folder, where colleagues find it under <b>Reports</b>. Check the figures before you share them outside Milestone.</li></ul>
    <p class="muted small">Responses are published to the register about once a minute after changes. <b>Refresh</b> reads the latest.</p>`],
  ["settings", "Settings", `
    <ul><li><b>Your name and initials</b>: these go into the responses and show who approved what.</li>
      <li><b>Shared knowledge folder</b>: the team's folder in the synced SharePoint library. The list of <b>SharePoint libraries synced to this computer</b> shows which libraries the app found. Files in them are stored by library name, so sources work on Windows and Mac alike. Add a folder under <b>Extra library folders</b> if one is missing.</li>
      <li><b>My reporting focus</b>: the view the Reporting tab opens on.</li>
      <li><b>Strict evidence</b>, and the number of passages and earlier answers given to Claude per requirement.</li>
      <li><b>Claude Code</b>: the executable (click <b>Test</b>), model, effort, requirements per call, calls in parallel and timeout. Claude runs with read-only tools and uses your own Claude Code sign-in.</li></ul>`],
  ["breaks", "Breaks, shutdowns & reconnecting", `
    <ul><li>The app keeps running as long as its window is open, through long breaks, a locked screen or sleep. It never stops while answers are being generated.</li>
      <li>If the connection drops, a <b>Reconnecting…</b> banner appears. The window waits and repeats what it was doing, so nothing is lost.</li>
      <li>Edits are saved while you type.</li>
      <li>After the PC has been shut down, start RFP Responder again: it opens the response, step and row you were on. A window left open reconnects by itself.</li>
      <li>If a run was interrupted by a shutdown, the batches that had finished are kept. Click <b>Answer unanswered</b> to do the rest.</li></ul>`],
  ["install", "Install, update & Mac", `
    <h4>Windows</h4>
    <p>Double-click <code>install\\Install RFP Responder (Windows).bat</code> in the RFP Responder folder. Then start <b>RFP Responder</b> from the Start menu or the desktop.</p>
    <h4>Mac</h4>
    <p>Open Terminal, type <code>bash </code> (with a space), drag <code>install/install_mac.command</code> into the window and press Enter. Then start <b>RFP Responder</b> from Launchpad or Spotlight. The first time a file dialog opens, macOS may ask to let the app control "System Events": allow it. Database sources on a Mac need extra drivers and a SQL login (see the README). Areas built by Windows colleagues work without them.</p>
    <h4>Update</h4>
    <p>Run the installer again. Settings, responses and caches are kept.</p>
    <p>You need Python 3.11 or newer, Claude Code signed in with your own account, Edge or Chrome, and the Global Bid Desk library synced with OneDrive.</p>`],
  ["keys", "Keyboard shortcuts", `
    <table class="apollo"><tbody>
      <tr><td><b>F1</b></td><td>Help on the screen you are on</td></tr>
      <tr><td><b>Alt+&#8592; / Alt+&#8594;</b></td><td>Previous / next requirement in the reading pane</td></tr>
      <tr><td><b>Esc</b></td><td>Close the reading pane or a dialog</td></tr></tbody></table>`],
  ["trouble", "Troubleshooting", `
    <ul><li><b>"Claude Code did not return an answer"</b>: in Settings, click <b>Test</b>. Check that Claude Code is installed and signed in (<code>claude</code> in a terminal). Lower <b>Requirements per call</b>, or raise the timeout, for long batches.</li>
      <li><b>"…is changing this area"</b>: a colleague is updating it. Wait, or use <b>Release lock</b> if you are sure the lock is left over from a crash.</li>
      <li><b>"not on this PC"</b> on a folder source: sync that SharePoint library with OneDrive, or add it under Settings → Extra library folders.</li>
      <li><b>"No password … is stored on this PC"</b>: open the database source with <b>Edit</b> and enter the password once on this computer.</li>
      <li><b>Needs review</b> on many rows: the cited quotes were not found word for word. Open the row to see which quote failed. Often the source is not indexed, so index the folder or update the area.</li>
      <li><b>A report says there is no data</b>: loosen the filters, and check that the responses are shared (step 3) and answered.</li></ul>
    <p class="muted small">Prompts and raw answers for each run are kept in the runs folder under ${esc(S.dataDir || "the app's data folder")}.</p>`],
];

function showHelp(topic) {
  const cur = HELP.find(h => h[0] === topic) || HELP[0];
  modal("Help", `<div class="helpgrid"><nav>${HELP.map(([id, title]) => `<a href="#" class="${id === cur[0] ? "on" : ""}" data-act="helpTopic" data-arg="${id}">${title}</a>`).join("")}
    <div class="muted small" style="margin-top:12px">Version ${esc(S.version || "")} · F1 opens help anywhere</div></nav>
    <article><h3>${cur[1]}</h3>${cur[2]}</article></div>`);
  $("#modal .modal").classList.add("wide", "help");
}
document.addEventListener("keydown", e => {
  if (e.key === "F1") { e.preventDefault(); ACT.help(); }
  else if (e.key === "Escape" && !S.reading && $("#modal").innerHTML) ACT.closeModal();
});

function msgOpen(text, path) {
  const el = document.createElement("div");
  el.className = "msgbar success";
  el.innerHTML = `<span>${esc(text)}</span><button class="btn sm secondary" data-act="openPath" data-arg="${esc(path)}">Open</button><span class="x">&#x2715;</span>`;
  el.querySelector(".x").onclick = () => el.remove();
  $("#msgs").appendChild(el); setTimeout(() => el.remove(), 15000);
}

function modal(title, body) {
  $("#modal").innerHTML = `<div class="modal-bg" data-act="closeModal"><div class="modal" onclick="event.stopPropagation()"><header>${title}<button class="iconbtn" data-act="closeModal">&#x2715;</button></header><div class="body">${body}</div></div></div>`;
}
ACT.closeModal = () => { $("#modal").innerHTML = ""; S.reading = null; };

// reading pane: one requirement at a time, large, with previous / next through the current filter
function renderReader() {
  const k = S.reading; if (!k) return;
  const r = S.project.rows.find(x => x.key === k); if (!r) return ACT.closeModal();
  const i = S.readList.indexOf(k), n = S.readList.length;
  const st = { approved: ["ok", "Approved"], draft: ["info", "Draft"], needs_review: ["warn", "Needs review"] }[r.status] || ["", "—"];
  const scroll = $("#modal .body")?.scrollTop || 0;
  $("#modal").innerHTML = `<div class="modal-bg" data-act="closeModal"><div class="modal reader" onclick="event.stopPropagation()">
    <header><span>${esc(r.id)}</span>&nbsp;${compPill(r.compliance)}&nbsp;<span class="tag ${st[0]}">${st[1]}</span>
      <span class="muted small" style="margin-left:12px">${i + 1} of ${n}${S.filter !== "all" ? " in this filter" : ""} · Alt+← / Alt+→</span>
      <span class="grow"></span>
      <button class="btn sm secondary" data-act="readMove" data-arg="-1" ${i <= 0 ? "disabled" : ""}>&#8592; Previous</button>
      <button class="btn sm secondary" data-act="readMove" data-arg="1" ${i >= n - 1 ? "disabled" : ""}>Next &#8594;</button>
      <button class="iconbtn" data-act="closeModal" title="Close">&#x2715;</button></header>
    <div class="body">${detail(r)}</div></div></div>`;
  $("#modal .body").scrollTop = scroll;
}
ACT.read = k => { S.readList = filtered().map(r => r.key); if (!S.readList.includes(k)) S.readList.unshift(k); S.reading = k; renderReader(); };
ACT.readMove = d => {
  const i = S.readList.indexOf(S.reading) + +d;
  if (i < 0 || i >= S.readList.length) return;
  S.reading = S.readList[i]; renderReader(); $("#modal .body").scrollTop = 0;
};
document.addEventListener("keydown", e => {
  if (!S.reading) return;
  if (e.key === "Escape") ACT.closeModal();
  else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); ACT.readMove(1); }
  else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); ACT.readMove(-1); }
});

async function loadPreview(applyGuess) {
  const rs0 = S.project.req_source;
  const d = await safe(() => api("/api/req/preview", { path: rs0.path, mode: rs0.docx_mode || "lists" }));
  if (!d) return;
  if (d.kind === "word") { S.outline = d.outline; render(); return; }
  S.preview = d.sheets;
  const rs = S.project.req_source;
  if (applyGuess || !rs.sheet || !S.preview.some(s => s.name === rs.sheet)) {
    const sh = S.preview.reduce((a, b) => (b.max_row > a.max_row ? b : a), S.preview[0]);
    rs.sheet = sh.name; Object.assign(rs, sh.guess);
    await saveProject();
  }
  render();
}

async function pushRows(changes) {
  await safe(() => api("/api/rows/update", { id: S.project.id, changes }));
}
async function loadProjectSources() {
  if (!S.project) return;
  const c = await safe(() => api("/api/project/collections", { id: S.project.id })); if (c) S.projCols = c.collections;
  if (!S.areas) await loadAreas();
}
async function reloadProject() {
  const d = await safe(() => api("/api/project/get", { id: S.project.id }));
  if (d) { S.project = d.project; render(); }
}

// row checkboxes + inline edits in the review table
document.addEventListener("change", e => {
  const el = e.target;
  if (el.classList.contains("rowsel")) {
    const r = S.project.rows.find(x => x.key === el.dataset.key); r.selected = el.checked;
    pushRows([{ key: r.key, selected: r.selected }]);
  }
  if (el.classList.contains("rowchk")) el.checked ? S.checked.add(el.dataset.key) : S.checked.delete(el.dataset.key);
  if (el.dataset.row) {
    const r = S.project.rows.find(x => x.key === el.dataset.row);
    r[el.dataset.f] = el.value;
    if (r.status === "approved") r.status = "draft";
    pushRows([{ key: r.key, [el.dataset.f]: el.value, status: r.status }]);
    if (el.tagName === "SELECT" && S.reading) renderReader();
  }
  if (el.dataset.src) {
    const [slug, i] = el.dataset.src.split("|");
    safe(() => api("/api/kb/area/set-source", { slug, index: +i, changes: { max_pages: +el.value || 200 } }));
  }
  if (el.dataset.sc) {
    const [i, f] = el.dataset.sc.split(".");
    S.settings.scale[+i][f] = el.value;
    safe(() => api("/api/settings", S.settings));
  }
});

// ------------------------------------------------------------------ jobs
async function watchJob(id, showLog = false) {
  const bar = $("#job");
  bar.classList.remove("hidden");
  let logOpen = showLog;
  for (;;) {
    let j;
    try { j = await api("/api/job", { id }); } catch { await sleep(1500); continue; }
    const logHidden = bar.querySelector(".log")?.classList.contains("hidden");
    if (bar.querySelector(".log")) logOpen = !logHidden;
    bar.innerHTML = `<b>${esc(j.title)}</b><div class="progress"><i style="width:${Math.round(j.progress * 100)}%"></i></div>
      <span class="small muted" style="max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(j.message || "")}</span>
      <span class="small muted">${fmtTime(j.elapsed)}</span><span class="grow" style="flex:1"></span>
      <button class="textbtn" data-act="toggleLog">Log</button>${j.status === "running" ? `<button class="btn sm secondary" data-act="cancelJob" data-arg="${j.id}">Cancel</button>` : ""}
      <div class="log ${logOpen ? "" : "hidden"}">${esc((j.log || []).join("\n"))}</div>`;
    const lg = bar.querySelector(".log"); if (lg) lg.scrollTop = lg.scrollHeight;
    if (j.status !== "running") {
      if (j.status === "error") msg(j.error || "Failed", "error", 0);
      else if (j.status === "done") msg(summary(j), "success");
      setTimeout(() => bar.classList.add("hidden"), 4000);
      return j;
    }
    if (S.tab === "respond" && S.project && j.title.startsWith("Answering")) reloadQuiet();
    await sleep(1500);
  }
}
let lastQuiet = 0;
async function reloadQuiet() {
  if (Date.now() - lastQuiet < 8000) return; lastQuiet = Date.now();
  const d = await api("/api/project/get", { id: S.project.id }).catch(() => null);
  if (d && !document.activeElement?.dataset?.row && !Object.keys(rowTimers).length) { S.project = d.project; render(); if (S.reading) renderReader(); }
}
function summary(j) {
  const r = j.result || {};
  if ("answered" in r) return `${r.answered} answered, ${r.needs_review} need review${r.errors ? `, ${r.errors} failed batch(es)` : ""} · $${r.cost_usd}`;
  if ("markdown" in r) return `Report “${r.title}” saved for colleagues (${r.rows} requirements, ${r.tenders} tenders) · $${r.cost_usd}`;
  if ("brief" in r) return `Tender brief written · $${r.cost_usd}`;
  if ("profile" in r) return `Profile suggested · $${r.cost_usd}`;
  if ("version" in r) return `${areaName(r.area)} published as version ${r.version}: ${fmtN(r.documents)} documents`;
  if ("removed" in r) return `${r.removed} documents removed`;
  if ("areas" in r && "answers" in r) return `Imported into ${r.areas} areas, ${r.answers} earlier answers`;
  if ("added" in r) return `${r.added} files indexed, ${r.skipped} unchanged, ${r.failed} unreadable`;
  if ("stored" in r) return `${r.stored} pages stored, ${r.unchanged} unchanged, ${r.failed} failed`;
  return "Done";
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fmtTime = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

// ------------------------------------------------------------------ start
$("#kbchip").onclick = () => ACT.tab("kb");
setInterval(async () => { if (!reconnecting && !(await pingOnce())) waitOnline(); }, 10000);
(async () => {
  await safe(refreshState);
  loadAreas().then(() => { if (S.tab === "sources") render(); });
  api("/api/report/meta").then(m => { S.reportMeta = S.reportMeta || m; if (S.tab === "strategy") render(); }).catch(() => {});
  let h = new URLSearchParams(location.hash.slice(1));
  if (!location.hash && S.view && S.view.project && S.projects.some(p => p.id === S.view.project)) h = new URLSearchParams({ project: S.view.project, tab: S.view.tab || "", open: S.view.open || "" });
  else if (!location.hash && S.view && S.view.tab && !S.view.project) h = new URLSearchParams({ tab: S.view.tab });
  if (h.get("project")) { await ACT.openProject(h.get("project")); if (h.get("tab")) { S.tab = h.get("tab"); } if (h.get("open")) S.open.add(h.get("open")); }
  else if (h.get("tab")) S.tab = h.get("tab");
  render();
  if (!S.settings.user_initials) msg("Welcome. Add your name and initials in Settings; they go into the responses you write.", "info", 9000);
})();
