"""Turn files into plain-text sections: [(heading_or_location, text), ...].

Each section keeps a pointer (heading, page or sheet) so an answer can cite where
the evidence sits in the source.
"""
import os
import re

TEXT_EXT = {".txt", ".md", ".csv", ".json", ".xml", ".log"}
HTML_EXT = {".html", ".htm"}
SUPPORTED = TEXT_EXT | HTML_EXT | {".docx", ".xlsx", ".xlsm", ".pdf", ".pptx", ".msg", ".eml"}
MAX_BYTES = 60 * 1024 * 1024


def supported(path):
    name = os.path.basename(path)
    return (os.path.splitext(name)[1].lower() in SUPPORTED
            and not name.startswith("~$") and not name.startswith("."))


def clean(t):
    t = (t or "").replace("​", "").replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return t.strip()


def extract(path):
    ext = os.path.splitext(path)[1].lower()
    if os.path.getsize(path) > MAX_BYTES:
        return []
    if ext in TEXT_EXT:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [("", clean(f.read()))]
    if ext in HTML_EXT:
        with open(path, encoding="utf-8", errors="replace") as f:
            return html_sections(f.read())[1]
    if ext == ".docx":
        return _docx(path)
    if ext in (".xlsx", ".xlsm"):
        return _xlsx(path)
    if ext == ".pdf":
        return _pdf(path)
    return _markitdown(path)


def _docx(path):
    import docx
    d = docx.Document(path)
    out, head, buf = [], "", []

    def flush():
        if buf:
            out.append((head, clean("\n".join(buf))))
            buf.clear()

    body = d.element.body
    for el in body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = docx.text.paragraph.Paragraph(el, d)
            txt = p.text.strip()
            if not txt:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if is_heading(p, style):
                flush()
                head = txt[:200]
            else:
                buf.append(txt)
        elif tag == "tbl":
            t = docx.table.Table(el, d)
            for row in t.rows:
                cells = []
                for c in row.cells:
                    ct = c.text.strip()
                    if ct and (not cells or cells[-1] != ct):
                        cells.append(ct)
                if cells:
                    buf.append(" | ".join(cells))
    flush()
    return out


HEAD_RE = re.compile(r"^(h\d|title|.*heading.*|.*überschrift.*|.*titre.*)$", re.I)
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def is_heading(p, style):
    if HEAD_RE.match(style.strip()) and not style.lower().startswith("toc"):
        return True
    ol = p._p.find(f".//{W_NS}outlineLvl")
    if ol is None and p.style is not None:
        ol = p.style.element.find(f".//{W_NS}outlineLvl")
    return ol is not None and (ol.get(f"{W_NS}val") or "9").isdigit() and int(ol.get(f"{W_NS}val")) < 9


def _xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(values_only=True):
            vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
            if vals:
                lines.append(" | ".join(vals))
        if lines:
            out.append((f"Sheet: {ws.title}", clean("\n".join(lines))))
    wb.close()
    return out


def _pdf(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    out = []
    for i, page in enumerate(r.pages, 1):
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - damaged pages should not stop the indexing
            t = ""
        if t.strip():
            out.append((f"Page {i}", clean(t)))
    return out


def _markitdown(path):
    try:
        from markitdown import MarkItDown
        res = MarkItDown().convert(path)
        return [("", clean(res.text_content))]
    except Exception:  # noqa: BLE001 - unsupported/odd files are skipped
        return []


def html_sections(html):
    """Main text of a web page split by headings. Returns (title, sections)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    title = clean(soup.title.get_text()) if soup.title else ""
    for t in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe"]):
        t.decompose()
    for sel in ["[role=navigation]", ".breadcrumb", ".breadcrumbs", ".toc", "#toc", ".cookie", "#cookie"]:
        for t in soup.select(sel):
            t.decompose()
    root = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"}) or soup.body or soup
    out, head, buf = [], title, []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th", "pre", "dt", "dd"]):
        if el.find(["p", "li", "td"]) and el.name in ("li", "td", "dd"):
            continue  # nested containers: take the leaves only
        txt = clean(el.get_text(" "))
        if not txt:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            if buf:
                out.append((head, clean("\n".join(buf))))
                buf = []
            head = txt[:200]
        else:
            buf.append(("- " if el.name == "li" else "") + txt)
    if buf:
        out.append((head, clean("\n".join(buf))))
    return title, out


def chunk(sections, size=1400, overlap=200):
    """Split sections into overlapping chunks that keep their heading."""
    for head, text in sections:
        text = text or ""
        if len(text) <= size:
            if text.strip():
                yield head, text
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            if end < len(text):
                cut = text.rfind("\n", start + size // 2, end)
                if cut == -1:
                    cut = text.rfind(". ", start + size // 2, end)
                if cut != -1:
                    end = cut + 1
            yield head, text[start:end].strip()
            if end >= len(text):
                break
            nxt = max(end - overlap, start + 1)
            sp = text.find(" ", nxt, end)
            start = sp + 1 if sp != -1 else nxt
