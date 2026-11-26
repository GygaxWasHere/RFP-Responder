"""Response persona: write the answers in a style that fits the bid manager, the readers and the customer.

  my style     Per user (Settings). The bid manager links responses they are proud of (files or pasted
               text). Claude reads them once and writes a style guide: tone, structure, vocabulary, level of
               detail. It is used in every response that user runs.
  standard     A response uses the user's style guide and a balanced audience.
  custom       A response is aimed at this tender:
               - audience: an "equalizer" over the five buying-committee roles seen in tenders (Global Bid
                 Desk, Nov 2026): procurement, requirement owner, specifier, gatekeeper, approver;
               - detail and tone;
               - mirroring: the customer's web pages, annual report or strategy documents, for their own
                 words and what concerns them;
               - scoring: the customer's evaluation criteria, pasted from the RFP.
               Claude turns these into a persona brief for the tender, which every batch gets. The bid
               manager can edit the brief.

The persona shapes tone, emphasis, vocabulary and detail. It never changes compliance, confidence or the
evidence rules.
"""
import hashlib
import io
import json
import os
import time

from . import config, extract

# ------------------------------------------------------------------ buying committee

ROLES = [
    {"key": "procurement", "name": "Procurement", "title": "Process owner",
     "who": "procurement, purchasing, contracting officer, category manager",
     "cares": "compliance with the process; complete, comparable answers; licence model and total cost; "
              "acceptance of contract terms, DPA and insurance; supplier risk (financial stability, ESG, references)",
     "write": "Answer exactly what is asked, in the customer's order and terms, so it is easy to compare and score. "
              "Explain licensing and cost drivers in plain words. No technical depth they cannot score. "
              "They are a filter to pass, not a person to persuade on features."},
    {"key": "owner", "name": "Requirement owner", "title": "Security, IT, police, loss prevention, facilities, OT",
     "who": "the security engineer or operational team that wrote the requirement and will use the system",
     "cares": "fit to their operational use cases; integration with the existing estate (cameras, access control, "
              "other systems); migration from the incumbent; open architecture",
     "write": "Use operational language: how it works day to day for operators and administrators, which "
              "integrations exist, what migration looks like. Concrete over abstract."},
    {"key": "specifier", "name": "Specifier", "title": "Consultant / engineer",
     "who": "independent security consultant, engineer of record, A&E firm",
     "cares": "performance specifications; equivalence to named products; approved-manufacturer lists; "
              "measurable, standard-referenced statements",
     "write": "Be precise and measurable, in specification language. Where the text names a product or 'or "
              "equal', make equivalence explicit. Reference standards. They decide equivalence at their own "
              "discretion, so leave nothing to interpretation."},
    {"key": "gatekeeper", "name": "Gatekeeper", "title": "IT/cyber, legal, data protection",
     "who": "IT security, network team, legal, data protection officer, works council",
     "cares": "cyber questionnaires, penetration tests; ISO 27001, IEC 62443, NIS2, CRA, FIPS; cloud policy; "
              "GDPR and data processing agreements; identity (AD/SSO)",
     "write": "Pass/fail mindset: name the control, the standard and the evidence. State data flows, encryption "
              "and hardening plainly. No ambiguity, no marketing."},
    {"key": "approver", "name": "Approver", "title": "Board, council, steering committee",
     "who": "the body that formally awards: council, board, contracting officer, executive committee",
     "cares": "budget, risk and a defensible decision",
     "write": "Lead with the outcome and the risk it removes. Cost transparency, references, continuity. Short; "
              "they read summaries, rarely single answers."},
]
ROLE = {r["key"]: r for r in ROLES}
LEVELS = ["Not a reader", "Secondary", "Important", "Primary"]

PRESETS = {
    "balanced": {"label": "Balanced panel",
                 "hint": "Mixed evaluation panel; nothing special is known about the evaluators.",
                 "audience": {"procurement": 2, "owner": 2, "specifier": 1, "gatekeeper": 1, "approver": 1}},
    "public": {"label": "Public, procurement-led",
               "hint": "Owner-issued public tender with published weights; procurement runs it, the "
                       "requirement owner scores the technical part.",
               "audience": {"procurement": 3, "owner": 2, "specifier": 0, "gatekeeper": 2, "approver": 1}},
    "consultant": {"label": "Consultant-led",
                   "hint": "Written or run by a consultant or engineer (typical in the UK, the Middle East and "
                           "North American construction).",
                   "audience": {"procurement": 1, "owner": 1, "specifier": 3, "gatekeeper": 1, "approver": 0}},
    "technical": {"label": "Technical panel",
                  "hint": "Scored by security and IT specialists; demos and use cases weigh most.",
                  "audience": {"procurement": 1, "owner": 3, "specifier": 1, "gatekeeper": 2, "approver": 0}},
    "private": {"label": "Private enterprise",
                "hint": "Invitation-only, category management with a business owner; 'fit', TCO and contract "
                        "terms, criteria often undisclosed.",
                "audience": {"procurement": 2, "owner": 3, "specifier": 0, "gatekeeper": 2, "approver": 2}},
    "cyber": {"label": "Cyber gate",
              "hint": "Security questionnaire or annex: IT security and legal can eliminate the bid regardless of "
                      "score.",
              "audience": {"procurement": 1, "owner": 1, "specifier": 0, "gatekeeper": 3, "approver": 0}},
}

