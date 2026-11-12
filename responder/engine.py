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
import time
import urllib.parse

from . import config, extract

SYSTEM_PROMPT = """You are a senior bid and compliance specialist. You write compliance responses to tender
requirements on behalf of the bidder. The responses will be used in real tender submissions and can become
legally binding, so accuracy matters more than a positive answer.

Rules:
1. Base every compliance statement on evidence. Evidence is the numbered extracts given to you ([S..]),
   files you read in the allowed folders, or pages you fetch on the allowed domains.
2. For each claim, copy a short exact quote (one or two sentences, word for word, no paraphrase, no
   ellipsis inside a sentence) from the source into `evidence`, with the source id ([S..] label without
   brackets), the full file path, or the URL. Quotes are checked automatically against the source;
   invented or reworded quotes are rejected.
3. Every factual sentence in `explanation` must be backed by your evidence. Do not speculate or add
   facts you have not seen ("may", "probably", "some components might"); put open points in `gaps`.
   Before you settle on partial or non-compliant, search the allowed folders and web pages for
   confirming evidence.
4. If the evidence does not clearly support the requirement, do not claim compliance. Use the partial or
   non-compliant code, explain the gap in `gaps`, lower `confidence`, and suggest a clarification
   question where useful.
5. Earlier approved responses ([P..]) show how the team answered similar requirements before. Use them
   for wording and consistency, but they are not evidence on their own: confirm the facts in a source.
6. Use the acknowledgement code for requirements that are not a product capability (installation,
   construction, project management, the customer's own processes). Say who is responsible.
7. Name the product(s) you considered for each requirement in `product`, and say in the explanation
   which product, module or licence delivers the function. Use only the products you are told to
   consider, and respect the product rules.
8. Respect the bidder's role: if the bidder is the software vendor, do not commit to integrator or
   customer tasks; state them as their responsibility.
9. Write plain, neutral, tender-safe language: short sentences, no marketing superlatives, no
   "fully", "always" or "guaranteed" unless the source says so. 2-5 sentences per explanation.
10. Use the win strategy to choose emphasis and wording, never to overstate a capability.
11. `links` are public URLs the customer can open to read more (product documentation). Only give URLs
    you have seen in the extracts or fetched. Never guess a URL.
12. Never modify files. Only read."""

ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "compliance": {"type": "string"},
        "product": {"type": "string"},
        "explanation": {"type": "string"},
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
    },
    "required": ["key", "compliance", "product", "explanation", "responsible", "evidence", "confidence"],
}


def schema(codes):
    s = json.loads(json.dumps(ROW_SCHEMA))
    s["properties"]["compliance"]["enum"] = codes
    return {"type": "object", "properties": {"responses": {"type": "array", "items": s}},
            "required": ["responses"]}


# ------------------------------------------------------------------ claude executable

def resolve_claude(path_setting):
    """Find claude.exe. The npm 'claude.cmd' shim is avoided: cmd.exe mangles long arguments."""
    p = (path_setting or "claude").strip().strip('"')
    if os.path.isfile(p) and p.lower().endswith(".exe"):
        return p
    cand = []
    w = shutil.which(p)
    if w:
        cand.append(w)
    cand += [shutil.which("claude.exe") or "", shutil.which("claude.cmd") or "",
             os.path.expandvars(r"%USERPROFILE%\.local\bin\claude.exe"),
             os.path.expandvars(r"%APPDATA%\npm\claude.cmd")]
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


