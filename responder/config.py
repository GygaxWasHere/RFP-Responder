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

APP_NAME = "RFP Responder"
APP_VERSION = "0.1.0"

BASE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RFPResponder")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
PROJECTS_DIR = os.path.join(BASE_DIR, "projects")
RUNS_DIR = os.path.join(BASE_DIR, "runs")

DEFAULT_SCALE = [
    {"code": "C", "label": "Compliant", "color": "#E2EFDA",
     "meaning": "The offered product meets the requirement as standard, and the evidence shows it."},
    {"code": "P", "label": "Partially compliant", "color": "#FFF2CC",
     "meaning": "Met in part, or only with a condition, an add-on, a third party or a workaround."},
    {"code": "N", "label": "Not compliant", "color": "#F8CBAD",
     "meaning": "The offered product does not meet the requirement."},
    {"code": "A", "label": "Acknowledged", "color": "#DDEBF7",
     "meaning": "Not a product capability (integrator, customer or contractual scope). Noted and accepted."},
]

DEFAULT_SETTINGS = {
    "user_name": "",
    "user_initials": "",
    "db_path": os.path.join(BASE_DIR, "knowledge.db"),
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
    if not s.get("user_name"):
        s["user_name"] = os.environ.get("USERNAME", "")
    return s


def save_settings(new):
    _ensure_dirs()
    with _lock:
        s = load_settings()
        s.update({k: v for k, v in new.items() if k in DEFAULT_SETTINGS})
        _atomic_write(SETTINGS_FILE, s)
    return s


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


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
        "req_source": {"path": "", "kind": "", "sheet": "", "header_row": 1, "first_row": 2,
                       "id_col": "", "text_col": "", "section_col": "", "context_col": "",
                       "docx_mode": "lists"},
        "folders": [],          # [{"path":..., "label":..., "index": True}]
        "websites": [],         # [{"url":..., "max_pages": 200}]
        "use_kb": True,
        "products": [],         # names from settings["products"]
        "product_rules": "",    # e.g. "Use XProtect only, not BriefCam, even for analytics"
        "strategy": "",
        "strategy_files": [],
        "instructions": "",
        "output": {
            "excel": {"enabled": True, "path": "", "sheet": "", "overwrite": False,
                      "mode": "text", "compliance_col": "", "marks": {}, "mark_char": "X",
                      "use_label": True, "explanation_col": "", "product_col": "",
                      "links_col": "", "evidence_col": "", "confidence_col": "",
                      "owner_col": "", "initials_col": "", "out_path": ""},
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


def delete_project(pid):
    path = project_path(pid)
    if os.path.exists(path):
        os.remove(path)
