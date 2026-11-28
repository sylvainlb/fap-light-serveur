"""Service-local storage (F5.3, F6.3): async jobs and counters.

No table on the data server (3.0): local service state, persisted as
JSON in AI_STATE_DIR (volume provisioned by the deployment), exposed
read-only on the service API (jobs: requester only; counters: admins).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid


class StateStore:
    def __init__(self, state_dir: str, budget_max_runs: int = 0, budget_window_days: int = 30,
                 retention_days: int = 7):
        self.dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs_path = os.path.join(state_dir, "jobs.json")
        self._counters_path = os.path.join(state_dir, "counters.json")
        self.budget_max_runs = budget_max_runs      # 0 = unlimited (F6.3)
        self.budget_window_days = budget_window_days
        self.retention_days = retention_days         # job purge (annex C)
        self._jobs = self._load(self._jobs_path)
        self._counters = self._load(self._counters_path)

    @staticmethod
    def _load(path: str) -> dict:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {}

    def _save(self, path: str, data: dict) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    # ---------------------------------------------------------------- jobs
    def create_job(self, component: str, source: str, requester: str, is_test: bool) -> dict:
        with self._lock:
            job = {
                "job": uuid.uuid4().hex,
                "component": component,
                "source": source,
                "requester": requester,
                "state": "queued",
                "isTest": is_test,
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "finished": None,
                "written": None,
                "error": None,
            }
            self._jobs[job["job"]] = job
            self._save(self._jobs_path, self._jobs)
            return dict(job)

    def update_job(self, job_id: str, **fields) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            if fields.get("state") in ("succeeded", "failed"):
                job["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save(self._jobs_path, self._jobs)
            return dict(job)

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def purge_jobs(self) -> int:
        cutoff = time.time() - self.retention_days * 86400
        with self._lock:
            stale = [jid for jid, j in self._jobs.items()
                     if j["state"] in ("succeeded", "failed")
                     and _parse_ts(j.get("finished")) and _parse_ts(j["finished"]) < cutoff]
            for jid in stale:
                del self._jobs[jid]
            if stale:
                self._save(self._jobs_path, self._jobs)
            return len(stale)

    # ------------------------------------------------------------ counters
    def incr_counter(self, component: str, model: str, is_test: bool) -> dict:
        with self._lock:
            key = f"{component}"
            c = self._counters.setdefault(key, {"runs": 0, "tests": 0, "byModel": {}, "windowStart": time.time()})
            now = time.time()
            if now - c["windowStart"] > self.budget_window_days * 86400:
                c.update({"runs": 0, "tests": 0, "byModel": {}, "windowStart": now})
            c["runs"] += 0 if is_test else 1
            c["tests"] += 1 if is_test else 0
            c["byModel"][model] = c["byModel"].get(model, 0) + 1
            self._save(self._counters_path, self._counters)
            return {"runs": c["runs"], "tests": c["tests"], "byModel": dict(c["byModel"])}

    def budget_exceeded(self, component: str) -> bool:
        if self.budget_max_runs <= 0:
            return False
        with self._lock:
            c = self._counters.get(component)
            if not c:
                return False
            now = time.time()
            if now - c["windowStart"] > self.budget_window_days * 86400:
                return False
            return c["runs"] >= self.budget_max_runs

    def counters(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._counters))


def _parse_ts(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
