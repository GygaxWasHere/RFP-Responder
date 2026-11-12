"""Write the responses out: back into the customer's Excel, and/or into a Word template."""
import copy
import os
import re
import time

from openpyxl.utils import column_index_from_string

from . import config


def _label(scale, code):
    return next((s["label"] for s in scale if s["code"] == code), code)


def _color(scale, code):
    return next((s.get("color", "") for s in scale if s["code"] == code), "").lstrip("#")


def links_text(row):
    return "\n".join(ln["url"] for ln in row.get("links") or [])


def evidence_text(row):
    out = []
    for e in row.get("evidence") or []:
        src = e.get("source", "")
        name = src if src.startswith("http") else os.path.basename(src)
        where = f" ({e['where']})" if e.get("where") else ""
        out.append(f"{name}{where}: \"{e.get('quote', '')}\"")
    return "\n".join(out)


def default_out(path, suffix):
    base, ext = os.path.splitext(path)
    base = re.sub(r"_Response_v\d+$", "", base)
    n = 1
    while os.path.exists(f"{base}_Response_v{n}{ext}"):
        n += 1
    return f"{base}_Response_v{n}{ext}" if not suffix else f"{base}_{suffix}{ext}"


# ------------------------------------------------------------------ Excel

def write_excel(project, settings, author=""):
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, PatternFill

    o = project["output"]["excel"]
    src = o.get("path") or project["req_source"]["path"]
    if not src or not src.lower().endswith((".xlsx", ".xlsm")):
        raise ValueError("Choose the Excel workbook to write into")
    out = o.get("out_path") or default_out(src, "")
    keep_vba = src.lower().endswith(".xlsm")
    wb = openpyxl.load_workbook(src, keep_vba=keep_vba)
    ws = wb[o.get("sheet") or project["req_source"].get("sheet") or wb.sheetnames[0]]
    scale = settings.get("scale") or config.DEFAULT_SCALE
    wrap = Alignment(wrap_text=True, vertical="top")
    written = skipped = 0

    def put(col, rown, value, fill=None):
        if not col or value in (None, ""):
            return
        c = ws.cell(row=rown, column=column_index_from_string(col))
        if c.coordinate in ws.merged_cells:
            return
        if c.value not in (None, "") and not o.get("overwrite"):
            return
        c.value = value
        c.alignment = wrap
        if fill:
            c.fill = PatternFill("solid", fgColor=fill)

    for row in project["rows"]:
        if not row.get("row") or not row.get("compliance"):
            continue
        if row.get("status") not in ("approved", "draft", "needs_review"):
            continue
        r = row["row"]
        code = row["compliance"]
        if o.get("mode") == "marks":
            col = (o.get("marks") or {}).get(code)
            put(col, r, o.get("mark_char") or "X")
        else:
            put(o.get("compliance_col"), r, _label(scale, code) if o.get("use_label", True) else code,
                _color(scale, code) or None)
        expl = row.get("explanation", "")
        if not o.get("product_col") and row.get("product"):
            expl = f"Product: {row['product']}. {expl}"
        put(o.get("explanation_col"), r, expl)
        put(o.get("product_col"), r, row.get("product"))
        put(o.get("links_col"), r, links_text(row))
        put(o.get("evidence_col"), r, evidence_text(row))
        put(o.get("confidence_col"), r, f"{row['confidence']}%" if row.get("confidence") is not None else "")
        put(o.get("owner_col"), r, row.get("responsible"))
        put(o.get("initials_col"), r, settings.get("user_initials") or author)
        note_col = o.get("explanation_col") or o.get("compliance_col") or next(iter((o.get("marks") or {}).values()), "")
        notes = [n for n in (row.get("review_notes"), row.get("gaps") and "Gaps: " + row["gaps"],
                             row.get("question") and "Question to customer: " + row["question"]) if n]
        if note_col and notes and row.get("status") != "approved":
            ws.cell(row=r, column=column_index_from_string(note_col)).comment = Comment(
                "RFP Responder review note:\n" + "\n".join(notes), "RFP Responder")
        written += 1
    wb.save(out)
    return {"path": out, "written": written, "skipped": skipped}


# ------------------------------------------------------------------ Word

PH = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _replace_in_paragraph(p, values):
    full = "".join(r.text for r in p.runs)
    if "{{" not in full:
        return
    new = PH.sub(lambda m: str(values.get(m.group(1).lower(), m.group(0))), full)
    if new == full or not p.runs:
        return
    p.runs[0].text = new
    for r in p.runs[1:]:
        r.text = ""


