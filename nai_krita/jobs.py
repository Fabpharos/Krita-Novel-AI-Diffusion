"""Job queue: runs NovelAI requests on worker threads, one at a time.

Qt only (no Krita). Results come back to the UI thread by signal; the UI thread
does all document work.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .qt import QObject, QThread, QTimer, pyqtSignal
from . import api, cost, request, vibes

log = logging.getLogger("nai_krita")

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
QUEUE_GAP_MS = 1500


@dataclass
class Job:
    params: request.GenerationParams      # seeded
    verdict: cost.Verdict
    target: object                         # panels.Target: rect and where to place it
    label: str = ""
    entry: object = None                   # UI history entry this job belongs to
    auto_apply: bool = False               # place the first image straight away (panel fill)
    vibe_sources: list = field(default_factory=list)  # [(png, info_extracted, cache_key, strength, name)]
    upscale_png: bytes = b""                # set: this job is a 2x upscale of that image
    vibe_cache_dir: str = ""
    status: str = QUEUED
    images: List[bytes] = field(default_factory=list)
    error: str = ""
    balance_before: Optional[cost.Balance] = None
    balance_after: Optional[cost.Balance] = None
    saved_paths: List[str] = field(default_factory=list)

    @property
    def spent(self) -> Optional[int]:
        if self.balance_before and self.balance_after:
            return self.balance_before.total - self.balance_after.total
        return None


class _Worker(QThread):
    finished_job = pyqtSignal(object)

    def __init__(self, job: Job, token: str, timeout: int, autosave_dir: str):
        super().__init__()
        self.job, self._token, self._timeout, self._autosave = job, token, timeout, autosave_dir

    def run(self):
        job = self.job
        try:
            client = api.Client(self._token, self._timeout)
            job.balance_before = _try_balance(client)
            if job.upscale_png:
                job.images = client.upscale(job.upscale_png)
            else:
                _encode_vibes(client, job)
                job.images = client.generate(request.build_body(job.params))
            job.balance_after = _try_balance(client)
            if self._autosave:
                job.saved_paths = autosave(self._autosave, job)
            job.status = DONE
        except api.NaiError as e:
            job.status, job.error = FAILED, str(e)
        except Exception as e:  # keep the queue alive; show the message
            log.exception("job failed")
            job.status, job.error = FAILED, f"{type(e).__name__}: {e}"
        self.finished_job.emit(job)


def _encode_vibes(client, job: Job):
    """Encode the job's new vibe sources (cache first) and add them to its params.
    Runs on the worker thread before the generation request."""
    if not job.vibe_sources:
        return
    cache = vibes.VibeCache(job.vibe_cache_dir) if job.vibe_cache_dir else None
    for png, ie, key, strength, name in job.vibe_sources:
        enc = cache.get(key) if cache else None
        if enc is None:
            enc = client.encode_vibe(png, ie, job.params.model)
            if cache:
                cache.put(key, enc)
        job.params.vibes.append(request.VibeRef(enc, strength, name))
    job.vibe_sources = []


class _BalanceWorker(QThread):
    result = pyqtSignal(object, str)   # Balance or None, error text

    def __init__(self, token: str, timeout: int):
        super().__init__()
        self._token, self._timeout = token, timeout

    def run(self):
        try:
            client = api.Client(self._token, self._timeout)
            self.result.emit(cost.parse_balance(client.subscription()), "")
        except Exception as e:
            self.result.emit(None, str(e))


def _try_balance(client) -> Optional[cost.Balance]:
    try:
        return cost.parse_balance(client.subscription())
    except Exception as e:
        log.warning("balance fetch failed: %s", e)
        return None


def autosave(folder: str, job: Job) -> List[str]:
    """Write raw PNGs exactly as NovelAI sent them (metadata intact)."""
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    paths = []
    for i, png in enumerate(job.images):
        path = os.path.join(folder, f"nai_{stamp}_{job.params.seed}_{i}.png")
        with open(path, "wb") as f:
            f.write(png)
        paths.append(path)
    return paths


class JobQueue(QObject):
    job_started = pyqtSignal(object)
    job_finished = pyqtSignal(object)      # DONE or FAILED
    jobs_cancelled = pyqtSignal(list)      # jobs dropped before sending
    queue_changed = pyqtSignal(int)        # jobs waiting + running
    balance_changed = pyqtSignal(object)   # Balance
    balance_failed = pyqtSignal(str)

    def __init__(self, get_settings):
        super().__init__()
        self._get_settings = get_settings
        self._pending: List[Job] = []
        self._worker: Optional[_Worker] = None
        self._balance_worker: Optional[_BalanceWorker] = None
        self.balance: Optional[cost.Balance] = None

    # ---- balance ----
    def refresh_balance(self):
        s = self._get_settings()
        if not s.token or (self._balance_worker and self._balance_worker.isRunning()):
            return
        self._balance_worker = _BalanceWorker(s.token, s.timeout)
        self._balance_worker.result.connect(self._on_balance)
        self._balance_worker.start()

    def _on_balance(self, balance, error):
        if balance is None:
            self.balance_failed.emit(error)
            return
        self.balance = balance
        self.balance_changed.emit(balance)

    # ---- queue ----
    @property
    def busy(self) -> bool:
        return self._worker is not None

    def count(self) -> int:
        return len(self._pending) + (1 if self._worker else 0)

    def submit(self, jobs: List[Job]):
        self._pending.extend(jobs)
        self.queue_changed.emit(self.count())
        if not self._worker:
            self._next()

    def cancel(self):
        """Drop every job not yet sent. A request already in flight still finishes."""
        dropped, self._pending = self._pending, []
        for job in dropped:
            job.status = CANCELLED
        if dropped:
            self.jobs_cancelled.emit(dropped)
        self.queue_changed.emit(self.count())

    def _next(self):
        if self._worker or not self._pending:
            return
        job = self._pending.pop(0)
        job.status = RUNNING
        s = self._get_settings()
        self._worker = _Worker(job, s.token, s.timeout, s.autosave_dir)
        self._worker.finished_job.connect(self._on_finished)
        self.job_started.emit(job)
        self._worker.start()

    def _on_finished(self, job: Job):
        worker, self._worker = self._worker, None
        if worker:
            worker.wait()
        if job.balance_after:
            self.balance = job.balance_after
            self.balance_changed.emit(job.balance_after)
        if job.status == DONE and job.verdict.free and (job.spent or 0) > 0:
            # The free test was wrong. Log the parameters (no token, no images).
            log.warning("Request judged free cost %s Anlas: %s", job.spent,
                        json.dumps(request.redact(request.build_body(job.params))))
        self.job_finished.emit(job)
        self.queue_changed.emit(self.count())
        if self._pending:
            QTimer.singleShot(QUEUE_GAP_MS, self._next)
