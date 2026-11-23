"""Whole-document awareness for answering, so the responses read as one coherent document.

  brief      Before answering, Claude reads all requirements once and writes a short tender brief:
             what the tender is about, how it is built, recurring themes, and which facts to state once
             (and where). Cached in the project until the requirements change.
  outline    The document's sections with their requirement ids, so an answer can refer to others.
  digest     The answers already given in this response that relate to the batch: Claude refers to
             them instead of repeating them.
  openings   How earlier answers start, so new ones do not start the same way.
  style_check  After answering: flags answers that restate the requirement, or open like several others.
"""
import hashlib
import os
import re
import time
from collections import Counter

from . import config
from .kb import STOP

_WORD = re.compile(r"[a-zA-Z0-9äöüåæøéèß]+")


def _tokens(text):
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in STOP}


def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)[0]


def opening(text, n=4):
    return " ".join(_WORD.findall((text or "").lower())[:n])


# ------------------------------------------------------------------ outline

def outline(project, limit=80):
    """[(section, first id, last id, count)] in document order."""
    out, cur = [], None
    for r in project.get("rows", []):
        sec = (r.get("section") or "").strip()
        if cur and cur[0] == sec:
            cur[2], cur[3] = r.get("id"), cur[3] + 1
        else:
            cur = [sec, r.get("id"), r.get("id"), 1]
            out.append(cur)
    if len(out) > limit:  # very long documents: keep the first sections and say how many more
        out = out[:limit] + [["…", "", "", sum(x[3] for x in out[limit:])]]
    return [tuple(x) for x in out]


def outline_text(project):
    lines = []
    for sec, a, b, n in outline(project):
        rng = a if a == b else f"{a} – {b}"
        lines.append(f"- {sec or '(no section)'}: {rng} ({n})")
    return "\n".join(lines)


# ------------------------------------------------------------------ tender brief

BRIEF_SYSTEM = """You prepare a bid team to answer a tender specification consistently. You read all the
requirements and write a short working brief. Use only what the requirements say. Be concrete."""

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "structure": {"type": "string"},
        "themes": {"type": "array", "items": {"type": "string"}},
        "state_once": {"type": "array", "items": {"type": "object", "properties": {
            "fact": {"type": "string"}, "where": {"type": "string"}}, "required": ["fact", "where"]}},
        "cross_references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "structure", "themes", "state_once"],
}


