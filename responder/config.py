"""Settings and project storage.

Settings live per user in %LOCALAPPDATA%\\RFPResponder\\settings.json.
Projects (one JSON file per tender response) live in %LOCALAPPDATA%\\RFPResponder\\projects.
The knowledge base path is a setting, so it can point to a shared drive.
"""
import json
import os
import re
import time
import uuid
import copy
import threading

from . import osdep

APP_NAME = "RFP Responder"
APP_VERSION = "0.4.0"

BASE_DIR = os.path.realpath(osdep.data_dir())  # Windows: %LOCALAPPDATA%\RFPResponder; macOS: ~/Library/Application Support/RFPResponder
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
RUNS_DIR = os.path.join(BASE_DIR, "runs")

DEFAULT_SCALE = [
    {"code": "C", "label": "Compliant", "color": "#E2EFDA",
     "meaning": "The offered products meet the requirement, including Milestone modules and licences that are "
                "part of the offer and normal configuration."},
    {"code": "P", "label": "Partially compliant", "color": "#FFF2CC",
     "meaning": "A specific part of the requirement is not met, or it needs a third-party product, custom work "
                "or a workaround."},
    {"code": "N", "label": "Not compliant", "color": "#F8CBAD",
     "meaning": "The offered product does not meet the requirement."},
    {"code": "A", "label": "Acknowledged", "color": "#DDEBF7",
     "meaning": "Not a product capability (integrator, customer or contractual scope). Noted and accepted."},
]
# Earlier default wording, replaced in saved settings so old installs get the sharper definitions.
_OLD_MEANINGS = {
    "C": "The offered product meets the requirement as standard, and the evidence shows it.",
    "P": "Met in part, or only with a condition, an add-on, a third party or a workaround.",
}
DEFAULT_PARTIES = ["Milestone", "Contractor", "Customer", "Third party", "Milestone + Contractor"]

DEFAULT_SETTINGS = {
    "user_name": "",
    "user_initials": "",
    "style_files": [],      # responses the user is proud of (persona.py learns their style from them)
    "style_snippets": [],   # pasted example answers
    "style": {},            # the learned style guide: guide, detail, tone, excerpts, hash, at
    "kb_root": os.path.join(BASE_DIR, "knowledge"),   # shared folder with the knowledge areas
    "db_path": os.path.join(BASE_DIR, "knowledge.db"), # old single-file knowledge base (import only)
    "legacy_imported": False,
    "extra_libraries": [],
    "report_role": "product",  # the Reporting tab opens on this view: product | security | marketing | all   # folders to treat as synced SharePoint libraries, if not found automatically
    "claude_path": "claude",
    "model": "opus",
    "effort": "high",
    "batch_size": 5,
    "parallel": 2,
    "timeout_min": 20,
    "strict_evidence": True,
    "allow_web_lookup": True,
    "web_domains": ["doc.milestonesys.com", "www.milestonesys.com", "doc.developer.milestonesys.com"],
    "kb_hits": 8,
    "precedent_hits": 3,
    "products": [
        {"name": "XProtect Corporate 2026 R2",
         "notes": "Milestone flagship VMS for large, high-security, multi-site systems."},
        {"name": "XProtect Expert 2026 R2", "notes": ""},
        {"name": "XProtect Professional+ 2026 R2", "notes": ""},
    ],
    "scale": DEFAULT_SCALE,
    "responsible_parties": DEFAULT_PARTIES,
}

_lock = threading.Lock()


def _ensure_dirs():
    for d in (BASE_DIR, PROJECTS_DIR, RUNS_DIR):
        os.makedirs(d, exist_ok=True)


