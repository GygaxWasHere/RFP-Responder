"""Local web server: serves the UI and a small JSON API on 127.0.0.1.

A random token is required on every API call, so other web pages in the browser cannot drive it.
"""
import json
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, engine, ingest, jobs, osdep, register, reporting, reqs, sqlsrc, writers
from .kb import KB
from .library import Library, Scope, clean_sql_source

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TOKEN = secrets.token_urlsafe(24)
LAST_PING = [time.time()]

_projects = {}           # id -> project dict kept in memory while the app runs
_plock = threading.RLock()
_lib = {"key": None, "lib": None}
_dialog_lock = threading.Lock()


def lib():
    s = config.load_settings()
    key = (os.path.normcase(s["kb_root"]), s.get("user_name", ""))
    if _lib["key"] != key:
        _lib["lib"] = Library(s["kb_root"], s.get("user_name", ""))
        _lib["key"] = key
    return _lib["lib"]


def project_kb(pid, create=False):
    path = config.project_db_path(pid)
    return KB(path) if create or os.path.exists(path) else None


def scope(p):
    lb = lib()
    slugs = lb.area_slugs() if p.get("areas") is None else [x for x in p["areas"] if x in lb.area_slugs()]
    names = {a: lb.manifest(a)["name"] for a in slugs}
    return Scope(lb, slugs if p.get("use_kb", True) else [], project_kb(p["id"]), names)


def get_project(pid):
    with _plock:
        if pid not in _projects:
            _projects[pid] = config.load_project(pid)
        return _projects[pid]


def save_project(p):
    with _plock:
        _projects[p["id"]] = p
        config.save_project(p)
        _dirty.add(p["id"])


# ------------------------------------------------------------------ requirements register
_dirty = set()           # responses changed since they were last published to the register
_reg = {"key": None, "reg": None}


def reg():
    lb = lib()
    if _reg["key"] != lb.root:
        _reg["reg"] = register.Register(lb.root, lb.cache_dir)
        _reg["key"] = lb.root
    return _reg["reg"]


def publish_now(pid):
    _dirty.discard(pid)
    p = get_project(pid)
    return register.publish(p, lib().root, config.load_settings().get("scale") or config.DEFAULT_SCALE)


def _publisher():
    """Publish changed responses about once a minute (not on every keystroke, and not on SharePoint
    for every autosave)."""
    while True:
        time.sleep(60)
        for pid in list(_dirty):
            try:
                publish_now(pid)
            except Exception:  # noqa: BLE001 - the shared folder may be offline; try again next round
                _dirty.add(pid)


threading.Thread(target=_publisher, daemon=True).start()


# ------------------------------------------------------------------ native dialogs

def _migrate(lb, path, assign, answers, job=None):
    r = lb.migrate(path, assign, answers, job)
    if not (job and job.cancelled):
        config.save_settings({"legacy_imported": True})
    return r


def browse(kind, title="", filetypes=None, initial=""):
    with _dialog_lock:
        return osdep.pick(kind, title, filetypes, initial)


FILETYPES = {
    "requirements": [("Requirement files", "*.xlsx *.xlsm *.docx"), ("All files", "*.*")],
    "excel": [("Excel", "*.xlsx *.xlsm")],
    "word": [("Word", "*.docx")],
    "any": [("Documents", "*.docx *.pdf *.txt *.msg *.eml *.md *.xlsx *.pptx"), ("All files", "*.*")],
    "db": [("Knowledge base", "*.db")],
}


# ------------------------------------------------------------------ API

