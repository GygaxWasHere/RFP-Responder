"""Reports on the requirements register, for product management, security & compliance and marketing.

Claude reports: the filtered register is written as CSV/JSON files into a run folder. Claude Code
reads them (read-only tools on that folder only) and writes a Markdown report that must stay with
the data: counts, examples with tender and requirement id, facts kept apart from interpretation.
The report is saved to <shared folder>/reports as Markdown and Word, so colleagues can open it too.
"""
import csv
import json
import os
import re
import time

from . import config, engine, register

PRESETS = {
    "product": {
        "label": "Product management",
        "description": "Where and why the product is not compliant: missing capabilities by product and topic, "
                       "how often customers ask, and what they ask for exactly.",
        "filters": {"compliance": "gaps"},
        "focus": "You write for product managers. Explain where the offered products are partly or not compliant, "
                 "grouped into concrete capability gaps (merge requirements that ask for the same thing). For each gap: "
                 "how many tenders and requirements ask for it, in which verticals/regions, the gap type, typical "
                 "customer wording (short quotes with tender and requirement id) and what the bid team answered. "
                 "Rank gaps by how often and how broadly they occur. End with suggested roadmap questions."},
    "security": {
        "label": "Security & compliance",
        "description": "Security-relevant requirements and the standards customers ask for; where we are not "
                       "compliant and what that says about how the product is built and certified.",
        "filters": {"security": True},
        "focus": "You write for the security and compliance team. Analyse the security-relevant requirements: which "
                 "standards, certifications and regulations customers ask for (counts), where we are partly or not "
                 "compliant, and what the gaps say about secure development, hardening, certification and "
                 "documentation. Group findings by theme (e.g. encryption, identity, secure development lifecycle, "
                 "vulnerability handling, certifications). Flag anything that looks like a systemic gap. Quote the "
                 "customer wording with tender and requirement id."},
    "marketing": {
        "label": "Marketing",
        "description": "What is happening in tenders: verticals, regions, who evaluates, why customers tender, "
                       "what concerns them and which themes come up again and again.",
        "filters": {},
        "focus": "You write for marketing. Describe the tender landscape: verticals, regions and countries, typical "
                 "customers, consultants/evaluators and partners involved, why customers run these tenders, what "
                 "concerns them, recurring themes and what they value. Note how compliance and outcomes differ by "
                 "vertical where the data allows. Suggest messaging and content opportunities, clearly marked as "
                 "suggestions."},
    "all": {"label": "All requirements", "description": "Every collected requirement, filter freely.", "filters": {},
            "focus": "You write for colleagues at Milestone. Answer the question asked, from the data."},
}

REPORT_SYSTEM = """You are an analyst at Milestone Systems. You write internal reports from a register of tender
requirements and the bid desk's compliance answers.

Rules:
1. Use only the data files you are given (read them with Read/Grep). Do not invent numbers, customers or facts.
2. Give counts for every claim ("14 requirements in 6 tenders"). When you quote a requirement, add the tender
   name and requirement id in brackets.
3. Keep facts and interpretation apart: findings first, then "What this may mean" or "Suggestions", marked as such.
4. Say when the data is thin (few tenders, short period) and what is missing.
5. Write in clear, plain English. Use Markdown: # title, ## sections, short paragraphs, bullet lists, and
   Markdown tables for rankings.
6. Structure: title; Summary (5-8 bullets); Findings (sections); Suggestions / questions to follow up;
   About the data (filters, number of tenders and requirements, period).
7. Output only the report in Markdown."""

PROFILE_SYSTEM = """You read tender documents and fill in a short profile of the tender for the bid desk's
reporting. Use only what the text says or clearly implies. Leave a field empty when the text does not tell.
Never guess a country or a consultant."""


# ------------------------------------------------------------------ Claude report

def _describe(f):
    parts = []
    for k, v in (f or {}).items():
        if v in (None, "", False, []):
            continue
        if k == "compliance" and v == "gaps":
            parts.append("only partly or not compliant answers")
        elif k == "security":
            parts.append("only security-relevant requirements")
        elif k == "approved_only":
            parts.append("only approved answers")
        else:
            parts.append(f"{k} = {v if not isinstance(v, list) else ', '.join(v)}")
    return "; ".join(parts) or "no filters (all collected requirements)"


