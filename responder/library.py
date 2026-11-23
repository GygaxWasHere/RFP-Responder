"""Knowledge areas on a shared (SharePoint/OneDrive-synced) folder.

SQLite cannot be shared live through a sync client: locks do not travel, and the sync client
uploads half-written files and makes conflict copies. So nothing is ever opened in place:

  <root>/areas/<slug>.json       manifest: name, sources, version, who built it and when
  <root>/areas/<slug>.db         published area database (read-only for everybody)
  <root>/areas/<slug>.prev.db    the version before, for roll back
  <root>/areas/<slug>.lock       present while someone changes the area
  <root>/answers/<yyyy>/<fp>.json  one file per saved answer (draft or approved)

Reading: each user works on a local copy in %LOCALAPPDATA%\\RFPResponder\\cache, refreshed when a
newer version has been published (the copy is integrity-checked before it is used).
Changing: take the lock, copy the published file to a local build file, change it there, then
publish it in one step (copy up as a temp file, keep the old as .prev, rename the new into
place). If someone else published in between, the publish is refused.
Answers are separate small files, so two people saving answers never write the same file.
"""
import contextlib
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import threading
import time
import uuid

from . import config, ingest, sqlsrc
from .kb import KB, expand, portable

DEFAULT_AREAS = [
    {"slug": "xprotect", "name": "XProtect", "description": "XProtect VMS: manuals, specifications, certificates, release notes."},
    {"slug": "briefcam", "name": "BriefCam", "description": "BriefCam video analytics."},
    {"slug": "arcules", "name": "Arcules", "description": "Arcules cloud video (VSaaS)."},
    {"slug": "husky", "name": "Husky IVO hardware", "description": "Husky IVO recording servers and appliances: datasheets, specifications."},
    {"slug": "partners", "name": "Technology partners", "description": "Partner integrations, Marketplace, ONVIF and device support."},
    {"slug": "company", "name": "Milestone company info", "description": "Annual report, ISO certificates, policies, company facts."},
]

LOCK_STALE_H = 12          # a lock older than this is treated as abandoned
CHECK_EVERY_S = 30         # how often to look for newly published versions
ANSWER_SCAN_S = 60         # how often to rescan the shared answers folder
_HOST = socket.gethostname()


class AreaBusy(RuntimeError):
    pass


def clean_sql_source(src):
    """Only connection facts and the query are shared; a password never is."""
    out = {"type": "sql", "name": (src.get("name") or "").strip(), "server": (src.get("server") or "").strip(),
           "database": (src.get("database") or "").strip(), "auth": "sql" if src.get("auth") == "sql" else "windows",
           "user": (src.get("user") or "").strip() if src.get("auth") == "sql" else "",
           "query": (src.get("query") or "").strip(), "title_col": (src.get("title_col") or "").strip(),
           "key_col": (src.get("key_col") or "").strip(), "trust_cert": bool(src.get("trust_cert", True))}
    for k in ("name", "server", "database", "query"):
        if not out[k]:
            raise ValueError(f"The database source needs a {k}.")
    sqlsrc.build_sql(out["query"])  # refuse anything but a read query up front
    return out


def source_collection(src):
    if src["type"] == "sql":
        return sqlsrc.collection_of(src)
    if src["type"] == "folder":
        return expand(src["path"])
    return ingest.prefix_of(src["url"], src.get("prefix"))


def source_label(src):
    if src["type"] == "sql":
        return f"{src['name']} ({src['server']}/{src['database']})"
    return expand(src.get("path") or src.get("url"))


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:40] or "area"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    config.replace_retry(tmp, path)


