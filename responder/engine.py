"""Answer requirements with Claude Code and check every claim against its source.

Per batch of requirements:
  1. Retrieve evidence from the knowledge base (indexed folders + scraped web pages) and earlier
     approved answers (precedents).
  2. Run Claude Code headless (`claude -p`) with read-only tools on the chosen folders and,
     optionally, web fetch on allowed domains. Output is forced into a JSON schema.
  3. Verify: every quoted piece of evidence must be found word for word in the cited source,
     and every link must resolve. Answers without verified evidence are flagged "needs review".
"""
import concurrent.futures as cf
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

from . import config, context, extract, osdep, persona, readable, register

SYSTEM_PROMPT = """You are a senior bid and compliance specialist. You write compliance responses to tender
requirements on behalf of the bidder. The responses will be used in real tender submissions and can become
legally binding, so accuracy matters more than a positive answer.

Rules:
1. Base every compliance statement on evidence. Evidence is the numbered extracts given to you ([S..]),
   files you read in the allowed folders, or pages you fetch on the allowed domains.
2. For each claim, copy a short exact quote (one or two sentences, word for word, no paraphrase, no
   ellipsis inside a sentence) from the source into `evidence`, with the source id ([S..] label without
   brackets), the full path of the FILE (not its folder), or the URL. Quotes are checked automatically against the source;
   invented or reworded quotes are rejected.
3. Every factual sentence in `explanation` must be backed by your evidence. Do not speculate or add
   facts you have not seen ("may", "probably", "some components might"); put open points in `gaps`.
   Before you settle on partial or non-compliant, search the allowed folders and web pages for
   confirming evidence.
4. Compliance says what the offered product does. `confidence` says how sure you are. Keep the two apart:
   - Compliant: the offered products meet the requirement as written. This includes Milestone modules,
     extensions, add-ons and licences that belong to the offered products (name them), and normal
     configuration by the integrator. A small detail you could not confirm is not a gap: stay compliant,
     lower `confidence` and say in `gaps` what to check.
   - Partially compliant: only when a specific part of the requirement is not met by the offered
     products, or needs a third-party product, custom development or a workaround. Name that part in
     `gaps`. If you cannot name it, the answer is not partial.
   - Not compliant: the core of the requirement is not met.
   - Missing evidence is not a reason for partial. Never use partial as a safe middle. Choose the code the
     product facts support, set `confidence` below 50, and say in `gaps` what must be confirmed. Never
     claim compliance for something you have reason to believe the product cannot do.
   `confidence` (0-100): 90+ a verified quote states it directly; 70-89 the evidence supports it with
   little inference; 50-69 indirect evidence or partly inferred; below 50 not confirmed, a person must check.
5. Earlier approved responses ([P..]) show how the team answered similar requirements before. Use them
   for wording and consistency, but they are not evidence on their own: confirm the facts in a source.
6. Use the acknowledgement code for requirements that are not a product capability (installation,
   construction, cabling, network, hardware supply, project management, the customer's own processes,
   general scope statements, headings). Put who is responsible in `responsible` (one of the listed
   parties). See "Length" below: these answers are one short sentence.
7. Name the product(s) you considered for each requirement in `product`. Use only the products you are
   told to consider, and respect the product rules. In `explanation`, name a product, module, edition
   or licence only where it adds information: when the function comes from a specific module, add-on,
   licence or edition, or differs from the main offer. Do not open the explanation with the product name.
8. Respect the bidder's role: if the bidder is the software vendor, do not commit to integrator or
   customer tasks; state them as their responsibility.
9. Write plain, tender-safe language: short sentences, no marketing superlatives, no
   "fully", "always" or "guaranteed" unless the source says so. Keep to the length rules below. Tone,
   emphasis and vocabulary follow the "Response style and audience" section of the prompt, within
   these rules.
10. Use the win strategy to choose emphasis and wording, never to overstate a capability.
11. `links` are public URLs the customer can open to read more (product documentation). Only give URLs
    you have seen in the extracts or fetched. Never guess a URL.
12. Never modify files. Only read.
13. Classify each requirement for the bid desk's reporting: `topic` (the closest of the listed topics),
    `security_relevant` (true when it concerns cybersecurity, data protection, access control or secure
    development), `standards` (standards, certifications or regulations the requirement names, e.g.
    "ISO 27001", "IEC 62443-4-1", "NDAA", "GDPR"; empty if none) and, when the answer is not fully
    compliant, `gap_type` (why: the closest of the listed gap types).

How to write `explanation` (the reviewer reads hundreds of these, the evaluator too):
A. Never restate the requirement. The reader knows the question. "Yes, the software can record video
   streams from cameras" tells them nothing. Start with the substance: how it works, the nuance, what
   goes beyond the literal question, the limits. Example for "Does the VMS record video from cameras?":
   "Recording covers more than video: audio and metadata from the cameras are stored with it, so the
   recorded media can later be searched and analysed. Recording can run continuously, on motion or on
   events, per camera." (each fact backed by evidence, as always).
B. For a simple compliant requirement, a short answer is fine, but it must add something the reader did
   not already know from the question.
C. You answer one part of a whole document. Use the tender brief and the earlier answers you are given:
   do not repeat what an earlier answer already explained; refer to it instead ("see 4.2.1") and add only
   what is new for this requirement. State the overall product, edition and architecture once (where the
   brief says), not in every answer.
   Write for the customer: never describe the answer itself or your process ("This answer sets the
   baseline", "as a placeholder", "the documentation could not be read"). Such notes for the bid team
   belong in `gaps`.
D. Vary the openings. Never start like the openings listed as already used, and never start two answers
   in this batch the same way.
E. `considerations`: one or two sentences, in perspective, on what the customer or project should
   consider for this requirement and why it matters for the result (design, sizing, licensing, operation,
   risk, dependency on other parties). It is advice, so it must not state product facts the evidence
   does not back. Leave it empty when there is nothing useful to add; do not pad. Most answers need none,
   and acknowledged answers never get one.

Length (the evaluator reads hundreds of answers; shorter is better when nothing is lost):
- `responsible` is only the party name from the list given, e.g. "Contractor". No details in brackets.
- Another party's scope (acknowledged, responsible is not Milestone): one short sentence, e.g. "Noted.
  This is in the contractor's scope." Add one more sentence only when the VMS has a specific role in it
  that the customer needs to know. No evidence, links, gaps or considerations are needed.
- General scope statements, headings, definitions: "Noted." or "Noted and accepted."
- Simple compliant requirement: at most about {SHORT} words.
- Other answers (complex compliant, partial, not compliant): at most about {LONG} words. Detail for the bid
  team goes in `gaps`, not in the answer."""


