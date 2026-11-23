"""Readable text copies of Word, Excel, PowerPoint and Outlook files, so Claude Code can read them.

Claude Code's Read tool reads text and PDF, but not .docx/.xlsx/.pptx/.msg. Before a run, every such
file in the allowed folders gets a .txt copy under %LOCALAPPDATA%\\RFPResponder\\readable, with the
same relative path plus ".txt". Claude reads the copy and cites the original; quotes are checked
against the same text. Copies are only refreshed when the original changed.
"""
import hashlib
import os

from . import config, extract

OFFICE = {".docx", ".xlsx", ".xlsm", ".pptx", ".msg", ".eml"}
ROOT = os.path.join(config.BASE_DIR, "readable")
MAX_MB = 60


def mirror_dir(folder):
    f = os.path.normcase(os.path.abspath(folder))
    name = "".join(c if c.isalnum() or c in " -_" else "_" for c in os.path.basename(f.rstrip("\\/")))[:40]
    return os.path.join(ROOT, f"{name}-{hashlib.sha1(f.encode('utf-8')).hexdigest()[:8]}")


def prepare(folders, kb=None, job=None):
    """Create/refresh the copies. Returns {mirror_dir: original_folder} for folders that have Office files."""
    out = {}
    made = failed = 0
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        md = mirror_dir(folder)
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith((".", "~", "__"))]
            for n in names:
                if n.startswith("~$") or os.path.splitext(n)[1].lower() not in OFFICE:
                    continue
                src = os.path.join(root, n)
                dst = os.path.join(md, os.path.relpath(src, folder) + ".txt")
                out[md] = folder
                try:
                    st = os.stat(src)
                    if st.st_size > MAX_MB * 1048576:
                        continue
                    if os.path.exists(dst) and os.path.getmtime(dst) >= st.st_mtime:
                        continue
                    text = kb.doc_text(src) if kb else None
                    if not text:
                        text = "\n\n".join((f"## {h}\n" if h else "") + t for h, t in extract.extract(src))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "w", encoding="utf-8") as f:
                        f.write(f"Readable copy of: {src}\n\n{text}")
                    made += 1
                    if job and made % 25 == 0:
                        job.update(message=f"Readable copies: {made} made")
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
                    failed += 1
                    if job:
                        job.log(f"No readable copy of {n}: {e}")
    if job and (made or failed):
        job.log(f"Readable copies of Office files: {made} new or updated, {failed} unreadable")
    return out


def to_original(path, mirrors):
    """Map a path inside a readable copy folder back to the original file."""
    p = os.path.normcase(os.path.abspath(path))
    for md, folder in (mirrors or {}).items():
        mdn = os.path.normcase(os.path.abspath(md))
        if p.startswith(mdn + os.sep):
            rel = os.path.abspath(path)[len(mdn) + 1:]
            if rel.lower().endswith(".txt"):
                rel = rel[:-4]
            return os.path.join(folder, rel)
    return path
