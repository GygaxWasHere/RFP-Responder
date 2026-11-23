"""Requirements register: every tender's requirements and compliance results, for reporting.

Each response publishes one file to the shared knowledge folder:
    <root>/register/<project id>.json   tender profile + answered requirements
Only the response's owner writes that file, so colleagues never write the same file. Everybody's
Reporting tab reads all files into a local SQLite index (cache/register.db), refreshed by
modification time, the same way as earlier answers.

Rows are classified (topic, security relevance, standards mentioned, gap type) by Claude while
answering; rows answered before that, or edited by hand, fall back to the keyword rules below.
"""
import csv
import json
import os
import re
import sqlite3
import time
import uuid

from . import config

TOPICS = ["Video management", "Recording & storage", "Video analytics", "Cybersecurity", "Access & identity",
          "Integration & APIs", "Cameras & devices", "Hardware & servers", "Network & infrastructure",
          "Mobile & web clients", "Cloud & hosting", "Redundancy & availability", "Licensing & commercial",
          "Services, support & training", "Certifications & standards", "Project & installation", "Other"]
GAP_TYPES = ["Not available", "Partly available", "Needs third party", "Needs configuration or services",
             "On roadmap", "Customer or integrator scope", "Unclear requirement"]
VERTICALS = ["Airports & aviation", "City surveillance & safe city", "Transportation & rail", "Ports & logistics",
             "Critical infrastructure & utilities", "Oil, gas & energy", "Government & defence", "Law enforcement",
             "Healthcare", "Education", "Retail", "Banking & finance", "Manufacturing & industrial",
             "Hospitality, venues & entertainment", "Commercial real estate", "Other"]
REGIONS = ["EMEA", "Americas", "APAC"]
OUTCOMES = ["Open", "Submitted", "Won", "Lost", "No bid", "Cancelled"]

_KW = {
    "Cybersecurity": r"encrypt|tls|ssl|certificate|cyber|harden|vulnerab|penetration|password|secure boot|"
                     r"audit log|integrity|tamper|malware|patch|iec\s*62443|fips|zero trust|sbom|cve",
    "Access & identity": r"active directory|ldap|single sign|sso|saml|oauth|kerberos|role[- ]based|rbac|user right|"
                         r"permission|two[- ]factor|2fa|mfa|authenticat|access control",
    "Recording & storage": r"record|storage|archiv|retention|raid|nas|san\b|evidence|export|fps|bitrate",
    "Video analytics": r"analytic|lpr|anpr|license plate|face|object detect|classification|ai\b|deep learning|"
                       r"people count|loiter|intrusion|line cross|synopsis",
    "Integration & APIs": r"\bapi\b|sdk|integrat|onvif|psim|third[- ]party|interface|protocol|bacnet|opc|modbus|"
                          r"webhook|mip|plug[- ]in",
    "Cameras & devices": r"camera|ptz|fisheye|thermal|encoder|device|sensor|i/o\b|audio|microphone",
    "Hardware & servers": r"(?<!management )(?<!recording )(?<!event )(?<!log )server|appliance|workstation|hardware|"
                          r"cpu|gpu|husky|rack|power supply",
    "Network & infrastructure": r"network|bandwidth|multicast|vlan|ipv6|switch|firewall|latency",
    "Mobile & web clients": r"mobile|smartphone|web client|browser|app\b|ios|android",
    "Cloud & hosting": r"cloud|saas|vsaas|azure|aws|hosted|virtuali|vmware|hyper-v",
    "Redundancy & availability": r"failover|redundan|high availability|cluster|backup|disaster|uptime|99\.",
    "Licensing & commercial": r"licen|warranty|price|cost|subscription|care plus|payment|commercial",
    "Services, support & training": r"training|support|maintenance|service level|sla|helpdesk|commission",
    "Certifications & standards": r"iso\s*\d|iec\s*\d|en\s*\d|ul\s*\d|ndaa|gdpr|fcc|ce mark|accredit|compliance with",
    "Project & installation": r"install|project manag|site survey|cabling|commissioning|documentation|as-built",
    "Video management": r"vms|video management|live view|playback|alarm|event|map|video wall|smart client|bookmark",
}
_KW = {k: re.compile(v, re.I) for k, v in _KW.items()}
_STD = re.compile(r"\b(ISO(?:/IEC)?\s?\d{4,5}(?:-\d+)*|IEC\s?\d{4,5}(?:-\d+)*|EN\s?\d{3,5}(?:-\d+)*|UL\s?\d{3,4}|"
                  r"NDAA|GDPR|FIPS\s?140-\d|NIST(?:\s?SP)?\s?\d{3}-\d+|ONVIF(?:\s(?:Profile\s)?[STGMDAC])?|"
                  r"SOC\s?2|HIPAA|CJIS|Common Criteria|Cyber Essentials|TAA|BSI|NIS\s?2|CE)\b", re.I)