def system_prompt(short=40, long=80):
    """The answering instructions, with the length limits of the response persona (persona.limits)."""
    return SYSTEM_PROMPT.replace("{SHORT}", str(short)).replace("{LONG}", str(long))

ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "compliance": {"type": "string"},
        "product": {"type": "string"},
        "explanation": {"type": "string"},
        "considerations": {"type": "string"},
        "responsible": {"type": "string"},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {"source": {"type": "string"}, "quote": {"type": "string"},
                           "where": {"type": "string"}},
            "required": ["source", "quote"]}},
        "links": {"type": "array", "items": {
            "type": "object", "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
            "required": ["url"]}},
        "confidence": {"type": "integer"},
        "gaps": {"type": "string"},
        "question": {"type": "string"},
        "topic": {"type": "string", "enum": register.TOPICS},
        "security_relevant": {"type": "boolean"},
        "standards": {"type": "array", "items": {"type": "string"}},
        "gap_type": {"type": "string", "enum": [""] + register.GAP_TYPES},
    },
    "required": ["key", "compliance", "product", "explanation", "responsible", "evidence", "confidence"],
}


def schema(codes, parties=None):
    s = json.loads(json.dumps(ROW_SCHEMA))
    s["properties"]["compliance"]["enum"] = codes
    if parties:
        s["properties"]["responsible"]["enum"] = list(parties)
    return {"type": "object", "properties": {"responses": {"type": "array", "items": s}},
            "required": ["responses"]}