DETAIL = {
    "concise": {"label": "Concise", "short": 25, "long": 50,
                "text": "Concise: the essentials only, one or two sentences per answer."},
    "standard": {"label": "Standard", "short": 40, "long": 80,
                 "text": "Standard: short answers that add something beyond the question."},
    "detailed": {"label": "Detailed", "short": 60, "long": 130,
                 "text": "Detailed: explain how it works and the relevant options, still without padding."},
}
TONE = {
    "neutral": {"label": "Neutral & factual",
                "text": "Neutral and factual: plain statements, no persuasion."},
    "confident": {"label": "Confident",
                  "text": "Confident: clear, active statements that show ownership of the solution, still "
                          "without superlatives."},
    "engaged": {"label": "Engaged",
                "text": "Engaged: benefit-led; connect each capability to what it achieves for this customer "
                        "and show commitment to their project. Every benefit must follow from the evidence."},
}


def default_persona():
    return {"mode": "standard", "preset": "balanced", "audience": dict(PRESETS["balanced"]["audience"]),
            "detail": "standard", "tone": "neutral", "mirror_urls": [], "mirror_files": [], "scoring": ""}


def persona_of(project):
    p = project.get("persona") or {}
    base = default_persona()
    for k, v in base.items():
        p.setdefault(k, v)
    for k, v in base["audience"].items():
        p["audience"].setdefault(k, v)
    return p


def effective(project, settings):
    """(detail key, tone key) that apply: chosen per tender in custom mode, else learned from the user's
    examples, else standard / neutral."""
    p = persona_of(project)
    if p["mode"] == "custom":
        return p.get("detail") or "standard", p.get("tone") or "neutral"
    st = settings.get("style") or {}
    return st.get("detail") or "standard", st.get("tone") or "neutral"


def limits(project, settings):
    d = DETAIL.get(effective(project, settings)[0], DETAIL["standard"])
    return d["short"], d["long"]


# ------------------------------------------------------------------ reading material

def _clip(t, n):
    t = (t or "").strip()
    return t if len(t) <= n else t[:n] + " …"


def read_file(path, limit=20000):
    return _clip("\n".join(f"{h}\n{t}" if h else t for h, t in extract.extract(path)), limit)


