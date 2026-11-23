"""One SQLite file with full-text search over documents and their passages.

Used for each knowledge area (built locally, then published as a whole file, see library.py),
for the per-project source database, and to read the old single-file knowledge base during
migration. The live database is always a local file: SQLite must never be opened directly on a
SharePoint/OneDrive synced folder.

File locations are stored so they resolve on every team member's computer, Windows or Mac:
inside a synced SharePoint library as  %LIB:Global Bid Desk - Documents%/06. Tools/spec.pdf
(each computer finds its own copy of that library), elsewhere in the home folder as
%USERPROFILE%/Desktop/spec.pdf. Forward slashes are used in the stored form.
"""
import contextlib
import hashlib
import os
import re
import sqlite3
import time

from . import osdep

_HOME = os.path.normcase(os.path.expanduser("~")).rstrip("\\/")
_LIB = re.compile(r"^%LIB:([^%]+)%(.*)$", re.S)


def portable(path):
    """A local path -> the stored, computer-independent form (URLs and mssql:// unchanged)."""
    if not path or path.startswith("%") or "://" in path:
        return path
    p = os.path.abspath(path)
    pn = os.path.normcase(p)
    for name, root in osdep.sync_libraries():
        rn = os.path.normcase(root)
        if pn == rn or pn.startswith(rn + os.sep):
            rel = p[len(root):].strip("\\/").replace(os.sep, "/")
            return f"%LIB:{name}%" + (f"/{rel}" if rel else "")
    if pn.startswith(_HOME + os.sep):
        return "%USERPROFILE%/" + p[len(_HOME) + 1:].replace(os.sep, "/")
    return p


def expand(path):
    """The stored form -> a path on this computer. A library that is not synced here stays as is."""
    if not path:
        return path
    m = _LIB.match(path)
    if m:
        root = osdep.library_root(m.group(1))
        if not root:
            return path
        parts = [x for x in re.split(r"[\\/]", m.group(2)) if x]
        return os.path.join(root, *parts)
    if path.startswith("%USERPROFILE%"):
        parts = [x for x in re.split(r"[\\/]", path[len("%USERPROFILE%"):]) if x]
        return os.path.join(os.path.expanduser("~"), *parts)
    return path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY, kind TEXT, location TEXT UNIQUE, title TEXT, collection TEXT,
  hash TEXT, mtime REAL, size INTEGER, added_by TEXT, added_at TEXT);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id INTEGER, ord INTEGER, heading TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, heading, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS responses(
  id INTEGER PRIMARY KEY, project TEXT, customer TEXT, req_id TEXT, requirement TEXT,
  product TEXT, compliance TEXT, explanation TEXT, evidence TEXT, links TEXT, responsible TEXT,
  status TEXT, author TEXT, created_at TEXT, updated_at TEXT, fingerprint TEXT UNIQUE);
