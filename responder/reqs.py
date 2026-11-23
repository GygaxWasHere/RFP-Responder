"""Read requirements from an Excel workbook or a Word document.

Every requirement becomes a row dict:
  key, id, section, text, row (Excel row or None), selected,
  compliance, explanation, product, responsible, evidence[], links[], confidence,
  gaps, question, status ('', 'draft', 'needs_review', 'approved'), verified
"""
import os
import re

from openpyxl.utils import column_index_from_string, get_column_letter

MODAL = re.compile(r"\b(shall|must|should|will be|required|requires|need to|has to|have to|is to|are to|"
                   r"muss|müssen|soll|sollen|ist zu|sind zu|erforderlich|gefordert|devra|doit|deve|dient)\b", re.I)


def blank_row(key, rid, section, text, row=None):
    return {"key": key, "id": rid, "section": section, "text": text, "row": row, "selected": True,
            "compliance": "", "explanation": "", "product": "", "responsible": "", "evidence": [],
            "links": [], "confidence": None, "gaps": "", "question": "", "status": "", "verified": None}


def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return "excel"
    if ext == ".docx":
        return "word"
    return ""


# ------------------------------------------------------------------ Excel

def excel_preview(path, rows=14, cols=16):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        grid = []
        for r in ws.iter_rows(min_row=1, max_row=rows, max_col=cols, values_only=True):
            grid.append(["" if v is None else str(v)[:120] for v in r])
        out.append({"name": ws.title, "max_row": ws.max_row, "max_col": ws.max_column,
                    "cols": [get_column_letter(i) for i in range(1, cols + 1)], "grid": grid,
                    "guess": _guess(grid)})
    wb.close()
    return out


def _guess(grid):
    """Guess header row and the id / text columns from the header words and the cell lengths."""
    hdr_words = ("requirement", "description", "anforderung", "question", "text", "spec", "exigence", "requisito")
    id_words = ("id", "no", "nr", "ref", "#", "item", "number", "nummer", "clause", "pos")
    best = {"header_row": 1, "first_row": 2, "id_col": "", "text_col": ""}
    for ri, row in enumerate(grid):
        low = [c.strip().lower() for c in row]
        t = next((i for i, c in enumerate(low) if c and any(w in c for w in hdr_words)), None)
        if t is not None:
            best["header_row"] = ri + 1
            best["first_row"] = ri + 2
            best["text_col"] = get_column_letter(t + 1)
            i = next((i for i, c in enumerate(low) if c and (c in id_words or any(c.startswith(w + " ") or
                      c.startswith(w + ".") for w in id_words))), None)
            if i is not None:
                best["id_col"] = get_column_letter(i + 1)
            return best
    # fall back: the column with the longest texts
    lens = {}
    for row in grid:
        for i, c in enumerate(row):
            lens[i] = lens.get(i, 0) + len(c)
    if lens:
        best["text_col"] = get_column_letter(max(lens, key=lens.get) + 1)
    return best


def load_excel(path, sheet, header_row, first_row, id_col, text_col, section_col="", context_col="", last_row=None):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    ti = column_index_from_string(text_col)
    ii = column_index_from_string(id_col) if id_col else None
    si = column_index_from_string(section_col) if section_col else None
    ci = column_index_from_string(context_col) if context_col else None
    out, section = [], ""
    last = int(last_row) if str(last_row or "").strip().isdigit() and int(last_row) > 0 else None
    if last is not None and last < int(first_row):
        raise ValueError(f"The last row ({last}) is before the first row ({first_row})")
    for rn, row in enumerate(ws.iter_rows(min_row=int(first_row), max_row=last, values_only=True), int(first_row)):
        def cell(i):
            if i is None or i > len(row):
                return ""
            v = row[i - 1]
            return "" if v is None else str(v).strip()
        text, rid = cell(ti), cell(ii)
        if si:
            section = cell(si) or section
        if not text:
            if rid and not si:
                section = rid  # an id without text is usually a heading row
            continue
        row_ = blank_row(f"r{rn}", rid or str(rn), section, text, rn)
        row_["context"] = cell(ci)
        out.append(row_)
    wb.close()
    return out


# ------------------------------------------------------------------ Word

