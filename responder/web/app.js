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
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Token": window.TOKEN }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({ error: "Bad response" }));
  if (!r.ok || d.error) throw new Error(d.error || r.statusText);
  return d;
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
  S.settings = d.settings; S.kb = d.kb; S.projects = d.projects; S.version = d.version;
  const ini = (S.settings.user_initials || S.settings.user_name || "?").split(/[\s.]+/).map(x => x[0] || "").join("").slice(0, 3).toUpperCase();
  $("#avatar").textContent = S.settings.user_initials || ini;
  $("#avatar").title = S.settings.user_name || "Set your name in Settings";
  const chip = $("#kbchip");
  chip.classList.toggle("err", !!S.kb.error);
  $("#kbtext").textContent = S.kb.error ? "Knowledge base unavailable" : `${S.kb.documents} documents · ${S.kb.responses} earlier answers`;
  chip.title = S.kb.path || "";
}

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
    S.settings[el.dataset.s] = v;
    clearTimeout(S._st); S._st = setTimeout(() => safe(() => api("/api/settings", S.settings)), 600);
  }
});
document.addEventListener("change", e => { if (e.target.dataset.rerender) render(); });

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
}

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
        <li><b>Sources</b>: folders with product documentation, specs, certificates. Click <b>+</b> to add more. Websites can be read into the knowledge base.</li>
        <li><b>Product &amp; strategy</b>: the product(s) to offer and the sales team's win strategy.</li>
        <li><b>Output</b>: which Excel columns to fill, and/or a Word template.</li>
        <li><b>Respond &amp; review</b>: Claude Code answers each requirement and quotes its evidence. Every quote and link is checked automatically; you approve before anything is exported.</li>
      </ol>
      <div class="msgbar info"><span>Answers can be legally binding. Nothing is marked compliant unless a quote from your sources supports it; rows without verified evidence are flagged for review.</span></div>
      <div class="footnote"><img src="${ICON("book")}" width="16" alt="">Approved answers go to the shared knowledge base, so the next response starts from what the team already agreed.</div>`)}
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
        <div class="row2"><div class="field"><label>Header row</label><input class="input" type="number" min="1" data-p="req_source.header_row" value="${rs.header_row}" data-rerender="1"></div>
        <div class="field"><label>First requirement row</label><input class="input" type="number" min="1" data-p="req_source.first_row" value="${rs.first_row}"></div></div>
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
      <div class="hint">Section numbers come from the headings, so the customer's numbering is kept (e.g. 4.1.3 = third item under heading 4.1).</div>`;
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
  const kbCols = (S.kbCols || []).reduce((m, c) => (m[c.collection] = c, m), {});
  const folders = p.folders.map((f, i) => {
    const c = kbCols[f.path];
    return `<div class="lrow"><img src="${ICON("folder")}" alt=""><div class="path" title="${esc(f.path)}">&lrm;${esc(f.path)}</div>
      <input class="input sm" placeholder="Label, e.g. A&E specs" data-p="folders.${i}.label" value="${esc(f.label || "")}">
      <label class="check small" title="Index the folder into the knowledge base, so its content can be searched and quoted"><input type="checkbox" data-p="folders.${i}.index" ${f.index !== false ? "checked" : ""}>Index</label>
      <span class="tag ${c ? "ok" : "y"}">${c ? c.docs + " docs indexed" : "not indexed"}</span>
      <button class="iconbtn" title="Remove" data-act="rmFolder" data-arg="${i}">&#x2715;</button></div>`;
  }).join("");
  const sites = p.websites.map((w, i) => {
    const c = Object.values(kbCols).find(x => w.url.startsWith(x.collection) || x.collection.startsWith(w.url.replace(/[^/]*$/, "")));
    return `<div class="lrow site"><img src="${ICON("world")}" alt=""><input class="input sm" data-p="websites.${i}.url" value="${esc(w.url)}" placeholder="https://doc.milestonesys.com/...">
      <input class="input sm" type="number" min="1" max="5000" data-p="websites.${i}.max_pages" value="${w.max_pages || 200}" title="Max pages">
      <button class="btn sm secondary" data-act="crawl" data-arg="${i}">${c ? "Update" : "Read site"}</button>
      <button class="iconbtn" title="Remove" data-act="rmSite" data-arg="${i}">&#x2715;</button></div>`;
  }).join("");
  return `<div class="cols">${pane("Background material", "folder", `
      ${hero("Where is the truth?", "Point to the folders that hold the facts: product documentation, A&E specifications, certificates, earlier responses. Claude Code reads them; nothing is written there.")}
      <div class="lbl">Folders</div>${folders || `<div class="hint">No folders yet.</div>`}
      <button class="add" data-act="addFolder"><span class="plus">+</span>Add folder</button>
      <div class="footer-actions"><span class="hint">Indexing makes the content searchable and lets every quote be checked word for word. Unchanged files are skipped.</span>
      <button class="btn primary" data-act="indexAll" ${p.folders.length ? "" : "disabled"}>Index folders now</button></div>
      <hr class="sep">
      <div class="lbl">Websites to read into the knowledge base</div>${sites || `<div class="hint">No websites yet. Add a documentation site, e.g. the XProtect manuals on doc.milestonesys.com.</div>`}
      <button class="add" data-act="addSite"><span class="plus">+</span>Add website</button>
      <div class="hint">The site is read page by page under the address you give (robots.txt respected). Run it again later to pick up changes.</div>`)}
    ${pane("Knowledge base", "book", `
      ${toggle("Use the shared knowledge base", "Search indexed documents, web pages and earlier approved answers for every requirement.", `data-p="use_kb" ${p.use_kb ? "checked" : ""}`)}
      <div class="stat"><div><b>${S.kb.documents ?? 0}</b><span>documents</span></div><div><b>${S.kb.chunks ?? 0}</b><span>passages</span></div><div><b>${S.kb.approved ?? 0}</b><span>approved answers</span></div><div><b>${S.kb.size_mb ?? 0}</b><span>MB</span></div></div>
      <div class="hint">Location: <code>${esc(S.kb.path || "")}</code><br>Put it on a shared drive in Settings, so the whole team builds and uses the same knowledge.</div>
      <hr class="sep">
      ${toggle("Live web lookups", "Claude Code may also open pages on these domains while answering:", `data-s="allow_web_lookup" ${S.settings.allow_web_lookup ? "checked" : ""}`)}
      <textarea class="input" style="min-height:60px" data-s="web_domains">${esc((S.settings.web_domains || []).join("\n"))}</textarea>
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
      <div class="footer-actions"><span></span><button class="btn primary" data-act="tab" data-arg="output">Next: output →</button></div>`)}</div>`;
};

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
        <div class="field"><label>Responsible</label>${colSelect("output.excel.owner_col", x.owner_col)}</div>
        <div class="field"><label>Confidence</label>${colSelect("output.excel.confidence_col", x.confidence_col)}</div>
        <div class="field"><label>Initials</label>${colSelect("output.excel.initials_col", x.initials_col)}</div>
      </div>
      <div class="hint">No product column? The product is written at the start of the explanation. Review notes are added as cell comments on rows that are not approved.</div>
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
      In one table row, repeated for every requirement: <code>{{id}}</code> <code>{{section}}</code> <code>{{requirement}}</code> <code>{{compliance_label}}</code> <code>{{explanation}}</code> <code>{{product}}</code> <code>{{responsible}}</code> <code>{{links}}</code> <code>{{evidence}}</code> <code>{{gaps}}</code> <code>{{question}}</code><br>
      Without such a row, a compliance table is added at the end of the template.</div></details></div>`)}
    ${pane("Compliance levels", "checkmark-ok", `<div class="hint">Shared by all responses. Match them to the customer's scale if needed (e.g. Fully / Widely / Not yet / Not supported).</div>
      <table class="apollo"><thead><tr><th>Code</th><th>Label</th><th>Meaning for the responder</th><th>Colour</th><th></th></tr></thead><tbody>${scaleRows}</tbody></table>
      <button class="add" data-act="addScale"><span class="plus">+</span>Add level</button>
      <div class="footer-actions"><span></span><button class="btn primary" data-act="tab" data-arg="respond">Next: respond →</button></div>`)}</div></div>`;
};