def read_url(url, limit=20000, max_pdf_pages=40):
    import requests
    from .ingest import USER_AGENT
    r = requests.get(url, timeout=40, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    if "pdf" in r.headers.get("content-type", "") or url.lower().split("?")[0].endswith(".pdf"):
        from pypdf import PdfReader
        pages = PdfReader(io.BytesIO(r.content)).pages[:max_pdf_pages]
        return _clip("\n".join(p.extract_text() or "" for p in pages), limit)
    r.encoding = r.encoding or "utf-8"
    title, sections = extract.html_sections(r.text)
    return _clip((title + "\n" if title else "") + "\n".join(f"{h}\n{t}" if h else t for h, t in sections), limit)


def _hash(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


# ------------------------------------------------------------------ my style (per user)

STYLE_SYSTEM = """You are a senior bid editor. You study tender responses a bid manager is proud of and describe
their writing style so that new answers can be written the same way. Describe the style, not the content:
the facts in the examples may be outdated and must not be reused."""

STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "guide": {"type": "string"},
        "detail": {"type": "string", "enum": list(DETAIL)},
        "tone": {"type": "string", "enum": list(TONE)},
        "excerpts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["guide", "detail", "tone", "excerpts"],
}


def style_hash(settings):
    files = [(f, _mtime(f)) for f in settings.get("style_files") or []]
    return _hash(files, settings.get("style_snippets") or [])


def style_state(settings):
    st = settings.get("style") or {}
    has = bool(settings.get("style_files") or settings.get("style_snippets"))
    return {"has_examples": has, "learned": bool(st.get("guide")),
            "stale": has and st.get("hash") != style_hash(settings)}


def learn_style(settings, job=None):
    from . import engine
    files = settings.get("style_files") or []
    snippets = [s for s in settings.get("style_snippets") or [] if s.strip()]
    if not files and not snippets:
        raise ValueError("Add at least one response you are proud of first.")
    parts, total = [], 0
    for f in files:
        if job:
            job.update(message=f"Reading {os.path.basename(f)}")
        try:
            t = read_file(f, 25000)
        except Exception as e:  # noqa: BLE001 - one unreadable file must not stop the rest
            if job:
                job.log(f"Skipped {os.path.basename(f)}: {e}")
            continue
        if total + len(t) > 80000:
            t = t[:max(0, 80000 - total)]
        if t:
            parts.append(f"## Example: {os.path.basename(f)}\n{t}")
            total += len(t)
    for i, s in enumerate(snippets, 1):
        parts.append(f"## Pasted example {i}\n{_clip(s, 6000)}")
    if not parts:
        raise ValueError("None of the example files could be read.")
    name = settings.get("user_name") or "the bid manager"
    prompt = "\n\n".join([
        f"These are tender responses {name} is proud of. Some are whole documents or compliance sheets; focus on "
        "the answer text written by the bidder, not on the customer's requirement text.", *parts, "",
        "Describe the writing style so another writer can match it:",
        "- guide: 8-15 short bullet lines (start each with '- '): tone and attitude (how much passion and "
        "commitment), sentence length and structure, how answers open, how benefits are connected to the "
        "customer, vocabulary and terms preferred, use of the customer's name, level of technical detail, "
        "how limitations are worded, formatting habits (lists, bold, references). Be concrete; quote short "
        "typical phrases.",
        "- detail: the closest level: concise (1-2 sentences), standard (about 40-80 words), detailed (longer, "
        "explanatory).",
        "- tone: the closest: neutral (factual), confident (active, owning the solution), engaged (benefit-led, "
        "committed).",
        "- excerpts: 3-5 short answers (or parts of answers, max 70 words each) copied word for word that best "
        "show the style. Remove customer names and prices.",
        "Do not recommend restating the requirement, marketing superlatives or unsupported claims, even if the "
        "examples do so: the responder never does those."])
    run_dir = os.path.join(config.RUNS_DIR, "_style", time.strftime("%Y%m%d-%H%M%S"))
    if job:
        job.update(message="Learning your style")
    out, cost = engine.claude_call(settings, prompt, run_dir, STYLE_SYSTEM, json_schema=STYLE_SCHEMA)
    style = {"hash": style_hash(settings), "guide": (out.get("guide") or "").strip(),
             "detail": out.get("detail") if out.get("detail") in DETAIL else "standard",
             "tone": out.get("tone") if out.get("tone") in TONE else "neutral",
             "excerpts": [e.strip() for e in out.get("excerpts") or [] if e and e.strip()][:5],
             "at": time.strftime("%Y-%m-%d %H:%M"), "examples": len(parts), "edited": False,
             "cost_usd": round(cost or 0, 3)}
    config.save_settings({"style": style})
    if job:
        job.log(f"Style learned from {len(parts)} examples (cost ${cost:.2f})")
    return {"style": style}


# ------------------------------------------------------------------ persona brief (per tender, custom)

BRIEF_SYSTEM = """You prepare a bid team to write a tender response for the people who will read and score it.
You read the customer's own material and the evaluation criteria, and write a short, concrete persona brief.
Use only what the material says; do not invent customer facts."""

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"}, "use": {"type": "string"}}, "required": ["term", "use"]}},
        "priorities": {"type": "array", "items": {"type": "string"}},
        "scoring": {"type": "array", "items": {"type": "string"}},
        "format_rules": {"type": "array", "items": {"type": "string"}},
        "audience": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["language", "priorities", "scoring", "format_rules", "audience"],
}


def inputs_hash(project, settings):
    p = persona_of(project)
    return _hash(p["audience"], p["detail"], p["tone"], p["mirror_urls"],
                 [(f, _mtime(f)) for f in p["mirror_files"]], p["scoring"], project.get("customer"))


def brief_state(project, settings):
    b = project.get("persona_brief") or {}
    return {"exists": bool(b.get("text")), "stale": bool(b.get("text")) and b.get("hash") != inputs_hash(project, settings),
            "edited": bool(b.get("edited"))}