def run_claude(settings, prompt, folders, codes, run_dir, log=None):
    exe = resolve_claude(settings.get("claude_path"))
    os.makedirs(run_dir, exist_ok=True)
    sp_file = os.path.join(run_dir, "system_prompt.txt")
    with open(sp_file, "w", encoding="utf-8") as f:
        f.write(SYSTEM_PROMPT)
    tools = ["Read", "Grep", "Glob"]
    allowed = ["Read", "Grep", "Glob"]
    if settings.get("allow_web_lookup") and settings.get("web_domains"):
        tools.append("WebFetch")
        allowed += [f"WebFetch(domain:{d})" for d in settings["web_domains"]]
    cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema(codes)),
           "--no-session-persistence", "--tools", ",".join(tools), "--allowedTools", *allowed,
           "--permission-mode", "dontAsk", "--append-system-prompt-file", sp_file]
    if settings.get("model"):
        cmd += ["--model", settings["model"]]
    if settings.get("effort"):
        cmd += ["--effort", settings["effort"]]
    for d in folders:
        if os.path.isdir(d):
            cmd += ["--add-dir", d]
    t0 = time.time()
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=run_dir, timeout=int(settings.get("timeout_min", 20)) * 60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.strip()
    try:
        data = json.loads(out[out.index("{"):]) if out else {}
    except ValueError:
        data = {}
    if not data or data.get("is_error") or "structured_output" not in data:
        msg = data.get("result") or r.stderr.strip()[-800:] or out[-800:] or f"exit code {r.returncode}"
        raise RuntimeError(f"Claude Code did not return an answer: {msg}")
    if log:
        log(f"Batch answered in {time.time() - t0:.0f}s, cost ${data.get('total_cost_usd', 0):.2f}, "
            f"{data.get('num_turns', 0)} turns")
    return data["structured_output"].get("responses", []), data.get("total_cost_usd", 0)


# ------------------------------------------------------------------ prompt

def build_prompt(project, settings, batch, kb, sources):
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
    L.append("\n# Compliance codes (use only these)")
    for s in scale:
        L.append(f"- {s['code']} = {s['label']}: {s.get('meaning', '')}")
    folders = [f["path"] for f in project.get("folders", []) if f.get("path")]
    L.append("\n# Where you may look for more evidence (read only)")
    for f in project.get("folders", []):
        L.append(f"- {f['path']}" + (f"  ({f['label']})" if f.get("label") else ""))
    if settings.get("allow_web_lookup") and settings.get("web_domains"):
        L.append("- Web pages on: " + ", ".join(settings["web_domains"]))
    if not folders:
        L.append("- Only the extracts below.")
    L.append("Search the folders (Grep/Glob/Read) when the extracts below are not enough. "
             "Word and Excel files cannot be read directly; prefer PDF, text and the extracts.")

    L.append("\n# Requirements to answer")
    for row in batch:
        L.append(f"\n## key={row['key']}  id={row['id']}" + (f"  section: {row['section']}" if row.get("section") else ""))
        L.append(row["text"])
        if row.get("context"):
            L.append(f"Customer guidance for this requirement: {row['context']}")
        if kb and project.get("use_kb", True):
            hits = kb.search(row["text"] + " " + (row.get("section") or ""), limit=int(settings.get("kb_hits", 8)))
            if hits:
                L.append("### Extracts from the knowledge base")
            for h in hits:
                label = f"S{len(sources) + 1}"
                sources[label] = h
                where = " - ".join(x for x in (h.get("title"), h.get("heading")) if x)
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
    def __init__(self, kb, settings, folders, sources):
        self.kb, self.settings, self.sources = kb, settings, sources
        self.folders = [os.path.normcase(os.path.abspath(f)) for f in folders]
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
        elif s.startswith(("http://", "https://")):
            txt = self.kb.doc_text(s) if self.kb else None
            if txt is None and self._domain_ok(s):
                txt = self._fetch(s)
        else:
            path = os.path.normcase(os.path.abspath(s))
            if any(path.startswith(f) for f in self.folders) and os.path.isfile(s):
                txt = (self.kb.doc_text(s) if self.kb else None) or "\n".join(t for _, t in extract.extract(s))
        self.cache[src] = _norm(txt) if txt else None
        return self.cache[src]

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
        text = self._text_for(ev.get("source", ""))
        q = _norm(ev.get("quote"))
        if not text or len(q) < 12:
            return False
        if q in text:
            return True
        parts = [p.strip() for p in re.split(r"\.\.\.|…", q) if len(p.strip()) >= 12]
        return bool(parts) and all(p in text for p in parts)

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
        return src, ""