def parties(settings):
    """The parties an answer can name as responsible (Settings), e.g. Milestone, Contractor, Customer."""
    return [p.strip() for p in (settings.get("responsible_parties") or config.DEFAULT_PARTIES) if p and p.strip()]


# ------------------------------------------------------------------ claude executable

def resolve_claude(path_setting):
    """Find the Claude Code executable.
    Windows: claude.exe; the npm 'claude.cmd' shim is avoided because cmd.exe mangles long arguments.
    macOS: apps started from Finder do not get the shell's PATH, so the usual install places are tried."""
    p = (path_setting or "claude").strip().strip('"')
    if not osdep.IS_WIN:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
        w = shutil.which(p)
        return w or next((c for c in osdep.claude_candidates() if c and os.access(c, os.X_OK)), p)
    if os.path.isfile(p) and p.lower().endswith(".exe"):
        return p
    cand = []
    w = shutil.which(p)
    if w:
        cand.append(w)
    cand += osdep.claude_candidates()
    for c in cand:
        if not c:
            continue
        if c.lower().endswith(".exe") and os.path.isfile(c):
            return c
        if c.lower().endswith((".cmd", "")) and os.path.isfile(c):
            exe = os.path.join(os.path.dirname(c), "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
            if os.path.isfile(exe):
                return exe
    return w or p


def test_claude(settings):
    exe = resolve_claude(settings.get("claude_path"))
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": r.returncode == 0, "exe": exe, "version": (r.stdout or r.stderr).strip()[:200]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "exe": exe, "version": str(e)}


def run_claude(settings, prompt, folders, codes, run_dir, log=None, system=None):
    exe = resolve_claude(settings.get("claude_path"))
    os.makedirs(run_dir, exist_ok=True)
    sp_file = os.path.join(run_dir, "system_prompt.txt")
    with open(sp_file, "w", encoding="utf-8") as f:
        f.write(system or system_prompt())
    tools = ["Read", "Grep", "Glob"]
    allowed = ["Read", "Grep", "Glob"]
    if settings.get("allow_web_lookup") and settings.get("web_domains"):
        tools.append("WebFetch")
        allowed += [f"WebFetch(domain:{d})" for d in settings["web_domains"]]
    cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema(codes, parties(settings))),
           "--no-session-persistence", "--tools", ",".join(tools), "--allowedTools", *allowed,
           "--permission-mode", "dontAsk", "--append-system-prompt-file", sp_file]
    if settings.get("model"):
        cmd += ["--model", settings["model"]]
    if settings.get("effort"):
        cmd += ["--effort", settings["effort"]]
    for d in folders:
        if os.path.isdir(d):
            cmd += ["--add-dir", os.path.realpath(d)]  # long form: Claude Code compares resolved paths
    t0 = time.time()
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.realpath(run_dir), timeout=int(settings.get("timeout_min", 20)) * 60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.strip()
    try:
        data = json.loads(out[out.index("{"):]) if out else {}
    except ValueError:
        data = {}
    with open(os.path.join(run_dir, "claude_output.json"), "w", encoding="utf-8") as f:
        json.dump(data or {"stdout": out[-4000:], "stderr": r.stderr[-4000:]}, f, ensure_ascii=False, indent=1)
    denied = [d.get("tool_input", {}).get("file_path") or d.get("tool_input", {}).get("path") or d.get("tool_name")
              for d in data.get("permission_denials") or []]
    if denied and log:
        log(f"Claude Code was refused access to: {', '.join(map(str, denied[:3]))}")
    if not data or data.get("is_error") or "structured_output" not in data:
        msg = data.get("result") or r.stderr.strip()[-800:] or out[-800:] or f"exit code {r.returncode}"
        raise RuntimeError(f"Claude Code did not return an answer: {msg}")
    if log:
        log(f"Batch answered in {time.time() - t0:.0f}s, cost ${data.get('total_cost_usd', 0):.2f}, "
            f"{data.get('num_turns', 0)} turns")
    return data["structured_output"].get("responses", []), data.get("total_cost_usd", 0)


