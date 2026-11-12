"""Start RFP Responder: local server + an app window (Edge/Chrome app mode, or the default browser)."""
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from . import config, server


def find_browser():
    cands = [shutil.which("msedge"),
             os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
             os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
             shutil.which("chrome"),
             os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def main():
    port = int(os.environ.get("RFP_RESPONDER_PORT", "0"))
    httpd = server.serve(port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={server.TOKEN}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"{config.APP_NAME} {config.APP_VERSION} running at {url}")
    exe = None if "--browser" in sys.argv else find_browser()
    if exe:
        profile = os.path.join(config.BASE_DIR, "window")
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1500,950",
                          "--no-first-run", "--disable-features=Translate"])
    else:
        webbrowser.open(url)
    # Stay alive while the window pings; exit three minutes after it is closed (unless jobs still run).
    server.LAST_PING[0] = time.time()
    try:
        while True:
            time.sleep(5)
            if time.time() - server.LAST_PING[0] > 180 and not server.jobs.active():
                break
    except KeyboardInterrupt:
        pass
    httpd.shutdown()


if __name__ == "__main__":
    main()
