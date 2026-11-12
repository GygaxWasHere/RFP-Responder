"""Background jobs with progress, log and cancel, polled by the UI."""
import threading
import time
import traceback
import uuid

_jobs = {}
_lock = threading.Lock()


class Job:
    def __init__(self, title):
        self.id = uuid.uuid4().hex[:10]
        self.title = title
        self.status = "running"
        self.progress = 0.0
        self.message = ""
        self.lines = []
        self.result = None
        self.error = None
        self.cancelled = False
        self.started = time.time()

    def update(self, progress=None, message=None):
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def log(self, line):
        self.lines.append(time.strftime("%H:%M:%S ") + str(line))
        del self.lines[:-400]

    def to_dict(self):
        return {"id": self.id, "title": self.title, "status": self.status, "progress": self.progress,
                "message": self.message, "log": self.lines[-200:], "result": self.result, "error": self.error,
                "elapsed": round(time.time() - self.started)}


def start(title, fn, *args, **kw):
    job = Job(title)
    with _lock:
        _jobs[job.id] = job

    def run():
        try:
            job.result = fn(*args, job=job, **kw)
            job.status = "cancelled" if job.cancelled else "done"
            job.progress = 1.0 if not job.cancelled else job.progress
        except Exception as e:  # noqa: BLE001 - reported to the UI
            job.status = "error"
            job.error = str(e)
            job.log(traceback.format_exc(limit=3))
    threading.Thread(target=run, daemon=True).start()
    return job


def get(jid):
    return _jobs.get(jid)


def active():
    return [j.to_dict() for j in _jobs.values() if j.status == "running"]
