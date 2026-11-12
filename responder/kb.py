"""Knowledge base: one SQLite file with full-text search.

Holds three things:
  * documents/chunks  - indexed files from folders and scraped web pages
  * responses         - earlier compliance answers (draft or approved), reusable as precedents
The file can sit on a shared drive. SQLite's WAL mode is not safe on network shares, so the
classic rollback journal is used with a long busy timeout, and every write is a short
transaction. That is fine for a bid team; a server database is the step up for heavy use.
"""
import hashlib
import json
import os
import re
import sqlite3
import time

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

    def conn(self):
        c = sqlite3.connect(self.path, timeout=60)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=60000")
        c.execute("PRAGMA journal_mode=DELETE")
        return c

    # ------------------------------------------------------------ documents
    def doc_state(self, location):
        with self.conn() as c:
            r = c.execute("SELECT id, hash, mtime FROM documents WHERE location=?", (location,)).fetchone()
            return dict(r) if r else None

    def put_document(self, kind, location, title, collection, chunks, mtime=None, size=None,
                     user="", content_hash=None):
        """Insert or replace a document and its chunks. chunks = [(heading, text)]."""
        h = content_hash or hashlib.sha1("\n".join(t for _, t in chunks).encode("utf-8", "replace")).hexdigest()
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
        with self.conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM documents WHERE collection=?", (collection,))]
            for did in ids:
                self._drop_chunks(c, did)
            c.execute("DELETE FROM documents WHERE collection=?", (collection,))
        return len(ids)

    def delete_missing(self, collection, keep_locations):
        keep = set(keep_locations)
        with self.conn() as c:
            gone = [r for r in c.execute("SELECT id, location FROM documents WHERE collection=? AND kind='file'",
                                         (collection,)) if r["location"] not in keep]
            for r in gone:
                self._drop_chunks(c, r["id"])
                c.execute("DELETE FROM documents WHERE id=?", (r["id"],))
        return len(gone)

    def collections(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT collection, kind, COUNT(*) AS docs, MAX(added_at) AS updated, "
                "(SELECT COUNT(*) FROM chunks ch JOIN documents d2 ON ch.doc_id=d2.id "
                " WHERE d2.collection=d.collection) AS chunks "
                "FROM documents d GROUP BY collection, kind ORDER BY updated DESC")]

    def stats(self):
        with self.conn() as c:
            return {
                "path": self.path,
                "documents": c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                "chunks": c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                "responses": c.execute("SELECT COUNT(*) FROM responses").fetchone()[0],
                "approved": c.execute("SELECT COUNT(*) FROM responses WHERE status='approved'").fetchone()[0],
                "size_mb": round(os.path.getsize(self.path) / 1048576, 1) if os.path.exists(self.path) else 0,
            }

    def doc_text(self, location):
        with self.conn() as c:
            r = c.execute("SELECT id FROM documents WHERE location=?", (location,)).fetchone()
            if not r:
                return None
            return "\n".join(x["text"] for x in c.execute(
                "SELECT text FROM chunks WHERE doc_id=? ORDER BY ord", (r["id"],)))

    def chunk(self, cid):
        with self.conn() as c:
            r = c.execute("SELECT ch.id, ch.heading, ch.text, d.location, d.title, d.kind, d.collection "
                          "FROM chunks ch JOIN documents d ON d.id=ch.doc_id WHERE ch.id=?", (cid,)).fetchone()
            return dict(r) if r else None

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
            args += list(collections)
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
                out.append(r)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------ responses
    def save_response(self, project, customer, row, status, author):
        fp = hashlib.sha1(f"{project}|{row.get('id')}|{row.get('text')}".encode("utf-8")).hexdigest()
        ev = json.dumps(row.get("evidence") or [], ensure_ascii=False)
        links = json.dumps(row.get("links") or [], ensure_ascii=False)
        with self.conn() as c:
            old = c.execute("SELECT id FROM responses WHERE fingerprint=?", (fp,)).fetchone()
            vals = (project, customer, row.get("id"), row.get("text"), row.get("product"),
                    row.get("compliance"), row.get("explanation"), ev, links, row.get("responsible"),
                    status, author)
            if old:
                c.execute("UPDATE responses SET project=?, customer=?, req_id=?, requirement=?, product=?, "
                          "compliance=?, explanation=?, evidence=?, links=?, responsible=?, status=?, author=?, "
                          "updated_at=? WHERE id=?", vals + (_now(), old["id"]))
                rid = old["id"]
                c.execute("DELETE FROM responses_fts WHERE rowid=?", (rid,))
            else:
                rid = c.execute("INSERT INTO responses(project, customer, req_id, requirement, product, compliance, "
                                "explanation, evidence, links, responsible, status, author, created_at, updated_at, "
                                "fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                vals + (_now(), _now(), fp)).lastrowid
            c.execute("INSERT INTO responses_fts(rowid, requirement, explanation) VALUES(?,?,?)",
                      (rid, row.get("text") or "", row.get("explanation") or ""))
        return rid

    def precedents(self, text, limit=3, approved_only=True, exclude_project=None):
        q = self.fts_query(text)
        if not q:
            return []
        sql = ("SELECT r.*, bm25(responses_fts) AS score FROM responses_fts JOIN responses r "
               "ON r.id=responses_fts.rowid WHERE responses_fts MATCH ?")
        args = [q]
        if approved_only:
            sql += " AND r.status='approved'"
        if exclude_project:
            sql += " AND r.project<>?"
            args.append(exclude_project)
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            try:
                return [dict(r) for r in c.execute(sql, args)]
            except sqlite3.OperationalError:
                return []

    def list_responses(self, query="", limit=200):
        with self.conn() as c:
            if query:
                q = self.fts_query(query)
                if not q:
                    return []
                try:
                    rows = c.execute("SELECT r.* FROM responses_fts JOIN responses r ON r.id=responses_fts.rowid "
                                     "WHERE responses_fts MATCH ? ORDER BY bm25(responses_fts) LIMIT ?", (q, limit))
                    return [dict(r) for r in rows]
                except sqlite3.OperationalError:
                    return []
            return [dict(r) for r in c.execute("SELECT * FROM responses ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def delete_response(self, rid):
        with self.conn() as c:
            c.execute("DELETE FROM responses_fts WHERE rowid=?", (rid,))
            c.execute("DELETE FROM responses WHERE id=?", (rid,))


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")