def claude_call(settings, prompt, run_dir, system, json_schema=None, read_dirs=(), model=None):
    """One headless Claude Code call with read-only file tools on read_dirs (no web).
    Returns (structured output or Markdown text, cost in USD)."""
    exe = resolve_claude(settings.get("claude_path"))
    os.makedirs(run_dir, exist_ok=True)
    sp_file = os.path.join(run_dir, "system_prompt.txt")
    with open(sp_file, "w", encoding="utf-8") as f:
        f.write(system)
    with open(os.path.join(run_dir, "prompt.md"), "w", encoding="utf-8") as f:
        f.write(prompt)
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence", "--tools", "Read,Grep,Glob",
           "--allowedTools", "Read", "Grep", "Glob", "--permission-mode", "dontAsk",
           "--append-system-prompt-file", sp_file]
    if json_schema:
        cmd += ["--json-schema", json.dumps(json_schema)]
    if model or settings.get("model"):
        cmd += ["--model", model or settings["model"]]
    if settings.get("effort"):
        cmd += ["--effort", settings["effort"]]
    for d in read_dirs:
        if os.path.isdir(d):
            cmd += ["--add-dir", os.path.realpath(d)]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=os.path.realpath(run_dir), timeout=int(settings.get("timeout_min", 20)) * 60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.strip()
    try:
        data = json.loads(out[out.index("{"):]) if out else {}
    except ValueError:
        data = {}
    with open(os.path.join(run_dir, "answer.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    if not data or data.get("is_error") or (json_schema and "structured_output" not in data):
        msg = data.get("result") or r.stderr.strip()[-800:] or out[-800:] or f"exit code {r.returncode}"
        raise RuntimeError(f"Claude Code did not return an answer: {msg}")
    denied = [d.get("tool_input", {}).get("file_path") or d.get("tool_name") for d in data.get("permission_denials") or []]
    if denied and read_dirs:
        raise RuntimeError("Claude Code was not allowed to read the data it needed (" + ", ".join(map(str, denied[:3])) +
                           "). Nothing was saved.")
    return (data["structured_output"] if json_schema else data.get("result", "")), data.get("total_cost_usd", 0)


# ------------------------------------------------------------------ prompt

def build_prompt(project, settings, batch, kb, sources, mirrors=None, brief=None):
    """sources: dict label -> {chunk dict} filled here."""
    scale = settings.get("scale") or config.DEFAULT_SCALE
    prods = {p["name"]: p.get("notes", "") for p in settings.get("products", [])}
    L = []
    L.append("# Tender")
    L.append(f"Project: {project.get('name')}")
    if project.get("customer"):
        L.append(f"Customer: {project['customer']}")
    L.append(f"Bidder role: {project.get('role') or 'Vendor'} (we answer as this party)")
    L.append(f"Write the responses in: {project.get('language') or 'English'}")
    L.append("\n# Products to consider")
    for name in project.get("products") or []:
        L.append(f"- {name}" + (f": {prods[name]}" if prods.get(name) else ""))
    if not project.get("products"):
        L.append("- (none chosen: decide from the evidence and say which product you assumed)")
    if project.get("product_rules"):
        L.append(f"Product rules: {project['product_rules']}")
    if project.get("strategy"):
        L.append("\n# Win strategy from the sales team (for emphasis and wording, never to overstate)")
        L.append(project["strategy"].strip())
    for sf in project.get("strategy_files") or []:
        try:
            txt = "\n".join(t for _, t in extract.extract(sf))[:6000]
            L.append(f"\n## Sales input: {os.path.basename(sf)}\n{txt}")
        except (OSError, ValueError):
            pass
    if project.get("instructions"):
        L.append("\n# Extra instructions\n" + project["instructions"].strip())
    L.append(persona.prompt_section(project, settings))
    L.append("\n# Compliance codes (use only these)")
    for s in scale:
        L.append(f"- {s['code']} = {s['label']}: {s.get('meaning', '')}")
    L.append("\n# Responsible parties (use only these in `responsible`)")
    L.append(", ".join(parties(settings)))
    folders = [f["path"] for f in project.get("folders", []) if f.get("path")]
    L.append("\n# Where you may look for more evidence (read only)")
    for f in project.get("folders", []):
        L.append(f"- {os.path.realpath(f['path'])}" + (f"  ({f['label']})" if f.get("label") else ""))
    if kb and project.get("use_kb", True) and hasattr(kb, "folders"):
        for f in kb.folders():
            f = os.path.realpath(f)
            folders.append(f)
            L.append(f"- {f}  (knowledge area source)")
    if settings.get("allow_web_lookup") and settings.get("web_domains"):
        L.append("- Web pages on: " + ", ".join(settings["web_domains"]))
    if not folders:
        L.append("- Only the extracts below.")
    L.append("Search the folders (Grep/Glob/Read) when the extracts below are not enough.")
    if mirrors:
        L.append("Word, Excel, PowerPoint and Outlook files cannot be read directly, so each has a readable text "
                 "copy with the same relative path plus '.txt' in these folders (read only):")
        for md, orig in mirrors.items():
            L.append(f"- {md}  = copies of {orig}")
        L.append("Read and grep the copies. In `evidence`, cite the ORIGINAL file path (without .txt).")

    if brief:
        L.append("\n# Tender brief (the whole document, read beforehand)")
        L.append(brief)
    if len(project.get("rows", [])) > len(batch):
        L.append(f"\n# Document outline ({len(project['rows'])} requirements; you answer {len(batch)} of them now)")
        L.append(context.outline_text(project))
    dg = context.digest(project, batch)
    if dg:
        L.append("\n# Already answered in this document (do not repeat these; refer to them by id and add only "
                 "what is new)")
        L.append(dg)
    ops = context.used_openings(project, batch)
    if ops:
        L.append("\n# Openings already used by other answers (do not start the same way)")
        L.append(", ".join(ops))

    L.append("\n# Requirements to answer")
    for row in batch:
        L.append(f"\n## key={row['key']}  id={row['id']}" + (f"  section: {row['section']}" if row.get("section") else ""))
        L.append(row["text"])
        if row.get("context"):
            L.append(f"Customer guidance for this requirement: {row['context']}")
        if kb:
            hits = kb.search(row["text"] + " " + (row.get("section") or ""), limit=int(settings.get("kb_hits", 8)))
            if hits:
                L.append("### Extracts from the knowledge base")
            for h in hits:
                label = f"S{len(sources) + 1}"
                sources[label] = h
                where = " - ".join(x for x in (h.get("title"), h.get("heading")) if x)
                if h.get("area_name"):
                    where = f"({h['area_name']}) {where}"
                L.append(f"[{label}] {where}\nSource: {h['location']}\n\"\"\"\n{h['text']}\n\"\"\"")
            precs = kb.precedents(row["text"], limit=int(settings.get("precedent_hits", 3)),
                                  exclude_project=project.get("id"))
            if precs:
                L.append("### Earlier approved responses (precedent, not evidence)")
            for i, pr in enumerate(precs, 1):
                L.append(f"[P{i}] {pr.get('project')} / {pr.get('product')}: requirement \"{(pr.get('requirement') or '')[:300]}\" "
                         f"-> {pr.get('compliance')}: {(pr.get('explanation') or '')[:600]}")
    L.append("\nReturn one response per key, using the same key values.")
    return "\n".join(L)


# ------------------------------------------------------------------ verification

def _norm(t):
    t = (t or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("–", "-").replace("—", "-").replace("­", "").replace("​", "")
    t = re.sub(r"[*_`#>|]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class Verifier:
    def __init__(self, kb, settings, folders, sources, mirrors=None):
        self.kb, self.settings, self.sources = kb, settings, sources
        self.mirrors = mirrors or {}
        folders = list(folders) + list(self.mirrors)
        self.roots = [os.path.realpath(f) for f in folders]
        self.folders = [os.path.normcase(f) for f in self.roots]
        self.cache = {}
        self.link_cache = {}

    def _text_for(self, src):
        if src in self.cache:
            return self.cache[src]
        txt = None
        s = src.strip().strip("[]")
        if re.fullmatch(r"S\d+", s) and s in self.sources:
            h = self.sources[s]
            full = self.kb.doc_text(h["location"]) if self.kb else None
            txt = (h["text"] + "\n" + (full or ""))
        elif s.startswith("mssql://"):
            txt = self.kb.doc_text(s) if self.kb else None
        elif s.startswith(("http://", "https://")):
            txt = self.kb.doc_text(s) if self.kb else None
            if txt is None and self._domain_ok(s):
                txt = self._fetch(s)
        else:
            s = self._resolve(s) or s
            path = os.path.normcase(os.path.realpath(s))
            if any(path == f or path.startswith(f + os.sep) for f in self.folders) and os.path.isfile(s):
                txt = (self.kb.doc_text(s) if self.kb else None) or "\n".join(t for _, t in extract.extract(s))
        self.cache[src] = _norm(txt) if txt else None
        return self.cache[src]

    def _resolve(self, s):
        """A file cited by name or relative path ("recording.txt", "manuals/x.pdf"): find it in the allowed
        folders. Only an unambiguous match counts."""
        if not s or os.path.isabs(s):
            return None
        rel = s.replace("/", os.sep).replace("\\", os.sep)
        hits = [os.path.join(f, rel) for f in self.roots if os.path.isfile(os.path.join(f, rel))]
        if not hits and os.sep not in rel:
            name = rel.lower()
            for f in self.roots:
                for root, _dirs, files in os.walk(f):
                    hits += [os.path.join(root, x) for x in files if x.lower() == name]
                    if len(hits) > 1:
                        return None
        return hits[0] if len(hits) == 1 else None

    def source_path(self, src):
        """The full path for a file cited by name, so the reviewer can open it."""
        return self._resolve(src.strip().strip("[]")) or src

    def _domain_ok(self, url):
        host = urllib.parse.urlsplit(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in self.settings.get("web_domains", []))

    def _fetch(self, url):
        try:
            import requests
            r = requests.get(url, timeout=25, headers={"User-Agent": "MilestoneRFPResponder/0.1"})
            if r.status_code != 200:
                return None
            if "pdf" in r.headers.get("content-type", ""):
                import io
                from pypdf import PdfReader
                return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
            return "\n".join(t for _, t in extract.html_sections(r.text)[1])
        except Exception:  # noqa: BLE001
            return None

    def check_quote(self, ev):
        q = _norm(ev.get("quote"))
        if len(q) < 12:
            return False
        src = ev.get("source", "").strip().strip("[]")
        folder = self._allowed_dir(src)
        if folder:  # a folder was cited instead of a file: find the file that holds the quote
            for path in self._files(folder):
                if self._contains(self._text_for(path), q):
                    ev["source"] = path
                    return True
            return False
        return self._contains(self._text_for(ev.get("source", "")), q)

    @staticmethod
    def _contains(text, q):
        if not text:
            return False
        if q in text:
            return True
        parts = [p.strip() for p in re.split(r"\.\.\.|…", q) if len(p.strip()) >= 12]
        return bool(parts) and all(p in text for p in parts)

    def _allowed_dir(self, s):
        if not s or "://" in s or not os.path.isabs(s) or not os.path.isdir(s):
            return None
        p = os.path.normcase(os.path.realpath(s))
        return s if any(p == f or p.startswith(f + os.sep) for f in self.folders) else None

    @staticmethod
    def _files(folder, limit=400):
        n = 0
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith((".", "~", "__"))]
            for x in names:
                if extract.supported(x):
                    yield os.path.join(root, x)
                    n += 1
                    if n >= limit:
                        return

    def check_link(self, url):
        if url in self.link_cache:
            return self.link_cache[url]
        ok = False
        if url.startswith(("http://", "https://")):
            if self.kb and self.kb.doc_state(url):
                ok = True
            else:
                try:
                    import requests
                    r = requests.get(url, timeout=20, allow_redirects=True, stream=True,
                                     headers={"User-Agent": "MilestoneRFPResponder/0.1"})
                    ok = r.status_code < 400
                    r.close()
                except Exception:  # noqa: BLE001
                    ok = False
        self.link_cache[url] = ok
        return ok

    def describe(self, src):
        s = src.strip().strip("[]")
        if s in self.sources:
            h = self.sources[s]
            return h["location"], " - ".join(x for x in (h.get("title"), h.get("heading")) if x)
        if s.startswith(("http", "mssql")):
            return src, ""
        full = self.source_path(s)
        return (readable.to_original(full, self.mirrors) if self.mirrors else full), ""


def apply_answer(row, ans, verifier, scale, strict):
    ack_codes = {s["code"] for s in scale if s["label"].lower().startswith(("ack", "not applicable", "n/a"))}
    row["compliance"] = ans.get("compliance", "")
    row["product"] = ans.get("product", "")
    row["explanation"] = ans.get("explanation", "").strip()
    row["responsible"] = ans.get("responsible", "")
    row["confidence"] = ans.get("confidence")
    row["gaps"] = ans.get("gaps", "") or ""
    row["question"] = ans.get("question", "") or ""
    row["considerations"] = (ans.get("considerations") or "").strip()
    row["topic"] = ans.get("topic") or ""
    row["security"] = ans.get("security_relevant")
    row["standards"] = [s for s in (ans.get("standards") or []) if isinstance(s, str) and s.strip()][:10]
    row["gap_type"] = ans.get("gap_type") or ""
    evs = []
    for ev in ans.get("evidence") or []:
        ok = verifier.check_quote(ev)  # first: it may replace a cited folder with the file that holds the quote
        loc, where = verifier.describe(ev.get("source", ""))
        evs.append({"source": loc, "where": ev.get("where") or where, "quote": ev.get("quote", ""), "verified": ok})
    row["evidence"] = evs
    links = []
    for ln in ans.get("links") or []:
        url = (ln.get("url") or "").strip()
        if url:
            links.append({"title": ln.get("title") or url, "url": url, "ok": verifier.check_link(url)})
    row["links"] = links
    row["verified"] = any(e["verified"] for e in evs)
    needs = False
    notes = []
    if row["compliance"] not in ack_codes and not row["verified"]:
        needs = True
        notes.append("No quote could be verified in the cited sources.")
    conf = row["confidence"] if isinstance(row["confidence"], (int, float)) else None
    if row["compliance"] not in ack_codes and conf is not None and conf < 50:
        needs = True
        notes.append(f"Low confidence ({conf}%): check the claim before approving.")
    bad_links = [ln["url"] for ln in links if not ln["ok"]]
    if bad_links:
        needs = True
        notes.append("Link(s) did not resolve: " + ", ".join(bad_links))
    unverified = sum(1 for e in evs if not e["verified"])
    if unverified and row["verified"]:
        notes.append(f"{unverified} of {len(evs)} quotes could not be matched to the source.")
    if strict and needs:
        row["status"] = "needs_review"
    else:
        row["status"] = "draft"
    row["review_notes"] = " ".join(notes)
    row["answered_at"] = time.strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ run

def respond(project, settings, kb, keys=None, job=None, on_batch=None):
    """Answer the selected rows. on_batch(project) is called after each batch to persist progress."""
    scale = settings.get("scale") or config.DEFAULT_SCALE
    codes = [s["code"] for s in scale]
    rows = [r for r in project["rows"] if (keys is None and r.get("selected")) or (keys and r["key"] in keys)]
    if not rows:
        raise ValueError("No requirements selected")
    folders = [os.path.realpath(f["path"]) for f in project.get("folders", []) if f.get("path")]
    if kb and project.get("use_kb", True) and hasattr(kb, "folders"):
        folders += [os.path.realpath(f) for f in kb.folders() if os.path.realpath(f) not in folders]
    bs = max(1, int(settings.get("batch_size", 5)))
    batches = [rows[i:i + bs] for i in range(0, len(rows), bs)]
    run_dir = os.path.join(config.RUNS_DIR, project["id"], time.strftime("%Y%m%d-%H%M%S"))
    total_cost = 0.0
    done = 0
    errors = 0
    log = job.log if job else (lambda *_: None)
    log(f"Answering {len(rows)} requirements in {len(batches)} batches "
        f"(model {settings.get('model')}, {settings.get('parallel', 2)} in parallel)")
    if job:
        job.update(message="Preparing readable copies of Word/Excel files")
    mirrors = readable.prepare(folders, kb, job)
    claude_dirs = folders + list(mirrors)
    brief = None
    if settings.get("tender_brief", True) and len(project.get("rows", [])) > 1:
        try:
            brief = context.ensure_brief(project, settings, job)
            if on_batch:
                on_batch(project)
        except Exception as e:  # noqa: BLE001 - answering still works without the brief
            log(f"No tender brief ({e}); answering without it")
    try:
        if persona.ensure_brief(project, settings, job) and on_batch:
            on_batch(project)
    except Exception as e:  # noqa: BLE001 - answering still works with the audience settings alone
        log(f"No persona brief ({e}); answering with the audience settings only")
    log(f"Response persona: {persona.summary(project, settings)}")
    sysprompt = system_prompt(*persona.limits(project, settings))

    def work(i, batch):
        if job and job.cancelled:
            return i, batch, None, {}, 0
        sources = {}
        prompt = build_prompt(project, settings, batch, kb, sources, mirrors, brief)
        bdir = os.path.join(run_dir, f"batch{i + 1:03d}")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "prompt.md"), "w", encoding="utf-8") as f:
            f.write(prompt)
        answers, cost = run_claude(settings, prompt, claude_dirs, codes, bdir, log, sysprompt)
        with open(os.path.join(bdir, "answers.json"), "w", encoding="utf-8") as f:
            json.dump(answers, f, ensure_ascii=False, indent=1)
        # apply right away, so the next batch this worker picks up already sees these answers
        ver = Verifier(kb, settings, folders, sources, mirrors)
        by_key = {a.get("key"): a for a in answers}
        with apply_lock:
            for row in batch:
                a = by_key.get(row["key"])
                if a:
                    apply_answer(row, a, ver, scale, settings.get("strict_evidence", True))
                    flags = context.style_check(row, project)
                    if flags:
                        note = " ".join(context.STYLE_NOTES[f] for f in flags)
                        row["review_notes"] = (row.get("review_notes", "") + " Style: " + note).strip()
                else:
                    log(f"No answer returned for {row['id']}")
        return i, batch, answers, sources, cost

    apply_lock = threading.Lock()

    def finished(fut):
        nonlocal total_cost, done, errors
        try:
            _i, batch, answers, _sources, cost = fut.result()
        except Exception as e:  # noqa: BLE001
            errors += 1
            log(f"A batch failed: {e}")
            return
        if answers is None:
            return
        total_cost += cost or 0
        done += len(batch)
        if job:
            job.update(progress=done / len(rows), message=f"{done}/{len(rows)} answered")
        if on_batch:
            on_batch(project)

    with cf.ThreadPoolExecutor(max_workers=max(1, int(settings.get("parallel", 2)))) as ex:
        # the first batch alone, so the others start from its answers (tone, what has been said)
        first = ex.submit(work, 0, batches[0])
        cf.wait([first])
        finished(first)
        futs = [ex.submit(work, i, b) for i, b in enumerate(batches) if i > 0]
        for fut in cf.as_completed(futs):
            finished(fut)
    project.setdefault("runs", []).append({"at": time.strftime("%Y-%m-%d %H:%M"), "rows": len(rows),
                                           "cost_usd": round(total_cost, 2), "model": settings.get("model"),
                                           "errors": errors, "dir": run_dir})
    if on_batch:
        on_batch(project)
    review = sum(1 for r in rows if r.get("status") == "needs_review")
    log(f"Done: {done} answered, {review} need review, {errors} failed batches, cost ${total_cost:.2f}")
    return {"answered": done, "needs_review": review, "errors": errors, "cost_usd": round(total_cost, 2)}
