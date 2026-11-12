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

from . import config, engine, ingest, jobs, reqs, writers
from .kb import KB

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TOKEN = secrets.token_urlsafe(24)
LAST_PING = [time.time()]

_projects = {}           # id -> project dict kept in memory while the app runs
_plock = threading.RLock()
_kb = {"path": None, "kb": None}
_dialog_lock = threading.Lock()


def kb():
    path = config.load_settings()["db_path"]
    if _kb["path"] != path:
        _kb["kb"] = KB(path)
        _kb["path"] = path
    return _kb["kb"]


def get_project(pid):
    with _plock:
        if pid not in _projects:
            _projects[pid] = config.load_project(pid)
        return _projects[pid]


def save_project(p):
    with _plock:
        _projects[p["id"]] = p
        config.save_project(p)


# ------------------------------------------------------------------ native dialogs

def browse(kind, title="", filetypes=None, initial=""):
    import tkinter as tk
    from tkinter import filedialog
    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            opts = {"title": title or "Select", "parent": root}
            if initial and os.path.isdir(initial):
                opts["initialdir"] = initial
            if kind == "folder":
                r = filedialog.askdirectory(**opts)
            elif kind == "save":
                r = filedialog.asksaveasfilename(filetypes=filetypes or [("All files", "*.*")], **opts)
            elif kind == "files":
                r = list(filedialog.askopenfilenames(filetypes=filetypes or [("All files", "*.*")], **opts))
            else:
                r = filedialog.askopenfilename(filetypes=filetypes or [("All files", "*.*")], **opts)
        finally:
            root.destroy()
    if isinstance(r, list):
        return [os.path.normpath(x) for x in r]
    return os.path.normpath(r) if r else ""


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
    if path == "/api/state":
        try:
            st = kb().stats()
        except Exception as e:  # noqa: BLE001
            st = {"path": s["db_path"], "error": str(e)}
        return {"settings": s, "projects": config.list_projects(), "kb": st, "version": config.APP_VERSION,
                "jobs": jobs.active()}
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
            os.startfile(p)  # noqa: S606 - opens the user's own output file/folder
            return {"ok": True}
        return {"ok": False, "error": "File not found"}

    # projects
    if path == "/api/project/new":
        p = config.new_project(body.get("name") or "New response")
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
            config.delete_project(body["id"])
        return {"ok": True}

    # requirements
    if path == "/api/req/preview":
        k = reqs.kind_of(body["path"])
        if k == "excel":
            return {"kind": k, "sheets": reqs.excel_preview(body["path"])}
        if k == "word":
            return {"kind": k, "sheets": []}
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
                                 "gaps", "question", "links"):
                            r[f] = v
            save_project(p)
        return {"project": p}

    # responding
    if path == "/api/run":
        p = get_project(body["id"])
        keys = body.get("keys") or None
        j = jobs.start("Answering requirements", engine.respond, p, s, kb(), keys, on_batch=save_project)
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
        k = kb()
        n = 0
        for r in p["rows"]:
            if r.get("compliance") and (r.get("status") == "approved" or not body.get("approved_only", True)):
                k.save_response(p.get("name"), p.get("customer"), r,
                                "approved" if r.get("status") == "approved" else "draft", user)
                n += 1
        return {"saved": n}

    # knowledge base
    if path == "/api/kb/ingest":
        folder = body["path"]
        if not os.path.isdir(folder):
            raise ValueError("Folder not found")
        j = jobs.start(f"Indexing {os.path.basename(folder)}", ingest.ingest_folder, kb(), folder,
                       body.get("collection") or folder, user)
        return {"job": j.id}
    if path == "/api/kb/crawl":
        url = body["url"].strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Enter a full web address starting with https://")
        j = jobs.start(f"Reading {urllib.parse.urlsplit(url).netloc}", ingest.crawl_site, kb(), url,
                       int(body.get("max_pages", 200)), body.get("prefix") or None, 0.5, user)
        return {"job": j.id}
    if path == "/api/kb/collections":
        return {"collections": kb().collections(), "stats": kb().stats()}
    if path == "/api/kb/delete-collection":
        return {"removed": kb().delete_collection(body["collection"])}
    if path == "/api/kb/search":
        return {"hits": kb().search(body.get("q", ""), limit=12),
                "precedents": kb().precedents(body.get("q", ""), limit=5, approved_only=False)}
    if path == "/api/kb/responses":
        return {"responses": kb().list_responses(body.get("q", ""))}
    if path == "/api/kb/delete-response":
        kb().delete_response(int(body["rid"]))
        return {"ok": True}

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