def load_settings():
    _ensure_dirs()
    s = copy.deepcopy(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s.update(json.load(f))
        except (OSError, ValueError):
            pass
    new = {x["code"]: x["meaning"] for x in DEFAULT_SCALE}
    for x in s.get("scale") or []:
        if x.get("code") in _OLD_MEANINGS and x.get("meaning") == _OLD_MEANINGS[x["code"]]:
            x["meaning"] = new[x["code"]]
    if not s.get("user_name"):
        s["user_name"] = os.environ.get("USERNAME") or os.environ.get("USER", "")
    extra = [x for x in (s.get("extra_libraries") or []) if x]
    if extra != osdep.EXTRA_LIBRARIES:
        osdep.EXTRA_LIBRARIES = extra
        osdep._libs["at"] = 0
    return s


def save_settings(new):
    _ensure_dirs()
    with _lock:
        s = load_settings()
        s.update({k: v for k, v in new.items() if k in DEFAULT_SETTINGS})
        _atomic_write(SETTINGS_FILE, s)
    return s


def _atomic_write(path, data):
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    replace_retry(tmp, path)


def replace_retry(tmp, path, tries=20):
    """os.replace, retried briefly: on Windows it fails while another thread (or OneDrive, or a virus
    scanner) has the target open for a moment."""
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                try:
                    os.remove(tmp)
                finally:
                    raise
            time.sleep(0.05 * (i + 1))


VIEW_FILE = os.path.join(BASE_DIR, "view.json")


def save_view(v):
    _ensure_dirs()
    _atomic_write(VIEW_FILE, v)


def load_view():
    try:
        with open(VIEW_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- projects

def new_project(name="New response"):
    return {
        "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4],
        "name": name,
        "customer": "",
        "language": "English",
        "role": "Vendor",
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "req_source": {"path": "", "kind": "", "sheet": "", "header_row": 1, "first_row": 2, "last_row": "",
                       "start_heading": -1, "stop_heading": -1,
                       "id_col": "", "text_col": "", "section_col": "", "context_col": "",
                       "docx_mode": "lists"},
        "folders": [],          # [{"path":..., "label":..., "index": True}]
        "websites": [],         # [{"url":..., "max_pages": 200}]
        "use_kb": True,
        "owner": "",            # who started the response (for the register)
        "register_share": True,
        "include_considerations": True,  # add "Consider: ..." to the answers in Excel/Word # publish requirements + compliance to the shared register for reporting
        "profile": {"vertical": "", "country": "", "region": "", "end_user_type": "", "consultant": "",
                    "partner": "", "purpose": "", "concerns": "", "themes": [], "due_date": "", "outcome": "Open"},
        "areas": None,          # knowledge areas to use (slugs); None = all
        "products": [],         # names from settings["products"]
        "product_rules": "",    # e.g. "Use XProtect only, not BriefCam, even for analytics"
        "strategy": "",
        "strategy_files": [],
        "instructions": "",
        "persona": {"mode": "standard", "preset": "balanced",   # see persona.py
                    "audience": {"procurement": 2, "owner": 2, "specifier": 1, "gatekeeper": 1, "approver": 1},
                    "detail": "standard", "tone": "neutral", "mirror_urls": [], "mirror_files": [], "scoring": ""},
        "output": {
            "excel": {"enabled": True, "path": "", "sheet": "", "overwrite": False,
                      "mode": "text", "compliance_col": "", "marks": {}, "mark_char": "X",
                      "use_label": True, "explanation_col": "", "product_col": "",
                      "links_col": "", "evidence_col": "", "confidence_col": "",
                      "owner_col": "", "initials_col": "", "considerations_col": "", "out_path": ""},
            "word": {"enabled": False, "template": "", "out_path": ""},
        },
        "rows": [],             # see reqs.py for the row shape
        "runs": [],
    }


def _safe_id(pid):
    if not re.fullmatch(r"[\w\-]+", pid or ""):
        raise ValueError("bad project id")
    return pid


def project_path(pid):
    return os.path.join(PROJECTS_DIR, _safe_id(pid) + ".json")


def save_project(p):
    _ensure_dirs()
    p["updated"] = time.strftime("%Y-%m-%d %H:%M")
    with _lock:
        _atomic_write(project_path(p["id"]), p)
    return p


def load_project(pid):
    with open(project_path(pid), encoding="utf-8") as f:
        p = json.load(f)
    base = new_project()
    for k, v in base.items():
        p.setdefault(k, v)
    for k, v in base["output"].items():
        p["output"].setdefault(k, v)
        for kk, vv in v.items():
            p["output"][k].setdefault(kk, vv)
    for k, v in base["req_source"].items():
        p["req_source"].setdefault(k, v)
    for k, v in base["profile"].items():
        p["profile"].setdefault(k, v)
    for k, v in base["persona"].items():
        p["persona"].setdefault(k, v)
    for k, v in base["persona"]["audience"].items():
        p["persona"]["audience"].setdefault(k, v)
    if not p.get("owner"):
        p["owner"] = load_settings().get("user_name", "")
    return p


def list_projects():
    _ensure_dirs()
    out = []
    for fn in os.listdir(PROJECTS_DIR):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(PROJECTS_DIR, fn), encoding="utf-8") as f:
                    p = json.load(f)
                rows = p.get("rows", [])
                out.append({"id": p["id"], "name": p.get("name"), "customer": p.get("customer"),
                            "updated": p.get("updated"), "rows": len(rows),
                            "answered": sum(1 for r in rows if r.get("compliance"))})
            except (OSError, ValueError, KeyError):
                continue
    return sorted(out, key=lambda x: x.get("updated") or "", reverse=True)


def project_db_path(pid):
    """Local database with the response's own indexed folders and websites."""
    return os.path.join(PROJECTS_DIR, _safe_id(pid) + ".db")


def delete_project(pid):
    for path in (project_path(pid), project_db_path(pid)):
        if os.path.exists(path):
            os.remove(path)