def audience_text(aud):
    """The readers by weight, with what each cares about and how to write for them."""
    out = []
    for lvl in (3, 2, 1):
        for r in ROLES:
            if int(aud.get(r["key"], 0)) == lvl:
                out.append(f"- {LEVELS[lvl]}: {r['name']} ({r['title']}; {r['who']}). Cares about: {r['cares']}. "
                           f"How to write for them: {r['write']}")
    off = [r["name"] for r in ROLES if int(aud.get(r["key"], 0)) == 0]
    if off:
        out.append(f"- Not a reader here: {', '.join(off)}. Do not write for them.")
    return "\n".join(out)


def build_brief(project, settings, job=None):
    from . import engine
    p = persona_of(project)
    log = job.log if job else (lambda *_: None)
    parts, failed = [], []
    for u in p["mirror_urls"]:
        if not u.strip():
            continue
        if job:
            job.update(message=f"Reading {u}")
        try:
            parts.append(f"## Customer material: {u}\n{read_url(u.strip())}")
        except Exception as e:  # noqa: BLE001
            failed.append(u)
            log(f"Could not read {u}: {e}. If the site refuses automated reading, save the page or PDF and "
                "add it as a file.")
    for f in p["mirror_files"]:
        if job:
            job.update(message=f"Reading {os.path.basename(f)}")
        try:
            parts.append(f"## Customer material: {os.path.basename(f)}\n{read_file(f)}")
        except Exception as e:  # noqa: BLE001
            failed.append(os.path.basename(f))
            log(f"Could not read {os.path.basename(f)}: {e}")
    scoring = (p.get("scoring") or "").strip()
    if not parts and not scoring:
        log("No customer material and no scoring criteria: the brief only describes the audience")
    pr = project.get("profile") or {}
    prof = "; ".join(f"{k}: {v}" for k, v in (("vertical", pr.get("vertical")), ("country", pr.get("country")),
                                              ("end customer", pr.get("end_user_type")),
                                              ("consultant", pr.get("consultant")),
                                              ("why they tender", pr.get("purpose")),
                                              ("concerns", pr.get("concerns"))) if v)
    tb = (project.get("brief") or {}).get("text") or ""
    prompt = "\n".join([
        f"Tender: {project.get('name')} · Customer: {project.get('customer') or '?'} · "
        f"We offer: {', '.join(project.get('products') or []) or '?'}",
        f"Tender profile: {prof}" if prof else "",
        ("\n# Tender brief (from the requirements)\n" + _clip(tb, 4000)) if tb else "",
        "\n# Who reads and scores the response (set by the bid manager)", audience_text(p["audience"]),
        f"Detail: {DETAIL[p['detail']]['text']}  Tone: {TONE[p['tone']]['text']}",
        ("\n# Evaluation and scoring criteria (pasted from the RFP)\n" + _clip(scoring, 15000)) if scoring else "",
        "\n# The customer's own material (web pages, annual report, strategy)" if parts else "",
        _clip("\n\n".join(parts), 60000) if parts else "",
        "\nWrite the persona brief:",
        "- language: 5-15 terms and phrases the customer uses for themselves, their users, sites and goals, with "
        "how to use each in our answers (e.g. term 'passengers', use 'say passengers, not visitors'). Only terms "
        "found in their material or the criteria. Empty if there is no material.",
        "- priorities: 3-8 strategic priorities or concerns found in their material (e.g. 'net zero by 2030', "
        "'passenger experience'), each with a short hint on when an answer can connect to it. Empty if none.",
        "- scoring: 2-6 sentences (one per item) on how the response is scored and what earns points (weights, pass/fail "
        "criteria, what the evaluators look for). Empty if no criteria were given.",
        "- format_rules: explicit demands on how answers must be written (e.g. 'start each answer with Comply / "
        "Not comply', word limits, 'reference the annex'). Only demands stated in the criteria.",
        "- audience: 3-6 sentences (one per item) on how to write for this panel: whom to serve first, what each reader needs "
        "to find quickly, and how to handle conflicting needs."])
    run_dir = os.path.join(config.RUNS_DIR, project["id"], "persona-" + time.strftime("%Y%m%d-%H%M%S"))
    if job:
        job.update(message="Writing the persona brief")
    out, cost = engine.claude_call(settings, prompt, run_dir, BRIEF_SYSTEM, json_schema=BRIEF_SCHEMA)
    project["persona_brief"] = {"hash": inputs_hash(project, settings), "text": brief_text(out), "data": out,
                                "at": time.strftime("%Y-%m-%d %H:%M"), "edited": False,
                                "sources": len(parts), "failed": failed, "cost_usd": round(cost or 0, 3)}
    log(f"Persona brief written from {len(parts)} customer sources (cost ${cost:.2f})")
    return {"persona_brief": True, "cost_usd": round(cost or 0, 3)}


