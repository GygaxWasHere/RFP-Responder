"""Fill the knowledge base from folders and from websites."""
import io
import os
import time
import urllib.parse
import urllib.robotparser
from collections import deque

from . import extract

USER_AGENT = "MilestoneRFPResponder/0.1 (bid desk documentation indexer)"


def ingest_folder(kb, folder, collection=None, user="", job=None):
    """Index every supported file under folder. Unchanged files (same mtime and size) are skipped."""
    collection = collection or folder
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith((".", "~", "__"))]
        for n in names:
            p = os.path.join(root, n)
            if extract.supported(p):
                files.append(p)
    added = skipped = failed = 0
    for i, p in enumerate(files, 1):
        if job and job.cancelled:
            break
        if job:
            job.update(progress=i / max(1, len(files)), message=f"{i}/{len(files)}  {os.path.basename(p)}")
        try:
            st = os.stat(p)
            old = kb.doc_state(p)
            if old and old["mtime"] == st.st_mtime:
                skipped += 1
                continue
            sections = extract.extract(p)
            chunks = list(extract.chunk(sections))
            if not chunks:
                failed += 1
                continue
            kb.put_document("file", p, os.path.basename(p), collection, chunks,
                            mtime=st.st_mtime, size=st.st_size, user=user)
            added += 1
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
            failed += 1
            if job:
                job.log(f"Skipped {os.path.basename(p)}: {e}")
    removed = 0 if (job and job.cancelled) else kb.delete_missing(collection, files)
    msg = f"{collection}: {added} indexed, {skipped} unchanged, {failed} unreadable, {removed} removed"
    if job:
        job.log(msg)
    return {"added": added, "skipped": skipped, "failed": failed, "removed": removed, "files": len(files)}


def _norm(url):
    u = urllib.parse.urlsplit(url)
    path = u.path or "/"
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), path, u.query, ""))


def crawl_site(kb, start_url, max_pages=200, prefix=None, delay=0.5, user="", job=None, collection=None):
    """Breadth-first crawl of pages under the start URL's folder. Respects robots.txt.

    Pages are stored with their URL as location, so answers can link straight to them.
    Re-crawling updates pages whose content changed.
    """
    import requests
    from bs4 import BeautifulSoup

    start_url = _norm(start_url)
    sp = urllib.parse.urlsplit(start_url)
    if prefix is None:
        base = sp.path if sp.path.endswith("/") else sp.path.rsplit("/", 1)[0] + "/"
        prefix = f"{sp.scheme}://{sp.netloc}{base}"
    collection = collection or prefix
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{sp.scheme}://{sp.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:  # noqa: BLE001 - no robots.txt means allowed
        rp = None

    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    queue, seen = deque([start_url]), {start_url}
    stored = unchanged = failed = 0
    n = 0
    while queue and n < max_pages:
        if job and job.cancelled:
            break
        url = queue.popleft()
        if rp and not rp.can_fetch(USER_AGENT, url):
            continue
        n += 1
        if job:
            job.update(progress=n / max_pages, message=f"{n}/{max_pages}  {url}")
        try:
            r = s.get(url, timeout=30)
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200:
                failed += 1
                continue
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                from pypdf import PdfReader
                rd = PdfReader(io.BytesIO(r.content))
                sections = [(f"Page {i}", extract.clean(pg.extract_text() or ""))
                            for i, pg in enumerate(rd.pages, 1)]
                title = os.path.basename(urllib.parse.urlsplit(url).path)
            elif "html" in ctype:
                r.encoding = r.encoding or "utf-8"
                html = r.text
                title, sections = extract.html_sections(html)
                soup = BeautifulSoup(html, "lxml")
                for a in soup.find_all("a", href=True):
                    nxt = _norm(urllib.parse.urljoin(url, a["href"]))
                    if nxt.startswith(prefix) and nxt not in seen and not _skip(nxt):
                        seen.add(nxt)
                        queue.append(nxt)
            else:
                continue
            chunks = list(extract.chunk(sections))
            if not chunks:
                continue
            import hashlib
            h = hashlib.sha1("\n".join(t for _, t in chunks).encode("utf-8", "replace")).hexdigest()
            old = kb.doc_state(url)
            if old and old["hash"] == h:
                unchanged += 1
            else:
                kb.put_document("web", url, title or url, collection, chunks, user=user, content_hash=h)
                stored += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if job:
                job.log(f"Failed {url}: {e}")
        time.sleep(delay)
    msg = f"{collection}: {stored} pages stored, {unchanged} unchanged, {failed} failed ({n} visited)"
    if job:
        job.log(msg)
    return {"stored": stored, "unchanged": unchanged, "failed": failed, "visited": n}


def _skip(url):
    low = url.lower().split("?")[0]
    return low.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".exe", ".msi", ".mp4",
                         ".css", ".js", ".ico", ".woff", ".woff2", ".xml"))