def classify(text):
    """Keyword fallback: (topic, security_relevant, standards)."""
    t = text or ""
    scores = {k: len(rx.findall(t)) for k, rx in _KW.items()}
    topic = max(scores, key=scores.get) if any(scores.values()) else "Other"
    security = bool(scores["Cybersecurity"] or scores["Access & identity"])
    return topic, security, standards(t)


def standards(text):
    seen, out = set(), []
    for m in _STD.findall(text or ""):
        s = re.sub(r"\s+", " ", m.strip()).upper().replace("ISO ", "ISO ").replace("PROFILE ", "Profile ")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ------------------------------------------------------------------ publishing

def record(project, scale):
    """The register record for one response (only answered rows)."""
    labels = {s["code"]: s["label"] for s in scale}
    rows = []
    for r in project.get("rows", []):
        if not r.get("compliance"):
            continue
        topic, sec, stds = classify(f"{r.get('section', '')} {r.get('text', '')}")
        rows.append({
            "key": r["key"], "id": r.get("id"), "section": r.get("section"), "text": r.get("text"),
            "compliance": r.get("compliance"), "compliance_label": labels.get(r.get("compliance"), r.get("compliance")),
            "product": r.get("product"), "explanation": r.get("explanation"), "gaps": r.get("gaps"),
            "question": r.get("question"), "considerations": r.get("considerations"),
            "responsible": r.get("responsible"), "status": r.get("status"),
            "confidence": r.get("confidence"), "verified": r.get("verified"),
            "topic": r.get("topic") or topic,
            "security": r.get("security") if r.get("security") is not None else sec,
            "standards": sorted(set((r.get("standards") or []) + stds)),
            "gap_type": r.get("gap_type") or "", "answered_at": r.get("answered_at"),
            "approved_by": r.get("approved_by"), "approved_at": r.get("approved_at")})
    prof = project.get("profile") or {}
    return {"id": project["id"], "name": project.get("name"), "customer": project.get("customer"),
            "role": project.get("role"), "language": project.get("language"), "products": project.get("products"),
            "areas": project.get("areas"), "owner": project.get("owner") or "", "profile": prof,
            "created": project.get("created"), "updated": project.get("updated"),
            "published_at": time.strftime("%Y-%m-%d %H:%M"), "requirements": len(project.get("rows", [])),
            "answered": len(rows), "rows": rows}


def register_dir(root):
    return os.path.join(root, "register")


def publish(project, root, scale):
    d = register_dir(root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{project['id']}.json")
    if project.get("register_share") is False or not any(r.get("compliance") for r in project.get("rows", [])):
        if os.path.exists(path) and project.get("register_share") is False:
            os.remove(path)  # made confidential: take it out of the register
        return None
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record(project, scale), f, ensure_ascii=False, indent=1)
    config.replace_retry(tmp, path)
    return path


def unpublish(project_id, root):
    p = os.path.join(register_dir(root), f"{project_id}.json")
    if os.path.exists(p):
        os.remove(p)


