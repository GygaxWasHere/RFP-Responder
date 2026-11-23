"""Start RFP Responder: local server + an app window (Edge/Chrome app mode, or the default browser).

Staying connected through long breaks:
  * The server lives as long as the app window's browser process. Idle time, a locked screen or
    sleep do not stop it.
  * If the window cannot be watched (browser handed off to an already running instance), the
    server waits for pings, ignores the time the PC was asleep, and only stops after
    IDLE_EXIT_MIN minutes without any sign of the window (and never while a job runs).
  * Port and token are kept in session.json. Starting the app again while it runs just opens a
    window on the running server; after a restart (e.g. the PC was shut down) the server comes
    back on the same address, so a window left open reconnects by itself.
"""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from . import config, osdep, server

IDLE_EXIT_MIN = 45
SESSION_FILE = os.path.join(config.BASE_DIR, "session.json")


find_browser = osdep.find_browser


def _load_session():
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            s = json.load(f)
        return int(s["port"]), str(s["token"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0, ""


def _save_session(port, token):
    os.makedirs(config.BASE_DIR, exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid()}, f)
    os.replace(tmp, SESSION_FILE)


def _running(port, token):
    """True when an RFP Responder server with this token answers on the port."""
    if not port or not token:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping", data=b"{}", method="POST",
                                     headers={"X-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def open_window(url):
    exe = None if "--browser" in sys.argv else find_browser()
    if exe:
        profile = os.path.join(config.BASE_DIR, "window")
        return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1500,950",
                                 "--no-first-run", "--disable-features=Translate"])
    webbrowser.open(url)
    return None


def main():
    port, token = _load_session()
    if _running(port, token):  # already running: just show it
        open_window(f"http://127.0.0.1:{port}/?t={token}")
        return
    token = token or secrets.token_urlsafe(24)
    server.TOKEN = token
    want = int(os.environ.get("RFP_RESPONDER_PORT", "0")) or port
    try:
        httpd = server.serve(want)
    except OSError:  # the old port is taken by something else
        httpd = server.serve(0)
    port = httpd.server_address[1]
    _save_session(port, token)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"{config.APP_NAME} {config.APP_VERSION} running at {url}")
    started = time.time()
    proc = open_window(url)
    server.LAST_PING[0] = time.time()
    last_tick = time.time()
    try:
        while True:
            time.sleep(5)
            now = time.time()
            if now - last_tick > 60:
                # the PC slept (or the process was frozen): count that time as "window still there"
                server.LAST_PING[0] = now
            last_tick = now
            if server.jobs.active():
                continue
            if proc is not None and proc.poll() is None:
                continue  # the app window is open
            if proc is not None and proc.poll() is not None and now - started < 20:
                proc = None  # the browser handed the window to a running instance: fall back to pings
            if proc is not None:
                # the window was closed; give a reopened/second window a moment to ping
                if now - server.LAST_PING[0] > 60:
                    break
                continue
            if now - server.LAST_PING[0] > IDLE_EXIT_MIN * 60:
                break
    except KeyboardInterrupt:
        pass
    httpd.shutdown()


if __name__ == "__main__":
    main()