def _sentences(v):
    """A list of sentences, or a string (some models return a JSON list inside a string)."""
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                v = json.loads(s)
            except ValueError:
                return s
        else:
            return s
    return " ".join(str(x).strip() for x in v or [] if str(x).strip())


def brief_text(d):
    out = []
    if d.get("audience"):
        out.append("How to write for this panel: " + _sentences(d["audience"]))
    if d.get("scoring"):
        out.append("How it is scored: " + _sentences(d["scoring"]))
    if d.get("format_rules"):
        out.append("Format demands from the customer:")
        out += [f"- {x}" for x in d["format_rules"]]
    if d.get("language"):
        out.append("The customer's language (mirror it):")
        out += [f"- {x.get('term')}: {x.get('use')}" for x in d["language"]]
    if d.get("priorities"):
        out.append("The customer's priorities (connect to them where an answer genuinely relates):")
        out += [f"- {x}" for x in d["priorities"]]
    return "\n".join(out)


def ensure_brief(project, settings, job=None):
    """Before answering in custom mode: write the persona brief when missing or out of date. A brief the
    bid manager has edited is kept as it is."""
    if persona_of(project)["mode"] != "custom":
        return None
    st = brief_state(project, settings)
    if st["exists"] and (not st["stale"] or st["edited"]):
        if st["stale"] and job:
            job.log("Persona inputs changed since the brief was edited; using the edited brief")
        return project["persona_brief"]["text"]
    build_brief(project, settings, job)
    return project["persona_brief"]["text"]


# ------------------------------------------------------------------ prompt

def prompt_section(project, settings):
    """The '# Response style and audience' section for each batch prompt."""
    p = persona_of(project)
    st = settings.get("style") or {}
    detail, tone = effective(project, settings)
    L = ["\n# Response style and audience",
         "This shapes tone, emphasis, vocabulary and level of detail. It never changes the compliance code, the "
         "confidence or the evidence rules, and never justifies a claim the evidence does not support. The "
         "writing rules in your instructions (no restating, no superlatives, varied openings) still apply."]
    if st.get("guide"):
        who = settings.get("user_name") or "the bid manager"
        L.append(f"\n## Style of {who} (learned from responses they are proud of)")
        L.append(st["guide"].strip())
        if st.get("excerpts"):
            L.append("Examples of their writing (style only; the facts in them are not evidence):")
            L += [f"> {e}" for e in st["excerpts"]]
    L.append("\n## Detail and tone")
    L.append(f"{DETAIL[detail]['text']} {TONE[tone]['text']}")
    L.append("\n## Who reads and scores the answers")
    if p["mode"] == "custom":
        L.append(audience_text(p["audience"]))
        L.append("Serve the primary readers first; give the others what they need to find quickly. When their "
                 "needs conflict, the primary reader wins.")
        b = (project.get("persona_brief") or {}).get("text")
        if b:
            L.append("\n## Persona brief for this tender")
            L.append(b)
            L.append("Format demands from the customer's scoring instructions take precedence over the style above "
                     "(never over the evidence rules).")
    else:
        L.append("A mixed evaluation panel: procurement checks that each answer is complete and comparable, the "
                 "requirement owner scores the substance, IT security may veto. Write so each of them can score "
                 "the answer quickly.")
    return "\n".join(L)


def summary(project, settings):
    """Short description for the UI and the run log."""
    p = persona_of(project)
    detail, tone = effective(project, settings)
    if p["mode"] != "custom":
        return f"Standard ({DETAIL[detail]['label'].lower()}, {TONE[tone]['label'].lower()})"
    hi = max([int(v) for k, v in p["audience"].items() if k in ROLE] or [0])
    top = [r["name"] for r in ROLES if hi and int(p["audience"].get(r["key"], 0)) == hi]
    return f"Custom: {', '.join(top) + ' first' if top else 'no readers set'}; {DETAIL[detail]['label'].lower()}, " \
           f"{TONE[tone]['label'].lower()}"


def ui_meta():
    return {"roles": ROLES, "levels": LEVELS,
            "presets": {k: {"label": v["label"], "hint": v["hint"], "audience": v["audience"]} for k, v in PRESETS.items()},
            "detail": {k: v["label"] for k, v in DETAIL.items()}, "tone": {k: v["label"] for k, v in TONE.items()}}