def _db_ok(path):
    """True when path is a complete, healthy SQLite file (a half-synced file fails here)."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            c.close()
    except sqlite3.Error:
        return False


class Library:
    def __init__(self, root, user=""):
        self.root = os.path.abspath(root)
        self.user = user
        self.areas_dir = os.path.join(self.root, "areas")
        self.answers_dir = os.path.join(self.root, "answers")
        key = hashlib.sha1(os.path.normcase(self.root).encode("utf-8")).hexdigest()[:8]
        self.cache_dir = os.path.join(config.BASE_DIR, "cache", key)
        self.build_dir = os.path.join(self.cache_dir, "build")
        for d in (self.areas_dir, self.answers_dir, self.cache_dir, self.build_dir):
            os.makedirs(d, exist_ok=True)
        self._lock = threading.RLock()
        self._checked = {}       # slug -> time of last "newer version?" check
        self._current = {}       # slug -> local cache path in use
        self._answers_scan = 0
        self._busy = set()       # slugs being changed by this process

    # ------------------------------------------------------------ paths
    def _p(self, slug, ext):
        if not re.fullmatch(r"[a-z0-9\-]+", slug or ""):
            raise ValueError("bad area id")
        return os.path.join(self.areas_dir, f"{slug}{ext}")

    # ------------------------------------------------------------ manifests
    def manifest(self, slug):
        m = _read_json(self._p(slug, ".json"))
        if m is None:
            d = next((a for a in DEFAULT_AREAS if a["slug"] == slug), None)
            if d is None:
                raise KeyError(f"area {slug}")
            m = dict(d)
        m.setdefault("sources", [])
        m.setdefault("version", 0)
        m["slug"] = slug
        return m

    def _save_manifest(self, m):
        _write_json(self._p(m["slug"], ".json"), m)

    def area_slugs(self):
        slugs = [a["slug"] for a in DEFAULT_AREAS]
        for f in sorted(glob.glob(os.path.join(self.areas_dir, "*.json"))):
            s = os.path.basename(f)[:-5]
            if re.fullmatch(r"[a-z0-9\-]+", s) and s not in slugs:
                slugs.append(s)
        return slugs

    def areas(self):
        out = []
        for slug in self.area_slugs():
            m = self.manifest(slug)
            m["lock"] = self.lock_info(slug)
            m["has_prev"] = os.path.exists(self._p(slug, ".prev.db"))
            m["published"] = os.path.exists(self._p(slug, ".db"))
            for src in m["sources"]:
                if src.get("type") == "folder":
                    src["path"] = expand(src["path"])
                    src["exists"] = os.path.isdir(src["path"])
            out.append(m)
        return out

    def create_area(self, name, description=""):
        slug = base = _slugify(name)
        i = 2
        while slug in self.area_slugs():
            slug = f"{base}-{i}"
            i += 1
        m = {"slug": slug, "name": name.strip(), "description": description, "sources": [], "version": 0}
        self._save_manifest(m)
        return m

    def summary(self):
        ms = [self.manifest(s) for s in self.area_slugs()]
        return {"root": self.root, "areas": len(ms),
                "documents": sum(m.get("documents", 0) for m in ms),
                "chunks": sum(m.get("chunks", 0) for m in ms),
                "answers": self.answers_index_count()}

    # ------------------------------------------------------------ lock
    def lock_info(self, slug):
        d = _read_json(self._p(slug, ".lock"))
        if d is None and os.path.exists(self._p(slug, ".lock")):
            d = {"user": "?", "since": ""}
        if d:
            try:
                age = time.time() - os.path.getmtime(self._p(slug, ".lock"))
            except OSError:
                return None
            d["stale"] = age > LOCK_STALE_H * 3600
            d["mine"] = d.get("host") == _HOST and slug in self._busy
        return d

    @contextlib.contextmanager
    def _area_lock(self, slug, what):
        path = self._p(slug, ".lock")
        info = self.lock_info(slug)
        if info and not info["stale"]:
            raise AreaBusy(f"{info.get('user') or 'Someone'} is {info.get('what') or 'changing'} this area "
                           f"(since {info.get('since', '?')}). Try again when they are done.")
        if info:
            with contextlib.suppress(OSError):
                os.remove(path)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise AreaBusy("Someone else just started changing this area. Try again in a moment.") from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"user": self.user, "host": _HOST, "since": _now(), "what": what}, f)
        self._busy.add(slug)
        try:
            yield
        finally:
            self._busy.discard(slug)
            with contextlib.suppress(OSError):
                os.remove(path)

    def break_lock(self, slug):
        with contextlib.suppress(OSError):
            os.remove(self._p(slug, ".lock"))

    # ------------------------------------------------------------ reading (local cache)
    def area_kb(self, slug):
        """KB on the local copy of the published area, refreshed when a newer version is out.
        Returns None when the area has never been published."""
        with self._lock:
            now = time.time()
            if now - self._checked.get(slug, 0) > CHECK_EVERY_S or slug not in self._current:
                self._checked[slug] = now
                self._refresh_cache(slug)
            p = self._current.get(slug)
        return KB(p) if p else None

    def _refresh_cache(self, slug):
        pub = self._p(slug, ".db")
        try:
            st = os.stat(pub)
        except OSError:
            self._current.pop(slug, None)
            return
        stamp = hashlib.sha1(f"{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:10]
        local = os.path.join(self.cache_dir, f"{slug}.{stamp}.db")
        if not os.path.exists(local):
            tmp = local + ".part"
            try:
                shutil.copyfile(pub, tmp)
            except OSError:
                return  # keep the copy we have; try again on the next check
            if not _db_ok(tmp):
                with contextlib.suppress(OSError):
                    os.remove(tmp)
                return  # probably still syncing
            os.replace(tmp, local)
        self._current[slug] = local
        for old in glob.glob(os.path.join(self.cache_dir, f"{slug}.*.db")):
            if old != local:
                with contextlib.suppress(OSError):
                    os.remove(old)  # fails while another thread still reads it; removed next time

    def search(self, text, limit=8, slugs=None, extra=()):
        """Search the given areas (and extra KBs, e.g. the project's own sources). Best scores first."""
        hits = []
        for slug in slugs if slugs is not None else self.area_slugs():
            k = self.area_kb(slug)
            if k:
                for h in k.search(text, limit=limit):
                    h["area"] = slug
                    hits.append(h)
        for name, k in extra:
            for h in k.search(text, limit=limit):
                h["area"] = name
                hits.append(h)
        hits.sort(key=lambda h: h["score"])
        return hits[:limit]

    # ------------------------------------------------------------ changing an area
    @contextlib.contextmanager
    def _build(self, slug, what, fresh=False):
        """Lock the area, give a local build KB, and publish it when the block ends without error."""
        with self._area_lock(slug, what):
            m = self.manifest(slug)
            base_version = m.get("version", 0)
            build = os.path.join(self.build_dir, f"{slug}.{uuid.uuid4().hex[:6]}.db")
            pub = self._p(slug, ".db")
            if not fresh and os.path.exists(pub):
                shutil.copyfile(pub, build)
                if not _db_ok(build):
                    os.remove(build)
                    raise RuntimeError("The published area file is incomplete (still syncing?). Try again shortly.")
            try:
                k = KB(build)
                yield k, m
                self._publish(slug, build, k, base_version)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(build)

    def _publish(self, slug, build, k, base_version):
        m = self.manifest(slug)
        if m.get("version", 0) != base_version:
            raise RuntimeError(f"{m.get('built_by') or 'Someone'} published a new version of this area meanwhile. "
                               "Your change was not published; run it again.")
        st = k.stats()
        m["version"] = base_version + 1
        m.update(built_at=_now(), built_by=self.user, documents=st["documents"], chunks=st["chunks"])
        k.set_meta(area=slug, version=m["version"], built_at=m["built_at"], built_by=self.user)
        c = sqlite3.connect(build)  # compact before upload (VACUUM cannot run inside a transaction)
        try:
            c.execute("VACUUM")
        finally:
            c.close()
        m["size_mb"] = round(os.path.getsize(build) / 1048576, 1)
        pub, prev = self._p(slug, ".db"), self._p(slug, ".prev.db")
        up = self._p(slug, f".{uuid.uuid4().hex[:6]}.uploading")
        shutil.copyfile(build, up)
        try:
            if os.path.exists(pub):
                os.replace(pub, prev)
            os.replace(up, pub)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(up)
            raise
        self._save_manifest(m)
        with self._lock:
            self._checked.pop(slug, None)

    def update_area(self, slug, full=False, job=None):
        """Re-read the area's sources. full=True starts from an empty database (new release, cleanup)."""
        log = job.log if job else (lambda *_: None)
        with self._build(slug, "rebuilding" if full else "updating", fresh=full) as (k, m):
            srcs = m["sources"]
            if not srcs:
                raise ValueError("This area has no sources yet. Add a folder or a website first.")
            keep = set()
            for i, src in enumerate(srcs, 1):
                if job and job.cancelled:
                    raise RuntimeError("Cancelled; nothing was published.")
                log(f"Source {i}/{len(srcs)}: {source_label(src)}")
                if src["type"] == "sql":
                    r = sqlsrc.ingest_sql(k, src, self.user, job)
                    keep.add(r["collection"])
                elif src["type"] == "folder":
                    path = expand(src["path"])
                    keep.add(portable(path))
                    if not os.path.isdir(path):
                        raise RuntimeError(f"Folder not found: {path}. Is the SharePoint library synced on this PC?")
                    ingest.ingest_folder(k, path, portable(path), self.user, job)
                else:
                    r = ingest.crawl_site(k, src["url"], int(src.get("max_pages") or 200), src.get("prefix") or None,
                                          0.5, self.user, job)
                    keep.add(r["collection"])
            if job and job.cancelled:
                raise RuntimeError("Cancelled; nothing was published.")
            for c in k.collections():
                if portable(c["collection"]) not in keep:
                    n = k.delete_collection(c["collection"])
                    log(f"Removed {n} documents from a source no longer in the area: {c['collection']}")
        m = self.manifest(slug)
        log(f"Published {m['name']} version {m['version']}: {m.get('documents')} documents, {m.get('size_mb')} MB")
        return {"area": slug, "version": m["version"], "documents": m.get("documents"), "chunks": m.get("chunks")}

    def add_source(self, slug, src):
        with self._area_lock(slug, "editing"):
            m = self.manifest(slug)
            if src.get("type") == "folder":
                src = {"type": "folder", "path": portable(src["path"])}
                if any(s.get("path") == src["path"] for s in m["sources"]):
                    return m
            elif src.get("type") == "sql":
                src = clean_sql_source(src)
                if any(s.get("type") == "sql" and sqlsrc.collection_of(s) == sqlsrc.collection_of(src) for s in m["sources"]):
                    raise ValueError("This area already has a database source with that name. Give it another name.")
            else:
                url = src["url"].strip()
                if not url.startswith(("http://", "https://")):
                    raise ValueError("Enter a full web address starting with https://")
                src = {"type": "web", "url": url, "max_pages": int(src.get("max_pages") or 200)}
            m["sources"].append(src)
            self._save_manifest(m)
            return m

    def set_source(self, slug, index, changes):
        with self._area_lock(slug, "editing"):
            m = self.manifest(slug)
            i = int(index)
            src = m["sources"][i]
            if src["type"] == "web":
                for k in ("url", "max_pages"):
                    if k in changes:
                        src[k] = int(changes[k]) if k == "max_pages" else str(changes[k]).strip()
            elif src["type"] == "sql":
                new = clean_sql_source({**src, **changes})
                if sqlsrc.collection_of(new) != sqlsrc.collection_of(src):
                    raise ValueError("Server, database and name cannot be changed; add a new source instead.")
                m["sources"][i] = new
            self._save_manifest(m)
            return m

    def remove_source(self, slug, index, job=None):
        """Drop a source and its documents from the published area."""
        with self._build(slug, "removing a source") as (k, m):
            src = m["sources"].pop(int(index))
            n = k.delete_collection(source_collection(src))
            if job:
                job.log(f"Removed {n} documents")
            self._save_manifest(m)  # the version check uses the version field only
        return {"removed": n}

    def remove_documents(self, slug, locations, job=None):
        with self._build(slug, "removing documents") as (k, _m):
            n = sum(k.delete_document(loc) for loc in locations)
        if job:
            job.log(f"Removed {n} documents")
        return {"removed": n}

    def rollback(self, slug):
        with self._area_lock(slug, "rolling back"):
            pub, prev = self._p(slug, ".db"), self._p(slug, ".prev.db")
            if not os.path.exists(prev):
                raise ValueError("There is no earlier version to roll back to.")
            tmp = self._p(slug, ".swap")
            os.replace(pub, tmp)
            os.replace(prev, pub)
            os.replace(tmp, prev)
            m = self.manifest(slug)
            tmpk = os.path.join(self.build_dir, f"{slug}.rb.db")
            shutil.copyfile(pub, tmpk)
            k = KB(tmpk)
            st = k.stats()
            m.update(version=m.get("version", 0) + 1, built_at=_now(), built_by=f"{self.user} (roll back)",
                     documents=st["documents"], chunks=st["chunks"],
                     size_mb=round(os.path.getsize(pub) / 1048576, 1))
            os.remove(tmpk)
            self._save_manifest(m)
            with self._lock:
                self._checked.pop(slug, None)
        return m

    def documents(self, slug, query=""):
        k = self.area_kb(slug)
        return k.documents(query) if k else []

    def area_folders(self, slugs):
        out = []
        for slug in slugs:
            for s in self.manifest(slug)["sources"]:
                if s.get("type") == "folder" and os.path.isdir(expand(s["path"])):
                    out.append(expand(s["path"]))
        return out

    # ------------------------------------------------------------ answers
    def _answers_db(self):
        p = os.path.join(self.cache_dir, "answers.db")
        c = sqlite3.connect(p, timeout=30)
        c.row_factory = sqlite3.Row
        c.executescript("""
            CREATE TABLE IF NOT EXISTS answers(fp TEXT PRIMARY KEY, file TEXT, mtime REAL, data TEXT,
              project TEXT, project_id TEXT, status TEXT, areas TEXT, updated_at TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS answers_fts USING fts5(fp UNINDEXED, requirement, explanation,
              tokenize='porter unicode61');""")
        return c

    @staticmethod
    def fingerprint(project_id, row):
        return hashlib.sha1(f"{project_id}|{row.get('id')}|{row.get('text')}".encode("utf-8")).hexdigest()[:20]

    def save_answer(self, project, row, status):
        fp = self.fingerprint(project["id"], row)
        folder = os.path.join(self.answers_dir, time.strftime("%Y"))
        os.makedirs(folder, exist_ok=True)
        path = next(iter(glob.glob(os.path.join(self.answers_dir, "*", fp + ".json"))), os.path.join(folder, fp + ".json"))
        old = _read_json(path) or {}
        areas = project.get("areas")
        rec = {"fp": fp, "project": project.get("name"), "project_id": project["id"],
               "customer": project.get("customer"), "req_id": row.get("id"), "requirement": row.get("text"),
               "product": row.get("product"), "areas": areas if areas is not None else [],
               "compliance": row.get("compliance"), "explanation": row.get("explanation"),
               "evidence": row.get("evidence") or [], "links": row.get("links") or [],
               "responsible": row.get("responsible"), "status": status, "author": self.user,
               "created_at": old.get("created_at") or _now(), "updated_at": _now()}
        _write_json(path, rec)
        self._index_answer(rec, path)
        return fp

    def _index_answer(self, rec, path, c=None):
        own = c is None
        c = c or self._answers_db()
        try:
            c.execute("DELETE FROM answers_fts WHERE fp=?", (rec["fp"],))
            c.execute("INSERT OR REPLACE INTO answers VALUES(?,?,?,?,?,?,?,?,?)",
                      (rec["fp"], path, os.path.getmtime(path), json.dumps(rec, ensure_ascii=False),
                       rec.get("project"), rec.get("project_id"), rec.get("status"),
                       "," + ",".join(rec.get("areas") or []) + ",", rec.get("updated_at")))
            c.execute("INSERT INTO answers_fts(fp, requirement, explanation) VALUES(?,?,?)",
                      (rec["fp"], rec.get("requirement") or "", rec.get("explanation") or ""))
            if own:
                c.commit()
        finally:
            if own:
                c.close()

    def sync_answers(self, force=False):
        """Bring the local answer index in line with the shared answers folder."""
        if not force and time.time() - self._answers_scan < ANSWER_SCAN_S:
            return
        self._answers_scan = time.time()
        files = {}
        for root, _dirs, names in os.walk(self.answers_dir):
            for n in names:
                if n.endswith(".json"):
                    files[n[:-5]] = os.path.join(root, n)
        c = self._answers_db()
        try:
            known = {r["fp"]: r["mtime"] for r in c.execute("SELECT fp, mtime FROM answers")}
            for fp in set(known) - set(files):
                c.execute("DELETE FROM answers WHERE fp=?", (fp,))
                c.execute("DELETE FROM answers_fts WHERE fp=?", (fp,))
            for fp, path in files.items():
                try:
                    mt = os.path.getmtime(path)
                except OSError:
                    continue
                if known.get(fp) == mt:
                    continue
                rec = _read_json(path)
                if rec and rec.get("fp") == fp:
                    self._index_answer(rec, path, c)
            c.commit()
        finally:
            c.close()

    def answers_index_count(self):
        self.sync_answers()
        c = self._answers_db()
        try:
            return c.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
        finally:
            c.close()

    def precedents(self, text, limit=3, approved_only=True, exclude_project=None, slugs=None):
        q = KB.fts_query(text)
        if not q:
            return []
        self.sync_answers()
        sql = ("SELECT a.data FROM answers_fts f JOIN answers a ON a.fp=f.fp WHERE answers_fts MATCH ?")
        args = [q]
        if approved_only:
            sql += " AND a.status='approved'"
        if exclude_project:
            sql += " AND a.project_id<>?"
            args.append(exclude_project)
        if slugs is not None:
            sql += " AND (a.areas=',,'" + "".join(" OR a.areas LIKE ?" for _ in slugs) + ")"
            args += [f"%,{s},%" for s in slugs]
        sql += " ORDER BY bm25(answers_fts) LIMIT ?"
        args.append(limit)
        c = self._answers_db()
        try:
            return [json.loads(r["data"]) for r in c.execute(sql, args)]
        except sqlite3.OperationalError:
            return []
        finally:
            c.close()

    def list_answers(self, query="", limit=200):
        self.sync_answers(force=True)
        c = self._answers_db()
        try:
            if query:
                q = KB.fts_query(query)
                if not q:
                    return []
                rows = c.execute("SELECT a.data FROM answers_fts f JOIN answers a ON a.fp=f.fp "
                                 "WHERE answers_fts MATCH ? ORDER BY bm25(answers_fts) LIMIT ?", (q, limit))
            else:
                rows = c.execute("SELECT data FROM answers ORDER BY updated_at DESC LIMIT ?", (limit,))
            return [json.loads(r["data"]) for r in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            c.close()

    def delete_answer(self, fp):
        if not re.fullmatch(r"[0-9a-f]{6,40}", fp or ""):
            raise ValueError("bad answer id")
        for p in glob.glob(os.path.join(self.answers_dir, "*", fp + ".json")):
            os.remove(p)
        c = self._answers_db()
        try:
            c.execute("DELETE FROM answers WHERE fp=?", (fp,))
            c.execute("DELETE FROM answers_fts WHERE fp=?", (fp,))
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------ migration from the single file
    @staticmethod
    def suggest_area(text):
        t = (text or "").lower()
        for words, slug in ((("briefcam",), "briefcam"), (("arcules",), "arcules"), (("husky",), "husky"),
                            (("partner", "marketplace", "onvif"), "partners"),
                            (("annual report", "annual-report", "company", "iso ", "iso27001", "policy"), "company"),
                            (("xprotect", "milestonesys", "smart client", "management client"), "xprotect")):
            if any(w in t for w in words):
                return slug
        return ""

    def legacy_info(self, path):
        if not path or not os.path.isfile(path):
            return {"path": path, "exists": False}
        old = KB(path)
        cols = old.collections()
        for c in cols:
            c["suggested"] = self.suggest_area(c["collection"])
        return {"path": path, "exists": True, "collections": cols, "responses": len(old.legacy_responses())}

    def migrate(self, path, assign, import_answers=True, job=None):
        """assign: {collection: slug}. Copies each collection into its area and adds it as a source."""
        log = job.log if job else (lambda *_: None)
        old = KB(path)
        kinds = {c["collection"]: c["kind"] for c in old.collections()}
        by_area = {}
        for coll, slug in assign.items():
            if slug:
                by_area.setdefault(slug, []).append(coll)
        done = 0
        for slug, colls in by_area.items():
            if job and job.cancelled:
                break
            with self._build(slug, "importing") as (k, m):
                for coll in colls:
                    n = k.copy_collection_from(old, portable(coll), job)
                    log(f"{m['name']}: {n} documents from {coll}")
                    if kinds.get(coll) == "web":
                        if not any(s.get("url") == coll for s in m["sources"]):
                            m["sources"].append({"type": "web", "url": coll, "prefix": coll, "max_pages": max(200, n + 50)})
                    elif not any(s.get("path") == portable(coll) for s in m["sources"]):
                        m["sources"].append({"type": "folder", "path": portable(coll)})
                self._save_manifest(m)
            done += 1
            if job:
                job.update(progress=done / (len(by_area) + 1))
        answers = 0
        if import_answers and not (job and job.cancelled):
            for r in old.legacy_responses():
                slug = self.suggest_area(r.get("product") or "")
                row = {"id": r.get("req_id"), "text": r.get("requirement"), "product": r.get("product"),
                       "compliance": r.get("compliance"), "explanation": r.get("explanation"),
                       "responsible": r.get("responsible"),
                       "evidence": json.loads(r.get("evidence") or "[]"), "links": json.loads(r.get("links") or "[]")}
                proj = {"id": "legacy-" + hashlib.sha1((r.get("project") or "").encode()).hexdigest()[:8],
                        "name": r.get("project"), "customer": r.get("customer"), "areas": [slug] if slug else []}
                user = self.user
                self.user = r.get("author") or user
                try:
                    self.save_answer(proj, row, r.get("status") or "draft")
                finally:
                    self.user = user
                answers += 1
            log(f"{answers} earlier answers imported")
        return {"areas": done, "answers": answers}


class Scope:
    """What one response may draw on: the chosen areas plus the project's own sources.
    Offers the small interface the engine uses (search, precedents, doc_text, doc_state)."""

    def __init__(self, lib, slugs, project_kb=None, names=None):
        self.lib, self.slugs, self.project_kb = lib, slugs, project_kb
        self.names = names or {}

    def _kbs(self):
        if self.project_kb:
            yield self.project_kb
        for s in self.slugs:
            k = self.lib.area_kb(s)
            if k:
                yield k

    def search(self, text, limit=8):
        hits = self.lib.search(text, limit, self.slugs, [("project", self.project_kb)] if self.project_kb else ())
        for h in hits:
            h["area_name"] = "This response's sources" if h["area"] == "project" else self.names.get(h["area"], h["area"])
        return hits

    def precedents(self, text, limit=3, exclude_project=None):
        if not self.slugs:
            return []
        return self.lib.precedents(text, limit, exclude_project=exclude_project, slugs=self.slugs)

    def doc_text(self, location):
        for k in self._kbs():
            t = k.doc_text(location)
            if t is not None:
                return t
        return None

    def doc_state(self, location):
        for k in self._kbs():
            st = k.doc_state(location)
            if st:
                return st
        return None

    def folders(self):
        return self.lib.area_folders(self.slugs)
