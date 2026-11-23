"""Everything that differs between Windows and macOS, in one place.

  data_dir()          where settings, projects, caches live
  find_browser()      Edge or Chrome, for the app window
  open_path(p)        open a file/folder with the default program
  pick(...)           native file/folder dialogs (Windows: Tk; macOS: AppleScript via osascript)
  save_secret/get_secret   passwords for database sources, kept on this computer only
                      (Windows: DPAPI-encrypted file; macOS: the login Keychain)
  sync_libraries()    SharePoint/OneDrive libraries synced to this computer, by name
  claude_candidates() where Claude Code is usually installed
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
APP = "RFPResponder"


def data_dir():
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP)
    if IS_MAC:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), APP)
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), APP)


# ------------------------------------------------------------------ browser, open

def find_browser():
    if IS_WIN:
        cands = [shutil.which("msedge"),
                 os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                 os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                 shutil.which("chrome"),
                 os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")]
    elif IS_MAC:
        cands = []
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            cands += [f"{base}/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                      f"{base}/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        cands = [shutil.which(x) for x in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def open_path(p):
    if IS_WIN:
        os.startfile(p)  # noqa: S606 - the user's own file
    elif IS_MAC:
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


# ------------------------------------------------------------------ dialogs

def pick(kind, title="", filetypes=None, initial=""):
    """kind: file | files | folder | save. Returns a path, a list of paths (files) or '' when cancelled."""
    if IS_MAC:
        return _pick_mac(kind, title, filetypes, initial)
    return _pick_tk(kind, title, filetypes, initial)


def _pick_tk(kind, title, filetypes, initial):
    import tkinter as tk
    from tkinter import filedialog
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


def _as(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pick_mac(kind, title, filetypes, initial):
    # Tk must run on the main thread on macOS, and the server answers on worker threads, so
    # the dialogs are AppleScript ("choose file") run through osascript.
    prompt = f" with prompt {_as(title or 'Select')}"
    loc = f" default location (POSIX file {_as(initial)})" if initial and os.path.isdir(initial) else ""
    exts = sorted({e for _, pats in (filetypes or []) for e in re.findall(r"\*\.(\w+)", pats) if e != "*"})
    types = (" of type {" + ", ".join(_as(e) for e in exts) + "}") if exts and kind in ("file", "files") else ""
    if kind == "folder":
        body = f"POSIX path of (choose folder{prompt}{loc})"
    elif kind == "save":
        body = f"POSIX path of (choose file name{prompt}{loc})"
    elif kind == "files":
        body = (f"set fs to (choose file{prompt}{types}{loc} with multiple selections allowed)\n"
                "set out to \"\"\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\nout")
    else:
        body = f"POSIX path of (choose file{prompt}{types}{loc})"
    script = f'tell application "System Events"\nactivate\n{body}\nend tell'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0 and "-1743" in r.stderr:  # not allowed to control System Events: plain dialog
        r = subprocess.run(["osascript", "-e", body], capture_output=True, text=True)
    if r.returncode != 0:
        return [] if kind == "files" else ""  # cancelled
    out = r.stdout.strip()
    if kind == "files":
        return [os.path.normpath(x) for x in out.splitlines() if x.strip()]
    return os.path.normpath(out) if out else ""


# ------------------------------------------------------------------ secrets (this computer only)

def _secrets_file():
    return os.path.join(data_dir(), "secrets.json")


def save_secret(key, value):
    if IS_MAC:
        # -U updates an existing item. The login Keychain encrypts it for this macOS user.
        subprocess.run(["security", "add-generic-password", "-U", "-s", APP, "-a", key, "-w", value],
                       check=True, capture_output=True)
        return
    blob = _dpapi(value.encode("utf-8"), True) if IS_WIN else value.encode("utf-8")
    s = _read_secrets()
    s[key] = base64.b64encode(blob).decode()
    os.makedirs(data_dir(), exist_ok=True)
    tmp = _secrets_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f)
    if not IS_WIN:
        os.chmod(tmp, 0o600)
    os.replace(tmp, _secrets_file())


def get_secret(key):
    if IS_MAC:
        r = subprocess.run(["security", "find-generic-password", "-s", APP, "-a", key, "-w"],
                           capture_output=True, text=True)
        return r.stdout.rstrip("\n") if r.returncode == 0 else None
    v = _read_secrets().get(key)
    if not v:
        return None
    blob = base64.b64decode(v)
    return (_dpapi(blob, False) if IS_WIN else blob).decode("utf-8")


def _read_secrets():
    try:
        with open(_secrets_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _dpapi(data, protect):
    import ctypes
    import ctypes.wintypes as wt

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt = ctypes.windll.crypt32
    src = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not protect/unprotect the password")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ------------------------------------------------------------------ synced SharePoint libraries

_libs = {"at": 0, "list": []}
EXTRA_LIBRARIES = []   # set from settings: extra folders to treat as synced libraries


def lib_key(name):
    """'OneDrive - Milestone Systems' (Windows) and 'OneDrive-MilestoneSystems' (Mac) are the same library."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def sync_libraries():
    """[(name, path)] of synced libraries, longest path first (so nested shortcuts win)."""
    if time.time() - _libs["at"] < 300 and _libs["list"]:
        return _libs["list"]
    found = []
    if IS_WIN:
        try:
            import winreg
            base = r"Software\SyncEngines\Providers\OneDrive"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(k, sub) as sk:
                            found.append(winreg.QueryValueEx(sk, "MountPoint")[0])
                    except OSError:
                        pass
        except OSError:
            pass
        for env in ("OneDriveCommercial", "OneDrive"):
            if os.environ.get(env):
                found.append(os.environ[env])
    elif IS_MAC:
        cs = os.path.expanduser("~/Library/CloudStorage")
        try:
            for e in os.listdir(cs):
                p = os.path.join(cs, e)
                if not e.startswith("OneDrive") or not os.path.isdir(p):
                    continue
                if e.startswith("OneDrive-SharedLibraries"):
                    found += [os.path.join(p, x) for x in os.listdir(p) if os.path.isdir(os.path.join(p, x))]
                else:
                    found.append(p)
        except OSError:
            pass
    # libraries added to "My files" as shortcuts appear as "<Site> - <Library>" inside the personal OneDrive
    for p in list(found):
        if lib_key(os.path.basename(p)).startswith("onedrive"):
            try:
                found += [os.path.join(p, x) for x in os.listdir(p)
                          if " - " in x and os.path.isdir(os.path.join(p, x))]
            except OSError:
                pass
    found += [p for p in EXTRA_LIBRARIES if p]
    seen, out = set(), []
    for p in found:
        p = os.path.abspath(p.rstrip("\\/"))
        if os.path.normcase(p) not in seen and os.path.isdir(p):
            seen.add(os.path.normcase(p))
            out.append((os.path.basename(p), p))
    out.sort(key=lambda x: -len(x[1]))
    _libs.update(at=time.time(), list=out)
    return out


def library_root(name):
    k = lib_key(name)
    return next((p for n, p in sync_libraries() if lib_key(n) == k), None)


# ------------------------------------------------------------------ Claude Code

def claude_candidates():
    if IS_WIN:
        return [shutil.which("claude.exe") or "", shutil.which("claude.cmd") or "",
                os.path.expandvars(r"%USERPROFILE%\.local\bin\claude.exe"),
                os.path.expandvars(r"%APPDATA%\npm\claude.cmd")]
    home = os.path.expanduser("~")
    return [shutil.which("claude") or "", f"{home}/.local/bin/claude", f"{home}/.claude/local/claude",
            "/opt/homebrew/bin/claude", "/usr/local/bin/claude", f"{home}/.npm-global/bin/claude"]