function filtered() {
  const f = S.filter, rows = S.project.rows;
  if (f === "all") return rows;
  if (f === "selected") return rows.filter(r => r.selected);
  if (f === "unanswered") return rows.filter(r => !r.compliance);
  if (f.startsWith("c:")) return rows.filter(r => r.compliance === f.slice(2));
  return rows.filter(r => r.status === f);
}

VIEWS.respond = () => {
  const p = S.project, rows = p.rows;
  const cnt = k => rows.filter(r => r.status === k).length;
  const sel = rows.filter(r => r.selected);
  const pending = sel.filter(r => !r.compliance).length;
  const chips = [["all", `All ${rows.length}`], ["unanswered", `Unanswered ${rows.filter(r => !r.compliance).length}`], ["needs_review", `Needs review ${cnt("needs_review")}`], ["draft", `Draft ${cnt("draft")}`], ["approved", `Approved ${cnt("approved")}`]]
    .concat(scale().map(s => ["c:" + s.code, `${s.label} ${rows.filter(r => r.compliance === s.code).length}`]))
    .map(([k, l]) => `<button class="chip ${S.filter === k ? "on" : ""}" data-act="filter" data-arg="${k}">${l}</button>`).join("");
  const list = filtered();
  const body = list.map(r => {
    const ev = r.evidence || [], ok = ev.filter(e => e.verified).length;
    const st = { approved: ["ok", "Approved"], draft: ["info", "Draft"], needs_review: ["warn", "Needs review"] }[r.status] || ["", "—"];
    const open = S.open.has(r.key);
    let html = `<tr class="${open ? "open" : ""}"><td><input type="checkbox" class="rowchk" data-key="${esc(r.key)}" ${S.checked.has(r.key) ? "checked" : ""}></td>
      <td>${esc(r.id)}</td><td class="req"><div class="clip">${esc(r.text)}</div></td><td>${compPill(r.compliance)}</td>
      <td class="small">${esc(r.product || "")}</td><td class="req small"><div class="clip">${esc(r.explanation || "")}</div></td>
      <td>${r.compliance ? `<span class="tag ${ok ? "ok" : "err"}" title="Verified quotes / quotes">${ok}/${ev.length}</span>` : ""}</td>
      <td class="small">${r.confidence != null ? r.confidence + "%" : ""}</td><td><span class="tag ${st[0]}">${st[1]}</span></td>
      <td><button class="iconbtn" data-act="toggleOpen" data-arg="${esc(r.key)}" title="Details">${open ? "&#9652;" : "&#9662;"}</button></td></tr>`;
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
        <button class="btn tertiary" data-act="approveChecked">Approve checked</button>
        <button class="btn secondary" data-act="exportExcel" ${p.output.excel.enabled ? "" : "disabled"}><img src="${ICON("download")}" width="16" alt="">Write Excel</button>
        <button class="btn secondary" data-act="exportWord" ${p.output.word.enabled ? "" : "disabled"}><img src="${ICON("document")}" width="16" alt="">Write Word</button>
        <button class="btn accent" data-act="saveKb" title="Store approved answers in the knowledge base">Save approved to knowledge base</button>
      </div>
      <div class="chips">${chips}</div>
      ${last ? `<div class="hint">Last run ${esc(last.at)}: ${last.rows} requirements, model ${esc(last.model)}, cost $${last.cost_usd}${last.errors ? `, <span style="color:var(--W68)">${last.errors} failed batch(es)</span>` : ""}.</div>` : ""}
      <div class="tablewrap" style="max-height:calc(100vh - 330px)"><table class="apollo"><thead><tr><th style="width:34px"><input type="checkbox" data-act="checkAll"></th><th style="width:80px">ID</th><th>Requirement</th><th style="width:150px">Compliance</th><th style="width:150px">Product</th><th>Response</th><th style="width:70px">Evidence</th><th style="width:60px">Conf.</th><th style="width:110px">Status</th><th style="width:40px"></th></tr></thead>
      <tbody>${body || `<tr><td colspan="10"><div class="empty">Nothing here.</div></td></tr>`}</tbody></table></div>`)}`;
};

function detail(r) {
  const ev = (r.evidence || []).map(e => `<div class="ev ${e.verified ? "ok" : "bad"}"><span class="tag ${e.verified ? "ok" : "warn"}">${e.verified ? "Quote verified" : "Not found in source"}</span>
      <q>${esc(e.quote)}</q><div class="src">${e.source && e.source.startsWith("http") ? `<a href="${esc(e.source)}" target="_blank" rel="noopener">${esc(e.source)}</a>` : `<a href="#" data-act="openPath" data-arg="${esc(e.source)}" title="${esc(e.source)}">${esc(e.source.split(/[\\/]/).pop())}</a>`}${e.where ? " · " + esc(e.where) : ""}</div></div>`).join("") || `<div class="hint">No evidence given.</div>`;
  const links = (r.links || []).map(l => `<div class="small"><span class="tag ${l.ok ? "ok" : "err"}">${l.ok ? "OK" : "Broken"}</span> <a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title || l.url)}</a></div>`).join("");
  const opts = scale().map(s => `<option value="${esc(s.code)}" ${s.code === r.compliance ? "selected" : ""}>${esc(s.label)}</option>`).join("");
  return `<div class="detail-grid"><div style="display:grid;gap:10px">
      <div class="small muted">${esc(r.section || "")}</div><div>${esc(r.text)}</div>
      <div class="row3"><div class="field"><label>Compliance</label><select class="input" data-row="${esc(r.key)}" data-f="compliance"><option value="">—</option>${opts}</select></div>
        <div class="field"><label>Product considered</label><input class="input" data-row="${esc(r.key)}" data-f="product" value="${esc(r.product || "")}"></div>
        <div class="field"><label>Responsible</label><input class="input" data-row="${esc(r.key)}" data-f="responsible" value="${esc(r.responsible || "")}"></div></div>
      <div class="field"><label>Response</label><textarea class="input" style="min-height:110px" data-row="${esc(r.key)}" data-f="explanation">${esc(r.explanation || "")}</textarea></div>
      <div class="row2"><div class="field"><label>Gaps / assumptions</label><textarea class="input" style="min-height:60px" data-row="${esc(r.key)}" data-f="gaps">${esc(r.gaps || "")}</textarea></div>
        <div class="field"><label>Question to the customer</label><textarea class="input" style="min-height:60px" data-row="${esc(r.key)}" data-f="question">${esc(r.question || "")}</textarea></div></div>
      ${r.review_notes ? `<div class="msgbar warning"><span>${esc(r.review_notes)}</span></div>` : ""}
      <div class="inline"><button class="btn primary" data-act="setStatus" data-arg="${esc(r.key)}|approved">Approve</button><button class="btn secondary" data-act="setStatus" data-arg="${esc(r.key)}|draft">Back to draft</button>
        <button class="btn tertiary" data-act="runOne" data-arg="${esc(r.key)}">Answer again</button><span class="grow"></span>${r.confidence != null ? `<span class="muted small">Confidence ${r.confidence}%</span>` : ""}</div>
    </div><div><div class="lbl" style="margin-bottom:6px">Evidence</div>${ev}${links ? `<div class="lbl" style="margin:12px 0 6px">Links for the customer</div>${links}` : ""}</div></div>`;
}

VIEWS.kb = () => `<div class="cols">${pane("Knowledge base", "book", `
    ${hero("What the responder knows", "Indexed folders, websites and every answer the team has approved. Put the file on a shared drive and everyone builds on the same knowledge.")}
    <div class="stat" id="kbstat"></div>
    <div class="hint">File: <code>${esc(S.kb.path || "")}</code> · change it in Settings.</div>
    <div class="lbl">Collections</div><div id="kbcols" style="display:grid;gap:6px"><div class="hint">Loading…</div></div>
    <button class="add" data-act="kbAddFolder"><span class="plus">+</span>Index a folder</button>
    <div class="inline"><input class="input" id="kburl" placeholder="https://doc.milestonesys.com/xprotect/xprotect-management-client/2026r2/en/"><input class="input" id="kbmax" type="number" value="200" style="max-width:90px" title="Max pages"><button class="btn primary" data-act="kbCrawl">Read website</button></div>`)}
  ${pane("Search", "search-text", `
    <div class="inline"><input class="input" id="kbq" placeholder="Try a requirement, e.g. 'redundant management server failover'"><button class="btn primary" data-act="kbSearch">Search</button></div>
    <div id="kbhits"></div>
    <hr class="sep"><div class="toolbar"><div class="lbl">Earlier answers</div><span class="grow"></span><input class="input sm" style="max-width:240px" id="kbrq" placeholder="Filter"><button class="btn sm tertiary" data-act="kbResponses">Show</button></div>
    <div id="kbresp"></div>`)}</div>`;

async function loadKbView() {
  const d = await safe(() => api("/api/kb/collections"));
  if (!d) return;
  S.kbCols = d.collections;
  const st = d.stats;
  $("#kbstat") && ($("#kbstat").innerHTML = `<div><b>${st.documents}</b><span>documents</span></div><div><b>${st.chunks}</b><span>passages</span></div><div><b>${st.responses}</b><span>answers</span></div><div><b>${st.approved}</b><span>approved</span></div><div><b>${st.size_mb}</b><span>MB</span></div>`);
  const el = $("#kbcols");
  if (el) el.innerHTML = d.collections.length ? d.collections.map((c, i) => `<div class="lrow site" style="grid-template-columns:28px 1fr auto auto 32px"><img src="${ICON(c.kind === "web" ? "world" : "folder")}" alt="">
      <div class="path" title="${esc(c.collection)}">&lrm;${esc(c.collection)}</div><span class="tag info">${c.docs} docs · ${c.chunks} passages</span>
      <button class="btn sm secondary" data-act="kbRefresh" data-arg="${i}">Update</button><button class="iconbtn" title="Remove from knowledge base" data-act="kbDelete" data-arg="${i}">&#x2715;</button></div>`).join("")
    : `<div class="hint">Empty. Index a folder or read a website to start.</div>`;
}

VIEWS.settings = () => {
  const s = S.settings;
  return `<div class="cols even">${pane("You and the knowledge base", "settings", `
      <div class="row2"><div class="field"><label>Your name</label><input class="input" data-s="user_name" value="${esc(s.user_name)}"></div>
      <div class="field"><label>Initials</label><input class="input" data-s="user_initials" value="${esc(s.user_initials)}"></div></div>
      <div class="field"><label>Knowledge base file</label><div class="inline"><input class="input" readonly value="${esc(s.db_path)}"><button class="btn tertiary" data-act="pickDbFolder">Choose folder…</button><button class="btn tertiary" data-act="pickDbFile">Open existing…</button></div>
      <div class="hint">For a team: choose a folder on a shared drive (e.g. the Global Bid Desk SharePoint library synced locally, or a network share). Everyone who points to the same file shares indexed documents and approved answers.</div></div>
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
      <div class="hint">Claude Code runs with read-only tools (Read, Grep, Glob) on your source folders and, if allowed, web fetch on the listed domains. It cannot change files. Prompts and answers of every run are kept under <code>%LOCALAPPDATA%\\RFPResponder\\runs</code> for traceability.</div>
      <div class="hint">Version ${esc(S.version)}</div>`)}</div>`;
};

// ------------------------------------------------------------------ actions
const ACT = {
  tab: t => { S.tab = t; render(); },
  help: () => modal("Help", `
    <p><b>RFP Responder</b> answers tender requirements the way the bid desk does: from evidence, per product, in the customer's numbering.</p>
    <ol style="display:grid;gap:6px;padding-left:18px">
      <li><b>Requirements</b>: pick the Excel or Word file. For Excel, choose the sheet and the column with the requirement text (and the ID column).</li>
      <li><b>Sources</b>: add folders with <b>+</b>. “Index folders now” makes them searchable and lets quotes be checked. Add documentation websites; they are read into the knowledge base.</li>
      <li><b>Product &amp; strategy</b>: choose the product(s); add rules (e.g. “XProtect only, not BriefCam”) and the sales team's win strategy.</li>
      <li><b>Output</b>: map the Excel columns (text, or an X per level) and/or pick a Word template with placeholders.</li>
      <li><b>Respond &amp; review</b>: answer, then open each row, check the quotes, edit and approve. Write Excel/Word. Save approved answers to the knowledge base.</li>
    </ol>
    <p><b>Evidence check.</b> Each answer must quote its source. The quote is searched word for word in the cited file or web page, and every link is opened. Answers that fail are marked <span class="tag warn">Needs review</span>.</p>
    <p><b>Shared knowledge.</b> In Settings, point the knowledge base to a shared folder. Approved answers become precedents for the next tender; the facts are still checked against the sources.</p>
    <p class="muted small">Prompts and raw answers for each run: %LOCALAPPDATA%\\RFPResponder\\runs</p>`),
  newProject: async () => {
    const name = $("#newname").value.trim() || "New response";
    const d = await safe(() => api("/api/project/new", { name }));
    if (d) { S.project = d.project; S.preview = null; S.tab = "requirements"; await refreshState(); render(); }
  },
  openProject: async id => {
    const d = await safe(() => api("/api/project/get", { id }));
    if (!d) return;
    S.project = d.project; S.preview = null; S.open.clear(); S.checked.clear();
    S.tab = d.project.rows.length ? "respond" : "requirements";
    if (/\.xls[xm]$/i.test(d.project.req_source.path || "")) await loadPreview(false);
    const c = await safe(() => api("/api/kb/collections")); if (c) S.kbCols = c.collections;
    render();
  },
  pickReq: async () => {
    const p = await safe(() => browse("file", "requirements", "Requirements file"));
    if (!p) return;
    S.project.req_source.path = p; S.project.req_source.kind = p.toLowerCase().endsWith(".docx") ? "word" : "excel";
    await saveProject();
    if (S.project.req_source.kind === "excel") await loadPreview(true); else render();
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
      const d = await safe(() => api("/api/kb/ingest", { path: f.path, collection: f.path }));
      if (d) await watchJob(d.job);
    }
    const c = await safe(() => api("/api/kb/collections")); if (c) S.kbCols = c.collections;
    await refreshState(); render();
  },
  addSite: async () => { S.project.websites.push({ url: "", max_pages: 200 }); await saveProject(); render(); },
  rmSite: async i => { S.project.websites.splice(+i, 1); await saveProject(); render(); },
  crawl: async i => {
    const w = S.project.websites[+i];
    if (!w.url) return msg("Enter the website address first", "warning");
    await saveProject();
    const d = await safe(() => api("/api/kb/crawl", { url: w.url, max_pages: w.max_pages || 200 }));
    if (d) { await watchJob(d.job); const c = await safe(() => api("/api/kb/collections")); if (c) S.kbCols = c.collections; await refreshState(); render(); }
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
  runOne: async k => { const d = await safe(() => api("/api/run", { id: S.project.id, keys: [k] })); if (d) { await watchJob(d.job, true); await reloadProject(); } },
  setStatus: async arg => {
    const [k, st] = arg.split("|");
    const r = S.project.rows.find(x => x.key === k);
    if (st === "approved" && r.status === "needs_review" && !confirm("This answer has evidence that could not be verified. Approve anyway?")) return;
    r.status = st; await pushRows([{ key: k, status: st }]); render();
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
  kbAddFolder: async () => {
    const p = await safe(() => browse("folder", "", "Folder to index")); if (!p) return;
    const d = await safe(() => api("/api/kb/ingest", { path: p })); if (d) { await watchJob(d.job); await refreshState(); render(); }
  },
  kbCrawl: async () => {
    const url = $("#kburl").value.trim(); if (!url) return;
    const d = await safe(() => api("/api/kb/crawl", { url, max_pages: +$("#kbmax").value || 200 })); if (d) { await watchJob(d.job); await refreshState(); render(); }
  },
  kbRefresh: async i => {
    const c = S.kbCols[+i];
    const d = c.kind === "web" ? await safe(() => api("/api/kb/crawl", { url: c.collection, max_pages: Math.max(200, c.docs + 50), prefix: c.collection }))
      : await safe(() => api("/api/kb/ingest", { path: c.collection, collection: c.collection }));
    if (d) { await watchJob(d.job); await refreshState(); render(); }
  },
  kbDelete: async i => {
    const c = S.kbCols[+i];
    if (!confirm(`Remove “${c.collection}” (${c.docs} documents) from the shared knowledge base?`)) return;
    await safe(() => api("/api/kb/delete-collection", { collection: c.collection })); await refreshState(); render();
  },
  kbSearch: async () => {
    const d = await safe(() => api("/api/kb/search", { q: $("#kbq").value })); if (!d) return;
    $("#kbhits").innerHTML = (d.hits.map(h => `<div class="ev"><div class="src">${h.kind === "web" ? `<a href="${esc(h.location)}" target="_blank" rel="noopener">${esc(h.title || h.location)}</a>` : `<a href="#" data-act="openPath" data-arg="${esc(h.location)}">${esc(h.title)}</a>`} · ${esc(h.heading || "")}</div><div class="clip small">${esc(h.text)}</div></div>`).join("") || `<div class="hint">No passages found.</div>`)
      + (d.precedents.length ? `<div class="lbl" style="margin:10px 0 6px">Earlier answers</div>` + d.precedents.map(pr => `<div class="ev"><div class="src">${esc(pr.project)} · ${esc(pr.req_id)} · ${esc(pr.status)}</div>${compPill(pr.compliance)} <span class="small">${esc(pr.explanation)}</span></div>`).join("") : "");
  },
  kbResponses: async () => {
    const d = await safe(() => api("/api/kb/responses", { q: $("#kbrq").value })); if (!d) return;
    $("#kbresp").innerHTML = d.responses.length ? `<div class="tablewrap" style="max-height:340px"><table class="apollo"><thead><tr><th>Project</th><th>Requirement</th><th>Answer</th><th></th></tr></thead><tbody>${d.responses.map(r => `<tr><td class="small">${esc(r.project)}<br><span class="muted">${esc(r.author || "")} · ${esc(r.updated_at)}</span></td><td class="req small"><div class="clip">${esc(r.requirement)}</div></td><td class="req small">${compPill(r.compliance)} <span class="tag ${r.status === "approved" ? "ok" : "y"}">${esc(r.status)}</span><div class="clip">${esc(r.explanation)}</div></td><td><button class="iconbtn" data-act="kbDelResp" data-arg="${r.id}" title="Delete">&#x2715;</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="hint">No answers stored yet.</div>`;
  },
  kbDelResp: async id => { if (confirm("Delete this answer from the knowledge base?")) { await safe(() => api("/api/kb/delete-response", { rid: id })); ACT.kbResponses(); } },
  pickDbFolder: async () => {
    const p = await safe(() => browse("folder", "", "Folder for the knowledge base")); if (!p) return;
    S.settings.db_path = p.replace(/[\\/]$/, "") + "\\RFPResponder_knowledge.db";
    await safe(() => api("/api/settings", S.settings)); await refreshState(); render();
    msg("Knowledge base location changed. Existing content stays in the old file.", "info");
  },
  pickDbFile: async () => {
    const p = await safe(() => browse("file", "db", "Existing knowledge base")); if (!p) return;
    S.settings.db_path = p; await safe(() => api("/api/settings", S.settings)); await refreshState(); render();
  },
  testClaude: async () => {
    await safe(() => api("/api/settings", S.settings));
    const d = await safe(() => api("/api/claude/test"));
    if (d) $("#claudetest").innerHTML = d.ok ? `<span class="tag ok">OK</span> ${esc(d.version)} · ${esc(d.exe)}` : `<span class="tag err">Not found</span> ${esc(d.version)}`;
  },
  cancelJob: id => safe(() => api("/api/job/cancel", { id })),
  toggleLog: () => $("#job .log")?.classList.toggle("hidden"),
};

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
ACT.closeModal = () => { $("#modal").innerHTML = ""; };

async function loadPreview(applyGuess) {
  const d = await safe(() => api("/api/req/preview", { path: S.project.req_source.path }));
  if (!d) return;
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
  if (d && !document.activeElement?.dataset?.row) { S.project = d.project; render(); }
}
function summary(j) {
  const r = j.result || {};
  if ("answered" in r) return `${r.answered} answered, ${r.needs_review} need review${r.errors ? `, ${r.errors} failed batch(es)` : ""} · $${r.cost_usd}`;
  if ("added" in r) return `${r.added} files indexed, ${r.skipped} unchanged, ${r.failed} unreadable`;
  if ("stored" in r) return `${r.stored} pages stored, ${r.unchanged} unchanged, ${r.failed} failed`;
  return "Done";
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fmtTime = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

// ------------------------------------------------------------------ start
$("#kbchip").onclick = () => ACT.tab("kb");
setInterval(() => fetch("/api/ping", { method: "POST", headers: { "X-Token": window.TOKEN } }).catch(() => {}), 10000);
(async () => {
  await safe(refreshState);
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.get("project")) { await ACT.openProject(h.get("project")); if (h.get("tab")) { S.tab = h.get("tab"); } if (h.get("open")) S.open.add(h.get("open")); }
  else if (h.get("tab")) S.tab = h.get("tab");
  render();
  if (!S.settings.user_initials) msg("Welcome. Add your name and initials in Settings; they go into the responses you write.", "info", 9000);
})();