NUM_RE = re.compile(r"^\s*((?:\d+\.)*\d+)\.?\s+(.*)$")


def load_word(path, mode="lists", start_heading=None, stop_heading=None):
    """mode: 'lists' = bullets/numbered items and sentences with shall/must/...;
             'paragraphs' = every body paragraph; 'tables' = also every table row.
    start_heading / stop_heading: positions in word_outline(); only requirements from the start
    heading up to (not including) the stop heading are returned."""
    rows, _ = _parse_word(path, mode)
    start = int(start_heading) if str(start_heading).lstrip("-").isdigit() and int(start_heading) >= 0 else None
    stop = int(stop_heading) if str(stop_heading).lstrip("-").isdigit() and int(stop_heading) >= 0 else None
    if start is not None and stop is not None and stop <= start:
        raise ValueError("'Stop before' must be a heading after 'Start at'")
    return [r for r in rows if (start is None or r["h"] >= start) and (stop is None or r["h"] < stop)]


def word_outline(path, mode="lists"):
    """Headings in document order, with the number of requirements found under each."""
    rows, heads = _parse_word(path, mode)
    for h in heads:
        h["count"] = sum(1 for r in rows if r["h"] == h["idx"])
    return {"headings": heads, "before_first": sum(1 for r in rows if r["h"] < 0), "total": len(rows)}


def _parse_word(path, mode="lists"):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(path)
    counters = [0] * 9
    section_id, section_title = "", ""
    per_section = {}
    out = []
    heads = []

    def add(text):
        nonlocal section_id
        sid = section_id or "0"
        per_section[sid] = per_section.get(sid, 0) + 1
        rid = f"{sid}.{per_section[sid]}" if section_id else str(per_section[sid])
        r = blank_row(f"w{len(out) + 1}", rid, f"{section_id} {section_title}".strip(), text)
        r["h"] = len(heads) - 1  # index of the heading this requirement sits under (-1 = before any)
        out.append(r)

    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text = p.text.replace("​", "").strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.lower().startswith(("toc", "table of")) or text.lower() in ("table of contents", "inhaltsverzeichnis", "contents"):
                continue
            lvl = _heading_level(p, style)
            if lvl:
                m = NUM_RE.match(text)
                if m:
                    section_id, section_title = m.group(1), m.group(2)
                else:
                    counters[lvl - 1] += 1
                    for i in range(lvl, 9):
                        counters[i] = 0
                    section_id = ".".join(str(c) for c in counters[:lvl] if c) or str(counters[0])
                    section_title = text
                heads.append({"idx": len(heads), "level": lvl, "id": section_id, "title": section_title[:120]})
                continue
            is_list = el.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr") is not None \
                or "list" in style.lower()
            if mode == "paragraphs" or is_list or MODAL.search(text):
                add(text)
        elif tag == "tbl" and mode == "tables":
            for row in Table(el, d).rows:
                cells = []
                for c in row.cells:
                    ct = c.text.strip()
                    if ct and (not cells or cells[-1] != ct):
                        cells.append(ct)
                if cells and len(" ".join(cells)) > 15:
                    add(" | ".join(cells))
    return out, heads


def _heading_level(p, style):
    s = style.lower()
    m = re.search(r"heading\s*(\d)", s) or re.search(r"überschrift\s*(\d)", s) or re.fullmatch(r"h(\d)", s.strip())
    if m:
        return int(m.group(1))
    if s in ("title",):
        return 0
    ol = p._p.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}outlineLvl")
    if ol is not None:
        v = ol.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")
        if v is not None and v.isdigit() and int(v) < 9:
            return int(v) + 1
    return 0


def load(spec):
    path = spec["path"]
    k = kind_of(path)
    if k == "excel":
        return load_excel(path, spec.get("sheet"), spec.get("header_row", 1), spec.get("first_row", 2),
                          spec.get("id_col", ""), spec["text_col"], spec.get("section_col", ""),
                          spec.get("context_col", ""), spec.get("last_row"))
    if k == "word":
        return load_word(path, spec.get("docx_mode", "lists"), spec.get("start_heading"), spec.get("stop_heading"))
    raise ValueError("Requirements must come from an .xlsx, .xlsm or .docx file")