def build_report(reg, settings, preset, filters, question, user, root, job=None):
    log = job.log if job else (lambda *_: None)
    scale = settings.get("scale") or config.DEFAULT_SCALE
    p = PRESETS.get(preset) or PRESETS["all"]
    f = {**p["filters"], **(filters or {})}
    summ = reg.summary(f, scale)
    if not summ["total"]:
        raise ValueError("No requirements match these filters, so there is nothing to report on.")
    rows = reg.rows(f, scale, limit=20000)
    run_dir = os.path.join(config.RUNS_DIR, "reports", time.strftime("%Y%m%d-%H%M%S"))
    data_dir = os.path.join(run_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    data_dir = os.path.realpath(data_dir)  # the same long path Claude Code is given access to
    cols = ["tender", "customer", "vertical", "country", "region", "outcome", "rid", "section", "text",
            "compliance_label", "product", "topic", "security", "standards", "gap_type", "explanation", "gaps",
            "question", "status"]
    with open(os.path.join(data_dir, "requirements.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([register.Register._cell(k, r.get(k)) for k in cols])
    tcols = [k for k, _ in register.Register.TCOLS]
    with open(os.path.join(data_dir, "tenders.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(tcols)
        for t in summ["tenders"]:
            w.writerow([register.Register._cell(k, t.get(k)) for k in tcols])
    small = {k: v for k, v in summ.items() if k != "tenders"}
    with open(os.path.join(data_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(small, fh, ensure_ascii=False, indent=1)
    labels = {s["code"]: s["label"] for s in scale}
    prompt = "\n".join([
        f"# Report request ({p['label']})",
        p["focus"],
        "",
        f"Question from {user or 'a colleague'}: {question.strip() if question else '(none: give the standard overview for this audience)'}",
        "",
        "# Data",
        f"Filters: {_describe(f)}",
        f"{summ['total']} requirements from {summ['tenders_n']} tenders. Compliance: "
        + ", ".join(f"{labels.get(c['k'], c['k'])} {c['n']}" for c in summ["compliance"]),
        f"Files (read them; requirements.csv has one row per requirement):",
        f"- {os.path.join(data_dir, 'requirements.csv')}",
        f"- {os.path.join(data_dir, 'tenders.csv')}  (tender profiles: vertical, country, consultant, purpose, concerns...)",
        f"- {os.path.join(data_dir, 'summary.json')}  (counts by topic, product, vertical, region, standards...)",
        f"Today is {time.strftime('%Y-%m-%d')}. Write the report now."])
    log(f"Report on {summ['total']} requirements from {summ['tenders_n']} tenders ({p['label']})")
    md, cost = engine.claude_call(settings, prompt, run_dir, REPORT_SYSTEM, read_dirs=[data_dir])
    md = md.strip()
    title = next((ln.lstrip("# ").strip() for ln in md.splitlines() if ln.startswith("# ")), f"{p['label']} report")
    out_dir = os.path.join(root, "reports")
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r'[<>:"/\\|?*]+', "", f"{time.strftime('%Y-%m-%d %H%M')} {p['label']} - {title}")[:120].strip()
    header = (f"<!-- RFP Responder report · {p['label']} · by {user} · {time.strftime('%Y-%m-%d %H:%M')} · "
              f"filters: {_describe(f)} · {summ['total']} requirements / {summ['tenders_n']} tenders -->\n\n")
    md_path = os.path.join(out_dir, stem + ".md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(header + md)
    docx_path = os.path.join(out_dir, stem + ".docx")
    md_to_docx(md, docx_path, footer=f"Generated by RFP Responder with Claude on {time.strftime('%Y-%m-%d')} for "
                                     f"{user}. Filters: {_describe(f)}. Check the figures before you share them outside.")
    log(f"Report saved: {md_path} (cost ${cost:.3f})")
    return {"title": title, "markdown": md, "md": md_path, "docx": docx_path, "cost_usd": round(cost or 0, 3),
            "rows": summ["total"], "tenders": summ["tenders_n"]}


def list_reports(root):
    d = os.path.join(root, "reports")
    if not os.path.isdir(d):
        return []
    out = []
    for n in os.listdir(d):
        if n.endswith(".md"):
            p = os.path.join(d, n)
            docx = p[:-3] + ".docx"
            out.append({"name": n[:-3], "md": p, "docx": docx if os.path.exists(docx) else "",
                        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def read_report(path, root):
    d = os.path.normcase(os.path.abspath(os.path.join(root, "reports")))
    if not os.path.normcase(os.path.abspath(path)).startswith(d + os.sep):
        raise ValueError("Not a report")
    with open(path, encoding="utf-8") as fh:
        return re.sub(r"^<!--.*?-->\s*", "", fh.read(), flags=re.S)


# ------------------------------------------------------------------ Markdown -> Word

def _runs(par, text):
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if part.startswith("**") and part.endswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            par.add_run(part[1:-1]).italic = True
        elif part:
            par.add_run(re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", part))


def md_to_docx(md, path, footer=""):
    import docx
    d = docx.Document()
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip() or ln.strip().startswith("<!--"):
            i += 1
            continue
        if ln.lstrip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                block.append(lines[i].strip().strip("|"))
                i += 1
            rows = [[c.strip() for c in b.split("|")] for b in block if not re.fullmatch(r"[\s:\-|]+", b)]
            if rows:
                t = d.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                t.style = "Light Grid Accent 1"
                for ri, r in enumerate(rows):
                    for ci, c in enumerate(r):
                        cell = t.cell(ri, ci)
                        cell.text = ""
                        _runs(cell.paragraphs[0], c)
                        if ri == 0:
                            for run in cell.paragraphs[0].runs:
                                run.bold = True
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            d.add_heading(m.group(2).strip(), level=min(len(m.group(1)) - 1, 3) if len(m.group(1)) > 1 else 0)
        elif re.match(r"^\s*[-*•]\s+", ln):
            _runs(d.add_paragraph(style="List Bullet" if not ln.startswith("  ") else "List Bullet 2"),
                  re.sub(r"^\s*[-*•]\s+", "", ln))
        elif re.match(r"^\s*\d+[.)]\s+", ln):
            _runs(d.add_paragraph(style="List Number"), re.sub(r"^\s*\d+[.)]\s+", "", ln))
        elif ln.startswith(">"):
            _runs(d.add_paragraph(style="Intense Quote"), ln.lstrip("> "))
        else:
            _runs(d.add_paragraph(), ln.strip())
        i += 1
    if footer:
        p = d.add_paragraph()
        r = p.add_run(footer)
        r.italic = True
    d.save(path)


# ------------------------------------------------------------------ tender profile suggestion

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "vertical": {"type": "string", "enum": [""] + register.VERTICALS},
        "country": {"type": "string"},
        "region": {"type": "string", "enum": [""] + register.REGIONS},
        "end_user_type": {"type": "string"},
        "consultant": {"type": "string"},
        "purpose": {"type": "string"},
        "concerns": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["vertical", "country", "region", "purpose", "concerns", "themes"],
}


def suggest_profile(project, settings, job=None):
    rows = project.get("rows") or []
    if not rows:
        raise ValueError("Load the requirements first; the profile is read from them.")
    text, n = [], 0
    for r in rows:
        line = f"[{r.get('id')}] {r.get('section') or ''}: {r.get('text')}"
        n += len(line)
        if n > 24000:
            break
        text.append(line)
    prompt = "\n".join([
        f"Tender: {project.get('name')}", f"Customer: {project.get('customer') or '(not given)'}",
        f"Requirements file: {os.path.basename(project.get('req_source', {}).get('path') or '')}",
        f"{len(rows)} requirements; the first {len(text)} follow.", "", *text, "",
        "Fill in the profile:",
        "- vertical: the customer's market, from the list.",
        "- country / region (EMEA, Americas, APAC): only if the text says so.",
        "- end_user_type: e.g. international airport, municipality, hospital group.",
        "- consultant: the consultant, specifier or evaluator named in the documents, if any.",
        "- purpose: 1-3 sentences on why the customer runs this tender (new system, replacement, expansion, "
        "regulation, incident...).",
        "- concerns: 1-3 sentences on what the customer worries about most, judging by what they stress.",
        "- themes: 3-8 short recurring themes (e.g. 'cybersecurity', 'open platform', 'analytics', 'redundancy')."])
    run_dir = os.path.join(config.RUNS_DIR, project["id"], "profile-" + time.strftime("%Y%m%d-%H%M%S"))
    out, cost = engine.claude_call(settings, prompt, run_dir, PROFILE_SYSTEM, json_schema=PROFILE_SCHEMA,
                                   model=settings.get("report_model") or None)
    if job:
        job.log(f"Profile suggested (cost ${cost:.2f})")
    return {"profile": out, "cost_usd": round(cost or 0, 3)}