# ------------------------------------------------------------------ the local index

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, mtime REAL);
CREATE TABLE IF NOT EXISTS tenders(id TEXT PRIMARY KEY, name TEXT, customer TEXT, owner TEXT, products TEXT,
  vertical TEXT, country TEXT, region TEXT, consultant TEXT, partner TEXT, end_user_type TEXT, purpose TEXT,
  concerns TEXT, themes TEXT, due_date TEXT, outcome TEXT, created TEXT, updated TEXT, published_at TEXT,
  requirements INTEGER, answered INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS reqs(tender_id TEXT, key TEXT, rid TEXT, section TEXT, text TEXT, compliance TEXT,
  compliance_label TEXT, product TEXT, explanation TEXT, gaps TEXT, question TEXT, status TEXT, confidence INTEGER,
  topic TEXT, security INTEGER, standards TEXT, gap_type TEXT, answered_at TEXT, approved_by TEXT,
  PRIMARY KEY(tender_id, key));
CREATE VIRTUAL TABLE IF NOT EXISTS reqs_fts USING fts5(tender_id UNINDEXED, key UNINDEXED, text, explanation, gaps,
  tokenize='porter unicode61');
"""


class Register:
    def __init__(self, root, cache_dir):
        self.root = root
        self.db = os.path.join(cache_dir, "register.db")
        self._scan = 0

    def conn(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        return c

    def sync(self, force=False):
        if not force and time.time() - self._scan < 30:
            return
        self._scan = time.time()
        d = register_dir(self.root)
        files = {}
        if os.path.isdir(d):
            for n in os.listdir(d):
                if n.endswith(".json"):
                    files[n[:-5]] = os.path.join(d, n)
        c = self.conn()
        try:
            known = {r["id"]: r["mtime"] for r in c.execute("SELECT id, mtime FROM files")}
            for tid in set(known) - set(files):
                self._drop(c, tid)
                c.execute("DELETE FROM files WHERE id=?", (tid,))
            for tid, path in files.items():
                try:
                    mt = os.path.getmtime(path)
                    if known.get(tid) == mt:
                        continue
                    with open(path, encoding="utf-8") as f:
                        rec = json.load(f)
                except (OSError, ValueError):
                    continue
                self._drop(c, tid)
                self._load(c, rec)
                c.execute("INSERT OR REPLACE INTO files VALUES(?,?)", (tid, mt))
            c.commit()
        finally:
            c.close()

    @staticmethod
    def _drop(c, tid):
        c.execute("DELETE FROM tenders WHERE id=?", (tid,))
        c.execute("DELETE FROM reqs WHERE tender_id=?", (tid,))
        c.execute("DELETE FROM reqs_fts WHERE tender_id=?", (tid,))

    @staticmethod
    def _load(c, rec):
        p = rec.get("profile") or {}
        c.execute("INSERT INTO tenders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            rec["id"], rec.get("name"), rec.get("customer"), rec.get("owner"), json.dumps(rec.get("products") or []),
            p.get("vertical"), p.get("country"), p.get("region"), p.get("consultant"), p.get("partner"),
            p.get("end_user_type"), p.get("purpose"), p.get("concerns"), json.dumps(p.get("themes") or []),
            p.get("due_date"), p.get("outcome") or "Open", rec.get("created"), rec.get("updated"),
            rec.get("published_at"), rec.get("requirements"), rec.get("answered"),
            json.dumps({k: v for k, v in rec.items() if k != "rows"}, ensure_ascii=False)))
        for r in rec.get("rows", []):
            c.execute("INSERT OR REPLACE INTO reqs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                rec["id"], r["key"], r.get("id"), r.get("section"), r.get("text"), r.get("compliance"),
                r.get("compliance_label"), r.get("product"), r.get("explanation"), r.get("gaps"), r.get("question"),
                r.get("status"), r.get("confidence"), r.get("topic"), 1 if r.get("security") else 0,
                json.dumps(r.get("standards") or []), r.get("gap_type"), r.get("answered_at"), r.get("approved_by")))
            c.execute("INSERT INTO reqs_fts VALUES(?,?,?,?,?)", (rec["id"], r["key"], r.get("text") or "",
                                                                  r.get("explanation") or "", r.get("gaps") or ""))

    # -------------------------------------------------------------- queries
    def _where(self, f, scale):
        """Filters shared by every query. f keys: q, compliance (list of codes or 'gaps'), topic, product,
        vertical, region, country, outcome, security, tender, since, until, approved_only."""
        f = f or {}
        w, a = ["1=1"], []
        not_full = [s["code"] for s in scale if not s["label"].lower().startswith(("compliant", "ack", "fully", "not applicable"))]
        comp = f.get("compliance")
        if comp == "gaps":
            w.append("r.compliance IN (%s)" % ",".join("?" * len(not_full)) if not_full else "0")
            a += not_full
        elif comp:
            w.append("r.compliance IN (%s)" % ",".join("?" * len(comp)))
            a += list(comp)
        for col, key in (("r.topic", "topic"), ("t.vertical", "vertical"), ("t.region", "region"),
                         ("t.country", "country"), ("t.outcome", "outcome"), ("r.gap_type", "gap_type"),
                         ("t.id", "tender")):
            if f.get(key):
                w.append(f"{col}=?")
                a.append(f[key])
        if f.get("product"):
            w.append("r.product LIKE ?")
            a.append(f"%{f['product']}%")
        if f.get("security"):
            w.append("r.security=1")
        if f.get("approved_only"):
            w.append("r.status='approved'")
        if f.get("since"):
            w.append("COALESCE(r.answered_at, t.updated) >= ?")
            a.append(f["since"])
        if f.get("until"):
            w.append("COALESCE(r.answered_at, t.updated) <= ?")
            a.append(f["until"] + " 99")
        if f.get("q"):
            from .kb import KB
            q = KB.fts_query(f["q"])
            if q:
                w.append("(r.tender_id || '|' || r.key) IN (SELECT tender_id || '|' || key FROM reqs_fts WHERE reqs_fts MATCH ?)")
                a.append(q)
        return " AND ".join(w), a

    def summary(self, f, scale):
        self.sync()
        where, a = self._where(f, scale)
        base = f"FROM reqs r JOIN tenders t ON t.id=r.tender_id WHERE {where}"
        c = self.conn()
        try:
            def grp(col, limit=15, extra=""):
                return [dict(x) for x in c.execute(
                    f"SELECT COALESCE(NULLIF({col}, ''), '(not set)') AS k, COUNT(*) AS n, "
                    f"COUNT(DISTINCT r.tender_id) AS tenders {base} {extra} GROUP BY 1 ORDER BY n DESC LIMIT {limit}", a)]
            tot = c.execute(f"SELECT COUNT(*) AS n, COUNT(DISTINCT r.tender_id) AS t {base}", a).fetchone()
            comp = [dict(x) for x in c.execute(
                f"SELECT r.compliance AS k, COUNT(*) AS n {base} GROUP BY 1 ORDER BY n DESC", a)]
            # topics with their compliance mix (for "where do we fall short")
            mix = {}
            for x in c.execute(f"SELECT COALESCE(r.topic,'Other') AS topic, r.compliance, COUNT(*) AS n {base} GROUP BY 1, 2", a):
                mix.setdefault(x["topic"], {})[x["compliance"]] = x["n"]
            stds = {}
            for x in c.execute(f"SELECT r.standards {base}", a):
                for s in json.loads(x["standards"] or "[]"):
                    stds[s] = stds.get(s, 0) + 1
            tenders = [dict(x) for x in c.execute(
                f"SELECT t.id, t.name, t.customer, t.vertical, t.country, t.region, t.consultant, t.partner, t.outcome, "
                f"t.due_date, t.owner, t.purpose, t.concerns, t.themes, t.published_at, COUNT(*) AS rows_n {base} "
                f"GROUP BY t.id ORDER BY t.published_at DESC LIMIT 300", a)]
            return {
                "total": tot["n"], "tenders_n": tot["t"], "compliance": comp,
                "topics": grp("r.topic", 20), "topic_mix": mix, "products": grp("r.product"),
                "gap_types": grp("r.gap_type", 10, ""), "verticals": grp("t.vertical"), "regions": grp("t.region"),
                "countries": grp("t.country"), "consultants": grp("t.consultant"), "partners": grp("t.partner"),
                "outcomes": grp("t.outcome"), "owners": grp("t.owner"),
                "security": c.execute(f"SELECT COUNT(*) {base} AND r.security=1", a).fetchone()[0],
                "standards": sorted(({"k": k, "n": n} for k, n in stds.items()), key=lambda x: -x["n"])[:25],
                "tenders": tenders}
        finally:
            c.close()

    def rows(self, f, scale, limit=500, offset=0):
        self.sync()
        where, a = self._where(f, scale)
        c = self.conn()
        try:
            return [dict(x) for x in c.execute(
                f"SELECT r.*, t.name AS tender, t.customer, t.vertical, t.country, t.region, t.outcome "
                f"FROM reqs r JOIN tenders t ON t.id=r.tender_id WHERE {where} "
                f"ORDER BY t.published_at DESC, r.rowid LIMIT ? OFFSET ?", a + [limit, offset])]
        finally:
            c.close()

    def options(self):
        """Distinct values for the filter menus."""
        self.sync()
        c = self.conn()
        try:
            def d(sql):
                return [x[0] for x in c.execute(sql) if x[0]]
            return {"topics": d("SELECT DISTINCT topic FROM reqs ORDER BY 1"),
                    "products": d("SELECT DISTINCT product FROM reqs ORDER BY 1"),
                    "verticals": d("SELECT DISTINCT vertical FROM tenders ORDER BY 1"),
                    "regions": d("SELECT DISTINCT region FROM tenders ORDER BY 1"),
                    "countries": d("SELECT DISTINCT country FROM tenders ORDER BY 1"),
                    "outcomes": d("SELECT DISTINCT outcome FROM tenders ORDER BY 1"),
                    "gap_types": d("SELECT DISTINCT gap_type FROM reqs ORDER BY 1"),
                    "tenders": [dict(x) for x in c.execute("SELECT id, name, customer FROM tenders ORDER BY published_at DESC")]}
        finally:
            c.close()

    # -------------------------------------------------------------- export
    COLS = [("tender", "Tender"), ("customer", "Customer"), ("vertical", "Vertical"), ("country", "Country"),
            ("region", "Region"), ("outcome", "Outcome"), ("rid", "Req. ID"), ("section", "Section"),
            ("text", "Requirement"), ("compliance_label", "Compliance"), ("product", "Product"), ("topic", "Topic"),
            ("security", "Security relevant"), ("standards", "Standards"), ("gap_type", "Gap type"),
            ("explanation", "Response"), ("gaps", "Gaps / why not fully compliant"), ("question", "Question to customer"),
            ("status", "Status"), ("confidence", "Confidence"), ("approved_by", "Approved by"), ("answered_at", "Answered")]
    TCOLS = [("name", "Tender"), ("customer", "Customer"), ("vertical", "Vertical"), ("country", "Country"),
             ("region", "Region"), ("consultant", "Consultant / evaluator"), ("partner", "Partner / integrator"),
             ("outcome", "Outcome"), ("due_date", "Due date"), ("owner", "Bid owner"), ("purpose", "Why the tender"),
             ("concerns", "Customer concerns"), ("themes", "Key themes"), ("rows_n", "Requirements (filtered)"),
             ("published_at", "Last updated")]

    @staticmethod
    def _cell(k, v):
        if k == "security":
            return "Yes" if v else ""
        if k in ("standards", "themes") and isinstance(v, str) and v.startswith("["):
            return ", ".join(json.loads(v))
        return v

    def export(self, f, scale, path):
        rows = self.rows(f, scale, limit=1000000)
        tenders = self.summary(f, scale)["tenders"]
        if path.lower().endswith(".csv"):
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow([h for _, h in self.COLS])
                for r in rows:
                    w.writerow([self._cell(k, r.get(k)) for k, _ in self.COLS])
        elif path.lower().endswith(".json"):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"filters": f, "tenders": tenders, "requirements": rows}, fh, ensure_ascii=False, indent=1)
        else:
            import openpyxl
            from openpyxl.styles import Alignment, Font, PatternFill
            wb = openpyxl.Workbook()
            for ws, cols, data in ((wb.active, self.COLS, rows), (wb.create_sheet(), self.TCOLS, tenders)):
                ws.title = "Requirements" if cols is self.COLS else "Tenders"
                ws.append([h for _, h in cols])
                for r in data:
                    ws.append([self._cell(k, r.get(k)) for k, _ in cols])
                for cell in ws[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="1F3A5F")
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for i, (k, _) in enumerate(cols, 1):
                    wide = k in ("text", "explanation", "gaps", "question", "purpose", "concerns")
                    ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 60 if wide else 18
                    if wide:
                        for cell in list(ws.iter_cols(min_col=i, max_col=i, min_row=2))[0]:
                            cell.alignment = Alignment(wrap_text=True, vertical="top")
            wb.save(path)
        return {"path": path, "rows": len(rows), "tenders": len(tenders)}