def apply_answer(row, ans, verifier, scale, strict):
    ack_codes = {s["code"] for s in scale if s["label"].lower().startswith(("ack", "not applicable", "n/a"))}
    row["compliance"] = ans.get("compliance", "")
    row["product"] = ans.get("product", "")
    row["explanation"] = ans.get("explanation", "").strip()
    row["responsible"] = ans.get("responsible", "")
    row["confidence"] = ans.get("confidence")
    row["gaps"] = ans.get("gaps", "") or ""
    row["question"] = ans.get("question", "") or ""
    evs = []
    for ev in ans.get("evidence") or []:
        loc, where = verifier.describe(ev.get("source", ""))
        evs.append({"source": loc, "where": ev.get("where") or where, "quote": ev.get("quote", ""),
                    "verified": verifier.check_quote(ev)})
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
    folders = [f["path"] for f in project.get("folders", []) if f.get("path")]
    bs = max(1, int(settings.get("batch_size", 5)))
    batches = [rows[i:i + bs] for i in range(0, len(rows), bs)]
    run_dir = os.path.join(config.RUNS_DIR, project["id"], time.strftime("%Y%m%d-%H%M%S"))
    total_cost = 0.0
    done = 0
    errors = 0
    log = job.log if job else (lambda *_: None)
    log(f"Answering {len(rows)} requirements in {len(batches)} batches "
        f"(model {settings.get('model')}, {settings.get('parallel', 2)} in parallel)")

    def work(i, batch):
        if job and job.cancelled:
            return i, batch, None, {}, 0
        sources = {}
        prompt = build_prompt(project, settings, batch, kb, sources)
        bdir = os.path.join(run_dir, f"batch{i + 1:03d}")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "prompt.md"), "w", encoding="utf-8") as f:
            f.write(prompt)
        answers, cost = run_claude(settings, prompt, folders, codes, bdir, log)
        with open(os.path.join(bdir, "answers.json"), "w", encoding="utf-8") as f:
            json.dump(answers, f, ensure_ascii=False, indent=1)
        return i, batch, answers, sources, cost

    with cf.ThreadPoolExecutor(max_workers=max(1, int(settings.get("parallel", 2)))) as ex:
        futs = [ex.submit(work, i, b) for i, b in enumerate(batches)]
        for fut in cf.as_completed(futs):
            try:
                i, batch, answers, sources, cost = fut.result()
            except Exception as e:  # noqa: BLE001
                errors += 1
                log(f"A batch failed: {e}")
                continue
            if answers is None:
                continue
            total_cost += cost or 0
            ver = Verifier(kb, settings, folders, sources)
            by_key = {a.get("key"): a for a in answers}
            for row in batch:
                a = by_key.get(row["key"])
                if a:
                    apply_answer(row, a, ver, scale, settings.get("strict_evidence", True))
                else:
                    log(f"No answer returned for {row['id']}")
            done += len(batch)
            if job:
                job.update(progress=done / len(rows), message=f"{done}/{len(rows)} answered")
            if on_batch:
                on_batch(project)
    project.setdefault("runs", []).append({"at": time.strftime("%Y-%m-%d %H:%M"), "rows": len(rows),
                                           "cost_usd": round(total_cost, 2), "model": settings.get("model"),
                                           "errors": errors, "dir": run_dir})
    if on_batch:
        on_batch(project)
    review = sum(1 for r in rows if r.get("status") == "needs_review")
    log(f"Done: {done} answered, {review} need review, {errors} failed batches, cost ${total_cost:.2f}")
    return {"answered": done, "needs_review": review, "errors": errors, "cost_usd": round(total_cost, 2)}