def _rows_hash(project):
    h = hashlib.sha1()
    for r in project.get("rows", []):
        h.update(f"{r.get('id')}|{r.get('text')}\n".encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def ensure_brief(project, settings, job=None):
    """Return the tender brief text, creating it with Claude when missing or out of date."""
    from . import engine
    h = _rows_hash(project)
    b = project.get("brief") or {}
    if b.get("hash") == h and b.get("text"):
        return b["text"]
    rows = project.get("rows", [])
    lines, n = [], 0
    for r in rows:
        ln = f"[{r.get('id')}] {r.get('section') or ''} :: {(r.get('text') or '')[:600]}"
        n += len(ln)
        if n > 60000:
            lines.append(f"... ({len(rows) - len(lines)} more requirements not shown)")
            break
        lines.append(ln)
    prods = ", ".join(project.get("products") or []) or "(not chosen)"
    prompt = "\n".join([
        f"Tender: {project.get('name')} · Customer: {project.get('customer') or '?'} · We offer: {prods} · "
        f"Our role: {project.get('role') or 'Vendor'}", f"{len(rows)} requirements:", "", *lines, "",
        "Write the brief:",
        "- summary: 3-5 sentences: what the customer is buying, the scale and setting, what matters most to them.",
        "- structure: 2-4 sentences on how the document is organised (which sections cover what).",
        "- themes: 4-10 recurring themes that run through many requirements.",
        "- state_once: facts our answers would otherwise repeat in every answer (the offered product and edition, "
        "the overall architecture, licensing model, hosting, standard support), each with the requirement id or "
        "section where it is best stated once, so other answers can refer to it.",
        "- cross_references: groups of requirement ids that ask about the same thing (e.g. '4.2.1, 7.1.3: "
        "failover'), so they are answered consistently and without repetition."])
    run_dir = os.path.join(config.RUNS_DIR, project["id"], "brief-" + time.strftime("%Y%m%d-%H%M%S"))
    if job:
        job.update(message="Reading the whole document (tender brief)")
    data, cost = engine.claude_call(settings, prompt, run_dir, BRIEF_SYSTEM, json_schema=BRIEF_SCHEMA)
    text = brief_text(data)
    project["brief"] = {"hash": h, "text": text, "data": data, "at": time.strftime("%Y-%m-%d %H:%M"),
                        "cost_usd": round(cost or 0, 3)}
    if job:
        job.log(f"Tender brief written (cost ${cost:.3f})")
    return text


def brief_text(d):
    out = [d.get("summary", "").strip()]
    if d.get("structure"):
        out.append("Structure: " + d["structure"].strip())
    if d.get("themes"):
        out.append("Recurring themes: " + "; ".join(d["themes"]))
    if d.get("state_once"):
        out.append("State these once, where given, and refer to them elsewhere:")
        out += [f"- {x.get('fact')} → {x.get('where')}" for x in d["state_once"]]
    if d.get("cross_references"):
        out.append("Requirements that ask about the same thing (answer consistently, refer between them):")
        out += [f"- {x}" for x in d["cross_references"]]
    return "\n".join(x for x in out if x)


# ------------------------------------------------------------------ what has been answered already

def digest(project, batch, limit=18, chars=260):
    """Related answers already given in this response (other rows), most related first."""
    keys = {r["key"] for r in batch}
    want = set().union(*(_tokens(r.get("text")) for r in batch)) if batch else set()
    sections = {r.get("section") for r in batch}
    scored = []
    for r in project.get("rows", []):
        if r["key"] in keys or not r.get("explanation"):
            continue
        overlap = len(want & _tokens(r.get("text") + " " + r.get("explanation", "")))
        score = overlap + (3 if r.get("section") in sections else 0)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, r in scored[:limit]:
        e = re.sub(r"\s+", " ", r["explanation"]).strip()
        out.append(f"- [{r.get('id')}] ({r.get('compliance')}) {e[:chars]}{'…' if len(e) > chars else ''}")
    return "\n".join(out)


def used_openings(project, batch, limit=20):
    keys = {r["key"] for r in batch}
    c = Counter(opening(r["explanation"]) for r in project.get("rows", [])
                if r["key"] not in keys and r.get("explanation"))
    return [f'"{o} …" ({n}x)' if n > 1 else f'"{o} …"' for o, n in c.most_common(limit) if o]


# ------------------------------------------------------------------ after answering

def style_check(row, project):
    """Flag answers that restate the requirement or start like several others. Returns the flags."""
    flags = []
    expl = row.get("explanation") or ""
    if not expl:
        row["style_flags"] = []
        return []
    req = _tokens(row.get("text"))
    first = _tokens(_first_sentence(expl))
    if req and first and len(first & req) / max(1, len(first)) >= 0.7 and len(first) <= len(req) + 4:
        flags.append("restates")
    op = opening(expl)
    same = sum(1 for r in project.get("rows", []) if r["key"] != row["key"] and r.get("explanation")
               and opening(r["explanation"]) == op)
    if op and same >= 2:
        flags.append("same_opening")
    for p in project.get("products") or []:
        head = " ".join(_WORD.findall(p.lower())[:2])
        if head and expl.lower().lstrip().startswith(head):
            flags.append("product_opening")
            break
    row["style_flags"] = flags
    return flags


STYLE_NOTES = {"restates": "The answer mostly repeats the requirement.",
               "same_opening": "The answer starts like several other answers.",
               "product_opening": "The answer opens with the product name."}


def style_summary(project):
    c = Counter(f for r in project.get("rows", []) for f in (r.get("style_flags") or []))
    return dict(c)