def _replace_everywhere(container, values):
    for p in container.paragraphs:
        _replace_in_paragraph(p, values)
    for t in container.tables:
        for row in t.rows:
            for c in row.cells:
                _replace_everywhere(c, values)


def _shade(cell, hex_color):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    if not hex_color:
        return
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _row_values(row, scale):
    return {
        "id": row.get("id", ""), "section": row.get("section", ""), "requirement": row.get("text", ""),
        "compliance": row.get("compliance", ""), "compliance_label": _label(scale, row.get("compliance", "")),
        "explanation": row.get("explanation", ""), "product": row.get("product", ""),
        "responsible": row.get("responsible", ""), "evidence": evidence_text(row), "links": links_text(row),
        "confidence": f"{row['confidence']}%" if row.get("confidence") is not None else "",
        "gaps": row.get("gaps", ""), "question": row.get("question", ""),
    }


def write_word(project, settings, author=""):
    """Fill a Word template.

    Document placeholders: {{project}} {{customer}} {{date}} {{author}} {{products}} {{summary}}.
    Requirement table: put one table row with any of {{id}} {{section}} {{requirement}} {{compliance}}
    {{compliance_label}} {{explanation}} {{product}} {{responsible}} {{evidence}} {{links}}
    {{confidence}} {{gaps}} {{question}}. That row is repeated for every answered requirement.
    Without such a row, a compliance table is added at the end of the document.
    """
    import docx
    from docx.shared import Pt

    o = project["output"]["word"]
    tpl = o.get("template")
    if not tpl or not os.path.exists(tpl):
        raise ValueError("Choose a Word template (.docx)")
    d = docx.Document(tpl)
    scale = settings.get("scale") or config.DEFAULT_SCALE
    rows = [r for r in project["rows"] if r.get("compliance")]
    counts = {s["code"]: sum(1 for r in rows if r["compliance"] == s["code"]) for s in scale}
    summary = ", ".join(f"{counts[s['code']]} {s['label'].lower()}" for s in scale if counts[s["code"]])
    doc_vals = {"project": project.get("name", ""), "customer": project.get("customer", ""),
                "date": time.strftime("%d %B %Y"), "author": settings.get("user_name") or author,
                "products": ", ".join(project.get("products") or []),
                "summary": f"{len(rows)} requirements answered: {summary}."}

    tpl_row = None
    for t in d.tables:
        for tr in t.rows:
            txt = "".join(c.text for c in tr.cells)
            if re.search(r"\{\{\s*(requirement|explanation|compliance)\s*\}\}", txt):
                tpl_row = (t, tr)
                break
        if tpl_row:
            break

    if tpl_row:
        t, tr = tpl_row
        anchor = tr._tr
        for row in rows:
            vals = _row_values(row, scale)
            new = copy.deepcopy(tr._tr)
            anchor.addnext(new)
            anchor = new
            from docx.table import _Row
            nr = _Row(new, t)
            for c in nr.cells:
                had = "{{compliance" in c.text
                _replace_everywhere(c, vals)
                if had:
                    _shade(c, _color(scale, row["compliance"]))
        t._tbl.remove(tr._tr)
    else:
        d.add_heading("Compliance response", level=1)
        cols = ["ID", "Requirement", "Compliance", "Response", "Product"]
        t = d.add_table(rows=1, cols=len(cols))
        try:
            t.style = d.tables[0].style if d.tables else "Table Grid"
        except Exception:  # noqa: BLE001
            pass
        for i, c in enumerate(cols):
            t.rows[0].cells[i].text = c
        for row in rows:
            v = _row_values(row, scale)
            cells = t.add_row().cells
            resp = v["explanation"] + (("\n" + v["links"]) if v["links"] else "")
            for i, val in enumerate([v["id"], v["requirement"], v["compliance_label"], resp, v["product"]]):
                cells[i].text = val
                for p in cells[i].paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)
            _shade(cells[2], _color(scale, row["compliance"]))

    _replace_everywhere(d, doc_vals)
    for sec in d.sections:
        for part in (sec.header, sec.footer):
            _replace_everywhere(part, doc_vals)
    out = o.get("out_path") or os.path.join(os.path.dirname(tpl),
                                            re.sub(r"[^\w\- ]", "", project.get("name") or "Response")
                                            + "_Compliance_Response.docx")
    if os.path.abspath(out) == os.path.abspath(tpl):
        raise ValueError("The output file must differ from the template")
    d.save(out)
    return {"path": out, "written": len(rows)}