def api(method, path, q, body):
    s = config.load_settings()
    user = s.get("user_name", "")

    if path == "/api/ping":
        LAST_PING[0] = time.time()
        return {"ok": True}
    if path == "/api/view":  # where the user was, so the app reopens there after a restart
        LAST_PING[0] = time.time()
        config.save_view({k: body.get(k) for k in ("project", "tab", "open")})
        return {"ok": True}
    if path == "/api/state":
        try:
            st = lib().summary()
        except Exception as e:  # noqa: BLE001
            st = {"root": s["kb_root"], "error": str(e)}
        return {"settings": s, "projects": config.list_projects(), "kb": st, "version": config.APP_VERSION,
                "jobs": jobs.active(), "view": config.load_view(), "data_dir": config.BASE_DIR,
                "platform": "mac" if osdep.IS_MAC else ("windows" if osdep.IS_WIN else "linux"),
                "libraries": [{"name": n, "path": p_} for n, p_ in sorted(osdep.sync_libraries())]}
    if path == "/api/settings" and method == "POST":
        return {"settings": config.save_settings(body)}
    if path == "/api/claude/test":
        return engine.test_claude(s)
    if path == "/api/browse":
        return {"path": browse(body.get("kind", "file"), body.get("title", ""),
                               FILETYPES.get(body.get("types", ""), None), body.get("initial", ""))}
    if path == "/api/open":
        p = body.get("path", "")
        if p and os.path.exists(p):
            osdep.open_path(p)
            return {"ok": True}
        return {"ok": False, "error": "File not found"}

    # projects
    if path == "/api/project/new":
        p = config.new_project(body.get("name") or "New response")
        p["owner"] = user
        save_project(p)
        return {"project": p}
    if path == "/api/project/get":
        return {"project": get_project(q.get("id", body.get("id", "")))}
    if path == "/api/project/save":
        with _plock:
            p = get_project(body["id"])
            for k, v in body.items():
                if k not in ("id", "rows", "runs", "created"):
                    p[k] = v
            save_project(p)
        return {"project": p}
    if path == "/api/project/delete":
        with _plock:
            _projects.pop(body["id"], None)
            _dirty.discard(body["id"])
            config.delete_project(body["id"])
        try:
            register.unpublish(body["id"], lib().root)
        except OSError:
            pass
        return {"ok": True}

    # requirements
    if path == "/api/req/preview":
        k = reqs.kind_of(body["path"])
        if k == "excel":
            return {"kind": k, "sheets": reqs.excel_preview(body["path"])}
        if k == "word":
            return {"kind": k, "sheets": [], "outline": reqs.word_outline(body["path"], body.get("mode") or "lists")}
        raise ValueError("Pick an .xlsx, .xlsm or .docx file")
    if path == "/api/req/load":
        with _plock:
            p = get_project(body["id"])
            p["req_source"].update(body["spec"])
            p["req_source"]["kind"] = reqs.kind_of(p["req_source"]["path"])
            new = reqs.load(p["req_source"])
            old = {r["text"]: r for r in p.get("rows", []) if r.get("compliance")}
            for r in new:  # keep answers already given to identical requirement text
                if r["text"] in old:
                    keep = old[r["text"]]
                    for f in ("compliance", "explanation", "product", "responsible", "evidence", "links",
                              "confidence", "gaps", "question", "status", "verified", "review_notes"):
                        r[f] = keep.get(f, r.get(f))
            p["rows"] = new
            if p["req_source"]["kind"] == "excel" and not p["output"]["excel"].get("path"):
                p["output"]["excel"]["sheet"] = p["req_source"].get("sheet", "")
            save_project(p)
        return {"project": p}
    if path == "/api/rows/update":
        with _plock:
            p = get_project(body["id"])
            idx = {r["key"]: r for r in p["rows"]}
            for ch in body["changes"]:
                r = idx.get(ch.get("key"))
                if r:
                    for f, v in ch.items():
                        if f in ("selected", "compliance", "explanation", "product", "responsible", "status",
                                 "gaps", "question", "links", "topic", "gap_type", "security"):
                            r[f] = v
                    if ch.get("status") == "approved":
                        r["approved_by"], r["approved_at"] = user, time.strftime("%Y-%m-%d %H:%M")
                    elif "status" in ch:
                        r.pop("approved_by", None)
                        r.pop("approved_at", None)
            save_project(p)
        return {"project": p}

    # responding
    if path == "/api/run":
        p = get_project(body["id"])
        keys = body.get("keys") or None
        j = jobs.start("Answering requirements", engine.respond, p, s, scope(p), keys, on_batch=save_project)
        return {"job": j.id}

    # export
    if path == "/api/export/excel":
        p = get_project(body["id"])
        return writers.write_excel(p, s, user)
    if path == "/api/export/word":
        p = get_project(body["id"])
        return writers.write_word(p, s, user)
    if path == "/api/kb/save-responses":
        p = get_project(body["id"])
        lb = lib()
        n = 0
        for r in p["rows"]:
            if r.get("compliance") and (r.get("status") == "approved" or not body.get("approved_only", True)):
                lb.save_answer(p, r, "approved" if r.get("status") == "approved" else "draft")
                n += 1
        return {"saved": n}

    # knowledge areas (shared)
    if path == "/api/kb/areas":
        lb = lib()
        legacy = None
        if not s.get("legacy_imported") and os.path.isfile(s.get("db_path") or ""):
            legacy = lb.legacy_info(s["db_path"])
        return {"areas": lb.areas(), "root": lb.root, "legacy": legacy, "answers": lb.answers_index_count()}
    if path == "/api/kb/area/create":
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("Give the area a name")
        return {"area": lib().create_area(name, body.get("description", ""))}
    if path == "/api/kb/area/describe":
        lb = lib()
        m = lb.manifest(body["slug"])
        m.update({k: str(body[k]).strip() for k in ("name", "description") if k in body})
        lb._save_manifest(m)
        return {"area": m}
    if path == "/api/kb/area/add-source":
        if body["source"].get("type") == "sql" and body.get("password"):
            sqlsrc.save_password(clean_sql_source(body["source"]), body["password"])
        return {"area": lib().add_source(body["slug"], body["source"])}
    if path == "/api/kb/area/set-source":
        ch = body.get("changes", {})
        if ch.get("type") == "sql" and body.get("password"):
            sqlsrc.save_password(clean_sql_source(ch), body["password"])
        return {"area": lib().set_source(body["slug"], body["index"], ch)}
    if path == "/api/kb/sql/test":
        src = clean_sql_source(body["source"])
        pw = body.get("password") or None
        out = {"preview": sqlsrc.preview(src, pw)}
        try:
            out["tables"] = sqlsrc.list_tables(src, pw)
        except Exception:  # noqa: BLE001 - the list is a convenience; the query worked
            out["tables"] = []
        if pw and src["auth"] == "sql":
            sqlsrc.save_password(src, pw)
        return out
    if path == "/api/kb/sql/tables":
        src = clean_sql_source({**body["source"], "name": body["source"].get("name") or "tables",
                                "query": body["source"].get("query") or "SELECT 1 AS x"})
        return {"tables": sqlsrc.list_tables(src, body.get("password") or None)}
    if path == "/api/kb/sql/password":
        sqlsrc.save_password(clean_sql_source(body["source"]), body["password"])
        return {"ok": True}
    if path == "/api/kb/area/remove-source":
        lb = lib()
        m = lb.manifest(body["slug"])
        if not os.path.exists(lb._p(body["slug"], ".db")):  # nothing published yet: just edit the list
            with lb._area_lock(body["slug"], "editing"):
                m["sources"].pop(int(body["index"]))
                lb._save_manifest(m)
            return {"area": m}
        j = jobs.start(f"Removing a source from {m['name']}", lb.remove_source, body["slug"], body["index"])
        return {"job": j.id}
    if path == "/api/kb/area/update":
        lb = lib()
        m = lb.manifest(body["slug"])
        full = bool(body.get("full"))
        j = jobs.start(f"{'Rebuilding' if full else 'Updating'} {m['name']}", lb.update_area, body["slug"], full)
        return {"job": j.id}
    if path == "/api/kb/area/rollback":
        return {"area": lib().rollback(body["slug"])}
    if path == "/api/kb/area/unlock":
        lib().break_lock(body["slug"])
        return {"ok": True}
    if path == "/api/kb/area/docs":
        return {"docs": lib().documents(body["slug"], body.get("q", ""))}
    if path == "/api/kb/area/remove-docs":
        lb = lib()
        j = jobs.start(f"Removing documents from {lb.manifest(body['slug'])['name']}", lb.remove_documents,
                       body["slug"], body["locations"])
        return {"job": j.id}
    if path == "/api/kb/search":
        lb = lib()
        slugs = body.get("areas")
        return {"hits": lb.search(body.get("q", ""), 12, slugs),
                "precedents": lb.precedents(body.get("q", ""), limit=5, approved_only=False, slugs=slugs)}
    if path == "/api/kb/responses":
        return {"responses": lib().list_answers(body.get("q", ""))}
    if path == "/api/kb/delete-response":
        lib().delete_answer(body["fp"])
        return {"ok": True}
    if path == "/api/kb/migrate":
        lb = lib()
        j = jobs.start("Importing the old knowledge base", _migrate, lb, s["db_path"], body.get("assign", {}),
                       bool(body.get("answers", True)))
        return {"job": j.id}
    if path == "/api/kb/legacy-dismiss":
        config.save_settings({"legacy_imported": True})
        return {"ok": True}

    # the response's own sources (local to this PC)
    if path == "/api/project/ingest":
        folder = body["path"]
        if not os.path.isdir(folder):
            raise ValueError("Folder not found")
        j = jobs.start(f"Indexing {os.path.basename(folder)}", ingest.ingest_folder, project_kb(body["id"], True),
                       folder, folder, user)
        return {"job": j.id}
    if path == "/api/project/crawl":
        url = body["url"].strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Enter a full web address starting with https://")
        j = jobs.start(f"Reading {urllib.parse.urlsplit(url).netloc}", ingest.crawl_site, project_kb(body["id"], True),
                       url, int(body.get("max_pages", 200)), None, 0.5, user)
        return {"job": j.id}
    if path == "/api/project/collections":
        k = project_kb(body["id"])
        return {"collections": k.collections() if k else []}

    # reporting
    scale = s.get("scale") or config.DEFAULT_SCALE

    def rfilters():
        pre = reporting.PRESETS.get(body.get("preset") or "all", reporting.PRESETS["all"])
        return {**pre["filters"], **{k: v for k, v in (body.get("filters") or {}).items() if v not in (None, "")}}

    if path == "/api/report/meta":
        return {"presets": {k: {kk: v[kk] for kk in ("label", "description", "filters")} for k, v in reporting.PRESETS.items()},
                "options": reg().options(), "topics": register.TOPICS, "gap_types": register.GAP_TYPES,
                "verticals": register.VERTICALS, "regions": register.REGIONS, "outcomes": register.OUTCOMES,
                "reports": reporting.list_reports(lib().root)}
    if path == "/api/report/summary":
        reg().sync(force=bool(body.get("refresh")))
        return reg().summary(rfilters(), scale)
    if path == "/api/report/rows":
        return {"rows": reg().rows(rfilters(), scale, limit=int(body.get("limit", 200)), offset=int(body.get("offset", 0)))}
    if path == "/api/report/export":
        out = body.get("path") or ""
        if not out:
            raise ValueError("Choose where to save the export")
        if not out.lower().endswith((".xlsx", ".csv", ".json")):
            out += ".xlsx"
        return reg().export(rfilters(), scale, out)
    if path == "/api/report/claude":
        f = rfilters()
        j = jobs.start("Writing a report with Claude", reporting.build_report, reg(), s, body.get("preset") or "all",
                       f, body.get("question", ""), user, lib().root)
        return {"job": j.id}
    if path == "/api/report/read":
        return {"markdown": reporting.read_report(body["path"], lib().root)}
    if path == "/api/register/publish":
        publish_now(body["id"])
        reg().sync(force=True)
        return {"ok": True}
    if path == "/api/project/suggest-profile":
        p = get_project(body["id"])
        j = jobs.start("Reading the tender profile", reporting.suggest_profile, p, s)
        return {"job": j.id}

    # jobs
    if path == "/api/job":
        j = jobs.get(q.get("id", body.get("id", "")))
        return j.to_dict() if j else {"status": "unknown"}
    if path == "/api/job/cancel":
        j = jobs.get(body.get("id", ""))
        if j:
            j.cancelled = True
        return {"ok": True}
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/json"):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.startswith("/api/"):
            if self.headers.get("X-Token") != TOKEN:
                return self._send(403, {"error": "forbidden"})
            body = {}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except ValueError:
                    return self._send(400, {"error": "bad json"})
            try:
                return self._send(200, api(method, u.path, q, body))
            except KeyError as e:
                return self._send(404, {"error": f"not found: {e}"})
            except Exception as e:  # noqa: BLE001 - shown in the UI
                return self._send(400, {"error": str(e)})
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        with open(full, "rb") as f:
            data = f.read()
        if rel == "index.html":
            if q.get("t") != TOKEN:
                return self._send(403, b"Start the app from RFP Responder.", "text/plain")
            data = data.replace(b"__TOKEN__", TOKEN.encode())
        self._send(200, data, mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows, reuse would let two programs share one port

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(port=0):
    httpd = _Server(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd
