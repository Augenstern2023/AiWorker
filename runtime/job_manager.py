# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from agents.storage_agent import StorageAgent
from core.events import WorkerEvent, parse_event_line
from core.job import CrawlJob
from runtime.job_state import JobState, TERMINAL_STATUSES
from runtime.worker_launcher import launch_worker


logger = logging.getLogger("aiworker.web.jobs")


class JobConflictError(RuntimeError):
    pass


class JobNotFoundError(KeyError):
    pass


class JobManager:
    def __init__(self, runtime_dir: str | Path = ".runtime", cwd: str | Path | None = None):
        self.runtime_dir = Path(runtime_dir)
        self.jobs_dir = self.runtime_dir / "jobs"
        self.logs_dir = self.runtime_dir / "logs"
        self.state_dir = self.runtime_dir / "state"
        self.cwd = Path(cwd) if cwd else None
        self.jobs: dict[str, JobState] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()
        self.storage = StorageAgent(self.runtime_dir / "agent_state.db")
        for path in (self.jobs_dir, self.logs_dir, self.state_dir):
            path.mkdir(parents=True, exist_ok=True)

    def generate_job_id(self, platform: str) -> str:
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{platform}-{int(time.time() * 1000) % 100000}"

    def create_job(self, data: dict[str, Any] | CrawlJob) -> JobState:
        if isinstance(data, CrawlJob):
            job = data
        else:
            payload = dict(data)
            payload.setdefault("job_id", self.generate_job_id(str(payload.get("platform", "job"))))
            job = CrawlJob.from_dict(payload)
        return self.start_job(job)

    def start_job(self, job: CrawlJob) -> JobState:
        with self.lock:
            self._load_states_from_disk()
            self._reconcile_orphan_jobs()
            for state in self.jobs.values():
                if state.platform == job.platform and state.status in {"pending", "running", "stopping"}:
                    raise JobConflictError(f"platform {job.platform!r} already has a running job")

            state = JobState(job=job)
            self.jobs[job.job_id] = state
            job_file = self.jobs_dir / f"{job.job_id}.json"
            job.save(job_file)
            self._save_state(state)
            self.storage.upsert_job(state)

            process = launch_worker(job_file, cwd=self.cwd, platform=job.platform, crawler_type=job.crawler_type)
            state.pid = process.pid
            state.status = "running"
            state.started_at = time.time()
            self.processes[job.job_id] = process
            self._save_state(state)
            self.storage.upsert_job(state)

            thread = threading.Thread(
                target=self._monitor_process,
                args=(state, process),
                daemon=True,
            )
            thread.start()
            return state

    def _monitor_process(self, state: JobState, process: subprocess.Popen) -> None:
        try:
            if process.stdout:
                for line in process.stdout:
                    event = parse_event_line(line, job_id=state.job_id, platform=state.platform)
                    self._record_event(state.job_id, event)
            return_code = process.wait()
        except Exception as exc:
            return_code = -1
            event = WorkerEvent(
                event="job_failed",
                job_id=state.job_id,
                worker_id=f"{state.platform}-worker",
                platform=state.platform,
                level="error",
                message=f"worker monitor failed: {exc}",
            )
            self._record_event(state.job_id, event)

        with self.lock:
            current = self.jobs[state.job_id]
            current.return_code = return_code
            if current.status == "stopping":
                current.status = "cancelled"
                current.finished_at = time.time()
            elif current.status not in TERMINAL_STATUSES:
                current.status = "succeeded" if return_code == 0 else "failed"
                current.finished_at = time.time()
            self.processes.pop(state.job_id, None)
            self._scan_artifacts(current)
            self._save_state(current)
            self.storage.upsert_job(current)

    def _record_event(self, job_id: str, event: WorkerEvent) -> None:
        with self.lock:
            state = self.jobs.get(job_id)
            if not state:
                return
            state.add_event(event)
            self._save_state(state)
            self.storage.record_event(event)
            if event.event == "artifact":
                artifact_path = event.payload.get("path")
                artifact_type = event.payload.get("artifact_type", "file")
                if artifact_path:
                    self.storage.record_artifact(job_id, artifact_type, artifact_path, event.payload)
            if event.event in {"artifact", "job_finished", "job_failed"}:
                level = getattr(logging, event.level.upper(), logging.INFO)
                logger.log(
                    level if isinstance(level, int) else logging.INFO,
                    "任务事件：job_id=%s platform=%s event=%s message=%s payload=%s",
                    job_id,
                    event.platform,
                    event.event,
                    event.message,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                )

    def _scan_artifacts(self, state: JobState) -> None:
        job_dir = Path("data") / state.platform / "jobs" / state.job_id
        if self.cwd:
            job_dir = self.cwd / job_dir
        if not job_dir.exists():
            return
        artifacts = []
        for path in job_dir.rglob("*"):
            if not path.is_file():
                continue
            rel_path = path.relative_to(self.cwd) if self.cwd and path.is_relative_to(self.cwd) else path
            artifact = {
                "path": str(rel_path),
                "artifact_type": path.suffix.lstrip(".") or "file",
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
            }
            artifacts.append(artifact)
            self.storage.record_artifact(state.job_id, artifact["artifact_type"], artifact["path"], artifact)
        state.artifacts = artifacts

    def _save_state(self, state: JobState) -> None:
        target = self.state_dir / f"{state.job_id}.json"
        with target.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    def _state_from_disk(self, job_id: str) -> JobState | None:
        state_path = self.state_dir / f"{job_id}.json"
        job_path = self.jobs_dir / f"{job_id}.json"
        if not state_path.exists() and not job_path.exists():
            return None
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            job_data = state_data.get("job") if isinstance(state_data.get("job"), dict) else None
            if not job_data and job_path.exists():
                job_data = json.loads(job_path.read_text(encoding="utf-8"))
            if not isinstance(job_data, dict):
                return None
            state = JobState(job=CrawlJob.from_dict(job_data))
            state.status = str(state_data.get("status") or "failed")
            state.pid = state_data.get("pid")
            state.return_code = state_data.get("return_code")
            state.created_at = float(state_data.get("created_at") or state.created_at)
            state.started_at = state_data.get("started_at")
            state.finished_at = state_data.get("finished_at")
            state.message = str(state_data.get("message") or "")
            state.progress = state_data.get("progress") if isinstance(state_data.get("progress"), dict) else {}
            state.events = state_data.get("events") if isinstance(state_data.get("events"), list) else []
            state.artifacts = state_data.get("artifacts") if isinstance(state_data.get("artifacts"), list) else []
            self._reconcile_orphan_job(state)
            return state
        except (OSError, ValueError, TypeError):
            return None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            self._load_states_from_disk()
            self._reconcile_orphan_jobs()
            return [
                self._public_state(state)
                for state in sorted(self.jobs.values(), key=lambda item: item.created_at, reverse=True)
            ]

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            self._reconcile_orphan_job(state)
            return self._public_state(state, include_events=True)

    def get_logs(self, job_id: str) -> list[str]:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            self._reconcile_orphan_job(state)
            return [self._format_event(event) for event in state.events]

    def _load_states_from_disk(self) -> None:
        for state_path in self.state_dir.glob("*.json"):
            job_id = state_path.stem
            if job_id in self.jobs:
                continue
            state = self._state_from_disk(job_id)
            if state:
                self.jobs[job_id] = state

    def _tracked_process_is_alive(self, job_id: str) -> bool:
        process = self.processes.get(job_id)
        return bool(process and process.poll() is None)

    def _reconcile_orphan_jobs(self) -> None:
        for state in list(self.jobs.values()):
            self._reconcile_orphan_job(state)

    def _reconcile_orphan_job(self, state: JobState) -> None:
        if state.status not in {"pending", "running", "stopping"}:
            return
        if self._tracked_process_is_alive(state.job_id):
            return
        state.status = "cancelled" if state.status == "stopping" else "failed"
        state.return_code = state.return_code if state.return_code is not None else -1
        state.finished_at = state.finished_at or time.time()
        state.message = state.message or "Recovered orphaned job without a live worker process"
        self.processes.pop(state.job_id, None)
        self._scan_artifacts(state)
        self._save_state(state)
        self.storage.upsert_job(state)

    def stop(self, job_id: str) -> bool:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            self._pause_marker(job_id).unlink(missing_ok=True)
            process = self.processes.get(job_id)
            if not process or process.poll() is not None:
                self._reconcile_orphan_job(state)
                return False
            state.status = "stopping"
            self._save_state(state)
            self.storage.upsert_job(state)

        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return True

    def pause(self, job_id: str) -> bool:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            process = self.processes.get(job_id)
            if not process or process.poll() is not None:
                return False
            marker = self._pause_marker(job_id)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
            return True

    def resume(self, job_id: str) -> bool:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            marker = self._pause_marker(job_id)
            if not marker.exists():
                return False
            marker.unlink(missing_ok=True)
            return True

    def retry(self, job_id: str) -> JobState:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            self._reconcile_orphan_job(state)
            if state.status not in TERMINAL_STATUSES:
                raise JobConflictError("only terminal jobs can be retried")
            payload = state.job.to_dict()
            payload["job_id"] = self.generate_job_id(state.platform)
            extra = dict(payload.get("extra") or {})
            extra["retry_of_job_id"] = job_id
            payload["extra"] = extra
        return self.create_job(payload)

    def _pause_marker(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.pause"

    def stop_latest_running(self) -> bool:
        with self.lock:
            self._load_states_from_disk()
            self._reconcile_orphan_jobs()
            running = [
                state for state in self.jobs.values()
                if state.status in {"running", "pending", "stopping"}
            ]
            if not running:
                return False
            latest = max(running, key=lambda item: item.created_at)
        return self.stop(latest.job_id)

    def delete_job(self, job_id: str, force_missing: bool = False) -> None:
        with self.lock:
            state = self.jobs.get(job_id) or self._state_from_disk(job_id)
            if not state:
                if force_missing:
                    self._delete_job_files(job_id)
                    self._delete_storage_rows(job_id)
                    return
                raise JobNotFoundError(job_id)
            self.jobs[job_id] = state
            self._reconcile_orphan_job(state)
            if state.status not in TERMINAL_STATUSES:
                raise JobConflictError("running jobs cannot be deleted; stop the job first")
            process = self.processes.get(job_id)
            if process and process.poll() is None:
                raise JobConflictError("running jobs cannot be deleted; stop the job first")

            self.jobs.pop(job_id, None)
            self.processes.pop(job_id, None)
            self._pause_marker(job_id).unlink(missing_ok=True)

            self._delete_job_files(job_id)
            self._delete_storage_rows(job_id)

    def _delete_job_files(self, job_id: str) -> None:
        for path in (
            self.jobs_dir / f"{job_id}.json",
            self.state_dir / f"{job_id}.json",
            self.logs_dir / f"{job_id}.jsonl",
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _delete_storage_rows(self, job_id: str) -> None:
        db_path = self.runtime_dir / "agent_state.db"
        if not db_path.exists():
            return
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            for table in ("agent_artifacts", "agent_events", "agent_jobs"):
                conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
            conn.commit()

    def latest_job_id(self) -> str:
        with self.lock:
            if not self.jobs:
                return ""
            return max(self.jobs.values(), key=lambda item: item.created_at).job_id

    def has_running_jobs(self) -> bool:
        with self.lock:
            self._load_states_from_disk()
            self._reconcile_orphan_jobs()
            return any(state.status in {"pending", "running", "stopping"} for state in self.jobs.values())

    def _public_state(self, state: JobState, include_events: bool = False) -> dict[str, Any]:
        data = {
            "job_id": state.job_id,
            "platform": state.platform,
            "crawler_type": state.job.crawler_type,
            "status": state.status,
            "pid": state.pid,
            "return_code": state.return_code,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "message": state.message,
            "progress": state.progress,
            "paused": self._pause_marker(state.job_id).exists(),
            "artifacts": state.artifacts,
            "job": state.job.to_dict(),
        }
        if include_events:
            data["events"] = state.events
        return data

    def _format_event(self, event: dict[str, Any]) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(float(event.get("ts") or time.time())))
        level = str(event.get("level") or "info").upper()
        message = str(event.get("message") or "")
        return f"[{ts}] [{level}] {message}"