CREATE VIRTUAL TABLE IF NOT EXISTS responses_fts USING fts5(requirement, explanation, tokenize='porter unicode61');
"""

STOP = set("""a an and are as at be been being by can could do does for from has have how if in into is it its
may must not of on or our over shall should such than that the their them then there these they this to under
upon was we were what when where which while who will with would you your all any each other also only per via
system systems solution provide provided support supported supports able required requirement requirements
der die das und oder ist sind ein eine mit für von zu im auf den dem des nicht muss müssen soll sollen werden""".split())


class KB:
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")

    @contextlib.contextmanager
    def conn(self):
        """A short-lived connection: commit on success, roll back on error, always close.
        (sqlite3's own context manager does not close, which keeps the file locked on Windows.)"""
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA busy_timeout=30000")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def get_meta(self, key, default=None):
        with self.conn() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return r["value"] if r else default

    def set_meta(self, **kv):
        with self.conn() as c:
            for k, v in kv.items():
                c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, str(v)))

    # ------------------------------------------------------------ documents
    def doc_state(self, location):
        with self.conn() as c:
            r = c.execute("SELECT id, hash, mtime FROM documents WHERE location=?",
                          (portable(location),)).fetchone()
            return dict(r) if r else None

    def put_document(self, kind, location, title, collection, chunks, mtime=None, size=None,
                     user="", content_hash=None):
        """Insert or replace a document and its chunks. chunks = [(heading, text)]."""
        h = content_hash or hashlib.sha1("\n".join(t for _, t in chunks).encode("utf-8", "replace")).hexdigest()
        location, collection = portable(location), portable(collection)
        with self.conn() as c:
            old = c.execute("SELECT id FROM documents WHERE location=?", (location,)).fetchone()
            if old:
                self._drop_chunks(c, old["id"])
                c.execute("UPDATE documents SET kind=?, title=?, collection=?, hash=?, mtime=?, size=?, "
                          "added_by=?, added_at=? WHERE id=?",
                          (kind, title, collection, h, mtime, size, user, _now(), old["id"]))
                did = old["id"]
            else:
                did = c.execute("INSERT INTO documents(kind, location, title, collection, hash, mtime, size, "
                                "added_by, added_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                (kind, location, title, collection, h, mtime, size, user, _now())).lastrowid
            for i, (head, text) in enumerate(chunks):
                cid = c.execute("INSERT INTO chunks(doc_id, ord, heading, text) VALUES(?,?,?,?)",
                                (did, i, head, text)).lastrowid
                c.execute("INSERT INTO chunks_fts(rowid, text, heading) VALUES(?,?,?)", (cid, text, head or ""))
        return did

    def _drop_chunks(self, c, did):
        c.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (did,))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (did,))

    def delete_collection(self, collection):
        collection = portable(collection)
        with self.conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM documents WHERE collection=?", (collection,))]
            for did in ids:
                self._drop_chunks(c, did)
            c.execute("DELETE FROM documents WHERE collection=?", (collection,))
        return len(ids)

    def delete_missing(self, collection, keep_locations, kind="file"):
        keep = {portable(x) for x in keep_locations}
        collection = portable(collection)
        with self.conn() as c:
            gone = [r for r in c.execute("SELECT id, location FROM documents WHERE collection=? AND kind=?",
                                         (collection, kind)) if r["location"] not in keep]
            for r in gone:
                self._drop_chunks(c, r["id"])
                c.execute("DELETE FROM documents WHERE id=?", (r["id"],))
        return len(gone)

    def delete_document(self, location):
        with self.conn() as c:
            r = c.execute("SELECT id FROM documents WHERE location=?", (portable(location),)).fetchone()
            if not r:
                return 0
            self._drop_chunks(c, r["id"])
            c.execute("DELETE FROM documents WHERE id=?", (r["id"],))
            return 1

    def documents(self, query="", limit=500):
        sql = ("SELECT d.location, d.title, d.kind, d.collection, d.added_at, d.added_by, "
               "(SELECT COUNT(*) FROM chunks ch WHERE ch.doc_id=d.id) AS chunks FROM documents d")
        args = []
        if query:
            sql += " WHERE d.title LIKE ? OR d.location LIKE ?"
            args += [f"%{query}%"] * 2
        sql += " ORDER BY d.title LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            out = [dict(r) for r in c.execute(sql, args)]
        for r in out:
            r["location"], r["collection"] = expand(r["location"]), expand(r["collection"])
        return out

    def collections(self):
        with self.conn() as c:
            out = [dict(r) for r in c.execute(
                "SELECT collection, kind, COUNT(*) AS docs, MAX(added_at) AS updated, "
                "(SELECT COUNT(*) FROM chunks ch JOIN documents d2 ON ch.doc_id=d2.id "
                " WHERE d2.collection=d.collection) AS chunks "
                "FROM documents d GROUP BY collection, kind ORDER BY updated DESC")]
        for r in out:
            r["collection"] = expand(r["collection"])
        return out

    def stats(self):
        with self.conn() as c:
            return {
                "documents": c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "size_mb": round(os.path.getsize(self.path) / 1048576, 1) if os.path.exists(self.path) else 0,
            }

    def copy_collection_from(self, other, collection, job=None):
        """Copy one collection's documents and passages from another KB (used by migration)."""
        n = 0
        with other.conn() as src:
            docs = src.execute("SELECT * FROM documents WHERE collection=?", (collection,)).fetchall()
            for d in docs:
                if job and job.cancelled:
                    break
                chunks = [(r["heading"], r["text"]) for r in src.execute(
                    "SELECT heading, text FROM chunks WHERE doc_id=? ORDER BY ord", (d["id"],))]
                if chunks:
                    self.put_document(d["kind"], d["location"], d["title"], collection, chunks, mtime=d["mtime"],
                                      size=d["size"], user=d["added_by"] or "", content_hash=d["hash"])
                    n += 1
        return n

    def doc_text(self, location):
        with self.conn() as c:
            r = c.execute("SELECT id FROM documents WHERE location=?", (portable(location),)).fetchone()
            if not r:
                return None
            return "\n".join(x["text"] for x in c.execute(
                "SELECT text FROM chunks WHERE doc_id=? ORDER BY ord", (r["id"],)))

    def chunk(self, cid):
        with self.conn() as c:
            r = c.execute("SELECT ch.id, ch.heading, ch.text, d.location, d.title, d.kind, d.collection "
                          "FROM chunks ch JOIN documents d ON d.id=ch.doc_id WHERE ch.id=?", (cid,)).fetchone()
        if not r:
            return None
        r = dict(r)
        r["location"] = expand(r["location"])
        return r

    # ------------------------------------------------------------ search
    @staticmethod
    def fts_query(text, max_terms=24):
        words = re.findall(r"[\w\-\.]+", (text or "").lower())
        seen, terms = set(), []
        for w in words:
            w = w.strip(".-")
            if len(w) < 3 or w in STOP or w in seen or w.isdigit():
                continue
            seen.add(w)
            terms.append('"' + w.replace('"', "") + '"')
            if len(terms) >= max_terms:
                break
        return " OR ".join(terms)

    def search(self, text, limit=8, collections=None):
        q = self.fts_query(text)
        if not q:
            return []
        sql = ("SELECT ch.id, ch.heading, ch.text, d.location, d.title, d.kind, d.collection, "
               "bm25(chunks_fts) AS score FROM chunks_fts JOIN chunks ch ON ch.id=chunks_fts.rowid "
               "JOIN documents d ON d.id=ch.doc_id WHERE chunks_fts MATCH ?")
        args = [q]
        if collections:
            sql += " AND d.collection IN (%s)" % ",".join("?" * len(collections))
            args += [portable(x) for x in collections]
        sql += " ORDER BY score LIMIT ?"
        args.append(limit * 3)
        with self.conn() as c:
            try:
                rows = [dict(r) for r in c.execute(sql, args)]
            except sqlite3.OperationalError:
                return []
        # at most 2 chunks per document, so one big file does not crowd out the rest
        out, per, seen = [], {}, set()
        for r in rows:
            sig = re.sub(r"\s+", " ", r["text"][:300])
            if sig in seen:
                continue
            seen.add(sig)
            n = per.get(r["location"], 0)
            if n < 2:
                per[r["location"]] = n + 1
                r["location"] = expand(r["location"])
                out.append(r)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------ old answers (migration only)
    def legacy_responses(self):
        with self.conn() as c:
            try:
                return [dict(r) for r in c.execute("SELECT * FROM responses")]
            except sqlite3.OperationalError:
                return []


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")
