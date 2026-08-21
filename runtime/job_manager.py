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
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agents.storage_agent import OperationScopeConflictError, StorageAgent
from core.events import WorkerEvent, parse_event_line
from core.job import CrawlJob
from runtime.job_state import JobState, TERMINAL_STATUSES
from runtime.worker_launcher import launch_worker


logger = logging.getLogger("aiworker.web.jobs")

_CONCURRENT_DRAMA_DOWNLOAD_TYPES = frozenset(
    {"drama_download", "douyin_fallback_analysis", "drama_reviews"}
)
_CONCURRENT_BREAKDOWN_TYPES = frozenset({"breakdown_generate"})
_CONCURRENT_CREATIVE_GENERATION_TYPES = frozenset({"phase4_generate"})
_CONCURRENT_SCREENPLAY_REVIEW_TYPES = frozenset({"screenplay_review"})
_CONCURRENT_SCREENPLAY_OPTIMIZATION_TYPES = frozenset({"screenplay_optimization"})
_PENDING_LAUNCH_GRACE_SECONDS = 30.0
_LAUNCH_LEASE_SECONDS = 30.0


class JobConflictError(RuntimeError):
    """A durable Job reservation conflict with machine-readable cause data."""

    def __init__(
        self,
        message: str,
        *,
        kind: str | None = None,
        conflicting_operation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.conflicting_operation_id = conflicting_operation_id


class JobNotFoundError(KeyError):
    pass


class WorkerTerminationError(RuntimeError):
    pass


def _operation_request(job: CrawlJob) -> str:
    payload = job.to_dict()
    payload.pop("job_id", None)
    extra = dict(payload.get("extra") or {})
    extra.pop("retry_failed_operation", None)
    extra.pop("retry_of_job_id", None)
    payload["extra"] = extra
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def creative_job_scope(
    job: CrawlJob,
) -> tuple[str, str, tuple[int, int] | None] | None:
    """Return normalized project/stage/episode scope for a creative job."""

    if job.crawler_type != "phase4_generate":
        return None
    extra = job.extra or {}
    project_id = str(extra.get("project_id") or "").strip()
    raw_action = str(extra.get("action") or "").strip()
    stage = {
        "blueprints": "brief",
        "blueprint_variant": "brief",
        "characters": "characters",
        "outlines": "outline",
        "outline_overview": "outline",
        "beats": "beats",
        "script": "script",
    }.get(raw_action)
    if not project_id or stage is None:
        return None
    if stage in {"brief", "characters", "outline"}:
        return project_id, stage, None
    if bool(extra.get("series")):
        start = extra.get("episode_start")
        end = extra.get("episode_end")
        if start is None or end is None:
            raise ValueError("creative series job requires an episode range")
    else:
        episode = extra.get("episode_number")
        start = extra.get("episode_start", episode)
        end = extra.get("episode_end", episode)
    if start is None or end is None:
        return None
    start_number, end_number = int(start), int(end)
    if start_number < 1 or end_number < start_number:
        raise ValueError("creative job episode range is invalid")
    return project_id, stage, (start_number, end_number)


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

    def create_job(
        self,
        data: dict[str, Any] | CrawlJob,
        *,
        created_by_user_id: str | None = None,
        workflow_task_id: str | None = None,
        project_id: str | int | None = None,
    ) -> JobState:
        if isinstance(data, CrawlJob):
            job = data
        else:
            payload = dict(data)
            payload.setdefault("job_id", self.generate_job_id(str(payload.get("platform", "job"))))
            job = CrawlJob.from_dict(payload)
        ownership = {
            "created_by_user_id": created_by_user_id,
            "workflow_task_id": workflow_task_id,
            "project_id": str(project_id) if project_id is not None else None,
        }
        if any(value is not None for value in ownership.values()):
            job.extra = dict(job.extra)
            for field, value in ownership.items():
                if value is not None:
                    job.extra[field] = value
        return self.start_job(job)

    def get_or_create_job(
        self,
        operation_id: str,
        data: dict[str, Any],
        *,
        created_by_user_id: str,
        workflow_task_id: str,
        project_id: str | int,
    ) -> JobState:
        """Reserve operation_id transactionally so only one worker can launch."""

        payload = dict(data)
        payload.setdefault(
            "job_id", self.generate_job_id(str(payload.get("platform", "job")))
        )
        job = CrawlJob.from_dict(payload)
        job.extra = dict(job.extra)
        job.extra.update(
            {
                "created_by_user_id": created_by_user_id,
                "workflow_task_id": workflow_task_id,
                "project_id": str(project_id),
                "operation_id": operation_id,
            }
        )
        scope = creative_job_scope(job)
        proposed = JobState(job=job)
        launcher_owner = f"job-launcher-{uuid.uuid4().hex}"
        try:
            reserved, stored = self.storage.reserve_job_operation(
                operation_id,
                proposed,
                scope=scope,
                launcher_owner=launcher_owner,
                launcher_lease_expires_at=time.time() + _LAUNCH_LEASE_SECONDS,
            )
        except OperationScopeConflictError as exc:
            raise JobConflictError(
                f"creative scope is already reserved by job {exc}",
                kind="creative_scope_collision",
                conflicting_operation_id=exc.operation_id,
            ) from exc
        if not reserved:
            if _operation_request(stored.job) != _operation_request(job):
                raise JobConflictError(
                    "operation_id is already reserved for a different payload",
                    kind="operation_payload_mismatch",
                )
            claimed_prelaunch, prelaunch_state = (
                self.storage.claim_expired_prelaunch_operation(
                    operation_id,
                    launcher_owner=launcher_owner,
                    launcher_lease_expires_at=time.time()
                    + _LAUNCH_LEASE_SECONDS,
                )
            )
            if claimed_prelaunch and prelaunch_state is not None:
                return self._launch_reserved_operation(
                    operation_id, launcher_owner, prelaunch_state.job
                )
            if (
                stored.status == "failed"
                and bool(job.extra.get("retry_failed_operation"))
            ):
                try:
                    claimed, retry_state = self.storage.claim_failed_job_operation(
                        operation_id,
                        launcher_owner=launcher_owner,
                        launcher_lease_expires_at=time.time()
                        + _LAUNCH_LEASE_SECONDS,
                    )
                except OperationScopeConflictError as exc:
                    raise JobConflictError(
                        f"creative scope is already reserved by job {exc}",
                        kind="creative_scope_collision",
                        conflicting_operation_id=exc.operation_id,
                    ) from exc
                if claimed and retry_state is not None:
                    return self._launch_reserved_operation(
                        operation_id, launcher_owner, retry_state.job
                    )
                refreshed = self.storage.get_job_by_operation(operation_id)
                if refreshed is not None:
                    stored = refreshed
            with self.lock:
                current = self.jobs.get(stored.job_id)
                if current is None:
                    current = self._state_from_disk(stored.job_id) or stored
                    self.jobs[stored.job_id] = current
                return current
        return self._launch_reserved_operation(operation_id, launcher_owner, job)

    def _launch_reserved_operation(
        self, operation_id: str, launcher_owner: str, job: CrawlJob
    ) -> JobState:
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()

        def renew() -> None:
            while not heartbeat_stop.wait(_LAUNCH_LEASE_SECONDS / 3):
                try:
                    renewed = self.storage.renew_job_launch_lease(
                        operation_id,
                        launcher_owner=launcher_owner,
                        lease_expires_at=time.time() + _LAUNCH_LEASE_SECONDS,
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            try:
                state = self.start_job(
                    job,
                    operation_id=operation_id,
                    launcher_owner=launcher_owner,
                    start_monitor=False,
                )
            except WorkerTerminationError:
                raise
            except Exception:
                self._record_owned_launch_failure(
                    job,
                    operation_id=operation_id,
                    launcher_owner=launcher_owner,
                )
                raise
            if lease_lost.is_set():
                self._discard_unmonitored_process(
                    job.job_id, persist_terminal=False
                )
                self._record_owned_launch_failure(
                    job,
                    operation_id=operation_id,
                    launcher_owner=launcher_owner,
                )
                raise JobConflictError("launcher lease was lost during worker start")
            try:
                finished = self.storage.finish_job_launch(
                    operation_id, launcher_owner=launcher_owner, phase="launched"
                )
            except Exception:
                self._start_monitor(state)
                raise
            if not finished:
                self._discard_unmonitored_process(
                    job.job_id, persist_terminal=False
                )
                self._record_owned_launch_failure(
                    job,
                    operation_id=operation_id,
                    launcher_owner=launcher_owner,
                )
                raise JobConflictError("launcher lease was lost during worker start")
            self._start_monitor(state)
            return state
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1.0)

    def _record_owned_launch_failure(
        self,
        job: CrawlJob,
        *,
        operation_id: str,
        launcher_owner: str,
    ) -> None:
        failed = JobState(job=job)
        failed.status = "failed"
        failed.finished_at = time.time()
        failed.message = "worker launch failed"
        persisted = self.storage.upsert_owned_job_launch(
            failed,
            operation_id,
            launcher_owner=launcher_owner,
        )
        if persisted:
            self.storage.finish_job_launch(
                operation_id, launcher_owner=launcher_owner, phase="failed"
            )
        with self.lock:
            if persisted:
                self.jobs[job.job_id] = failed
                self._save_state(failed)
            else:
                self.jobs.pop(job.job_id, None)
                self.processes.pop(job.job_id, None)

    def _discard_unmonitored_process(
        self, job_id: str, *, persist_terminal: bool = True
    ) -> None:
        with self.lock:
            process = self.processes.pop(job_id, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                with self.lock:
                    self.processes[job_id] = process
                    state = self.jobs.get(job_id)
                if state is not None:
                    self._start_monitor(
                        state, persist_terminal=persist_terminal
                    )
                raise WorkerTerminationError(
                    f"worker {job_id} did not exit after kill"
                ) from exc
        if process.poll() is None:
            with self.lock:
                self.processes[job_id] = process
                state = self.jobs.get(job_id)
            if state is not None:
                self._start_monitor(state, persist_terminal=persist_terminal)
            raise WorkerTerminationError(
                f"worker {job_id} is still running after termination"
            )

    def _start_monitor(
        self, state: JobState, *, persist_terminal: bool = True
    ) -> None:
        process = self.processes.get(state.job_id)
        if process is None:
            return
        thread = threading.Thread(
            target=self._monitor_process,
            args=(state, process, persist_terminal),
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            self._monitor_process(state, process, persist_terminal)

    def start_job(
        self,
        job: CrawlJob,
        *,
        operation_id: str | None = None,
        launcher_owner: str | None = None,
        start_monitor: bool = True,
    ) -> JobState:
        if bool(operation_id) != bool(launcher_owner):
            raise ValueError("operation launch identity requires both id and owner")

        def persist_launch_state(state: JobState) -> None:
            if operation_id and launcher_owner:
                if not self.storage.upsert_owned_job_launch(
                    state,
                    operation_id,
                    launcher_owner=launcher_owner,
                ):
                    raise JobConflictError(
                        "launcher lease was lost during worker start"
                    )
            else:
                self.storage.upsert_job(state)

        with self.lock:
            creative_job_scope(job)
            self._load_states_from_disk()
            self._reconcile_orphan_jobs()
            for state in self.jobs.values():
                same_operation = bool(
                    operation_id
                    and state.job_id == job.job_id
                    and str(state.job.extra.get("operation_id") or "").strip()
                    == operation_id
                )
                if same_operation:
                    continue
                if (
                    state.platform == job.platform
                    and state.status in {"pending", "running", "stopping"}
                    and not self._can_run_concurrently(state.job, job)
                ):
                    raise JobConflictError(f"platform {job.platform!r} already has a running job")

            state = JobState(job=job)
            self.jobs[job.job_id] = state
            job_file = self.jobs_dir / f"{job.job_id}.json"
            job.save(job_file)
            persist_launch_state(state)
            self._save_state(state)

            process = launch_worker(job_file, cwd=self.cwd, platform=job.platform, crawler_type=job.crawler_type)
            state.pid = process.pid
            state.status = "running"
            state.started_at = time.time()
            self.processes[job.job_id] = process
            try:
                persist_launch_state(state)
            except Exception:
                self._discard_unmonitored_process(
                    job.job_id, persist_terminal=False
                )
                raise
            try:
                self._save_state(state)
            except Exception:
                self._discard_unmonitored_process(job.job_id)
                raise

            if start_monitor:
                self._start_monitor(state)
            return state

    @staticmethod
    def _can_run_concurrently(existing: CrawlJob, incoming: CrawlJob) -> bool:
        job_types = {existing.crawler_type, incoming.crawler_type}
        existing_extra = existing.extra or {}
        incoming_extra = incoming.extra or {}
        if not job_types - _CONCURRENT_DRAMA_DOWNLOAD_TYPES:
            existing_identity = str(
                existing_extra.get("book_id") or existing_extra.get("title") or ""
            ).strip().casefold()
            incoming_identity = str(
                incoming_extra.get("book_id") or incoming_extra.get("title") or ""
            ).strip().casefold()
        elif not job_types - _CONCURRENT_BREAKDOWN_TYPES:
            existing_identity = str(existing_extra.get("topic_id") or "").strip()
            incoming_identity = str(incoming_extra.get("topic_id") or "").strip()
        elif not job_types - _CONCURRENT_CREATIVE_GENERATION_TYPES:
            existing_scope = creative_job_scope(existing)
            incoming_scope = creative_job_scope(incoming)
            if existing_scope is None or incoming_scope is None:
                return False
            if existing_scope[:2] != incoming_scope[:2]:
                return True
            existing_range, incoming_range = existing_scope[2], incoming_scope[2]
            if existing_range is None or incoming_range is None:
                return False
            return (
                existing_range[1] < incoming_range[0]
                or incoming_range[1] < existing_range[0]
            )
        elif not job_types - _CONCURRENT_SCREENPLAY_REVIEW_TYPES:
            existing_identity = str(existing_extra.get("review_id") or "").strip()
            incoming_identity = str(incoming_extra.get("review_id") or "").strip()
        elif not job_types - _CONCURRENT_SCREENPLAY_OPTIMIZATION_TYPES:
            existing_identity = str(existing_extra.get("optimization_id") or "").strip()
            incoming_identity = str(incoming_extra.get("optimization_id") or "").strip()
        else:
            return False
        return bool(existing_identity and incoming_identity and existing_identity != incoming_identity)

    def _monitor_process(
        self,
        state: JobState,
        process: subprocess.Popen,
        persist_terminal: bool = True,
    ) -> None:
        monitor_error: Exception | None = None
        if process.stdout:
            try:
                for line in process.stdout:
                    if not persist_terminal:
                        continue
                    try:
                        event = parse_event_line(
                            line, job_id=state.job_id, platform=state.platform
                        )
                        self._record_event(state.job_id, event)
                    except Exception as exc:
                        monitor_error = monitor_error or exc
            except Exception as exc:
                monitor_error = monitor_error or exc
        try:
            return_code = process.wait()
        except Exception as exc:
            monitor_error = monitor_error or exc
            return_code = process.poll()
            if return_code is None:
                logger.exception(
                    "Worker monitor lost process state while worker may still run: %s",
                    state.job_id,
                )
                return

        if monitor_error is not None:
            return_code = -1
            event = WorkerEvent(
                event="job_failed",
                job_id=state.job_id,
                worker_id=f"{state.platform}-worker",
                platform=state.platform,
                level="error",
                message=f"worker monitor failed: {monitor_error}",
            )
            try:
                self._record_event(state.job_id, event)
            except Exception:
                logger.exception(
                    "Failed to persist worker monitor error for job %s",
                    state.job_id,
                )

        if not persist_terminal:
            with self.lock:
                self.processes.pop(state.job_id, None)
                if self.jobs.get(state.job_id) is state:
                    self.jobs.pop(state.job_id, None)
            return

        with self.lock:
            current = self.jobs.get(state.job_id)
            if current is None:
                return
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
        payload = state.to_dict()
        payload["state_schema_version"] = 1
        with target.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

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
            if not state_data.get("state_schema_version"):
                extra = dict(job_data.get("extra") or {})
                if (
                    job_data.get("crawler_type") == "phase4_generate"
                    and "source_authority" not in extra
                    and "source_authority_sha256" not in extra
                ):
                    extra["legacy_authority_compat"] = True
                    job_data = {**job_data, "extra": extra}
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
            if state_path.exists() and state.status in TERMINAL_STATUSES:
                synchronized = self.storage.sync_terminal_job_from_disk(state)
                if not synchronized:
                    operation_id = str(
                        state.job.extra.get("operation_id") or ""
                    ).strip()
                    active = self.storage.get_job_by_operation(operation_id)
                    if active is not None:
                        return active
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

    def find_job_by_operation(self, operation_id: str) -> dict[str, Any] | None:
        """Return the durable job reserved for an operation, including DB-only rows."""

        stored = self.storage.get_job_by_operation(str(operation_id))
        if stored is None:
            return None
        with self.lock:
            state = (
                self.jobs.get(stored.job_id)
                or self._state_from_disk(stored.job_id)
                or stored
            )
            self.jobs[stored.job_id] = state
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

    def _process_id_is_alive(self, pid: int | None) -> bool:
        if not pid or int(pid) < 1:
            return False
        process_id = int(pid)
        if os.name == "nt":
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        try:
            os.kill(process_id, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _reconcile_orphan_jobs(self) -> None:
        for state in list(self.jobs.values()):
            self._reconcile_orphan_job(state)

    def _reconcile_orphan_job(self, state: JobState) -> None:
        if state.status not in {"pending", "running", "stopping"}:
            return
        operation_id = str(state.job.extra.get("operation_id") or "").strip()
        launch_lease = (
            self.storage.job_launch_lease(operation_id) if operation_id else None
        )
        if (
            state.status in {"pending", "running"}
            and launch_lease
            and launch_lease["phase"] == "launching"
            and float(launch_lease.get("expires_at") or 0) > time.time()
        ):
            return
        if state.status == "pending" and state.started_at is None:
            if launch_lease and launch_lease["phase"] == "launching":
                pass
            elif time.time() - state.created_at < _PENDING_LAUNCH_GRACE_SECONDS:
                return
        if (
            self._tracked_process_is_alive(state.job_id)
            or self._process_id_is_alive(state.pid)
        ):
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
            if str(state.job.extra.get("operation_id") or "").strip():
                raise JobConflictError(
                    "operation-bound jobs must be retried through the owning action"
                )
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
                    self._delete_storage_rows(
                        job_id,
                        preserve_job=self._storage_job_has_operation(job_id),
                    )
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
            self._delete_storage_rows(
                job_id,
                preserve_job=bool(
                    str(state.job.extra.get("operation_id") or "").strip()
                ),
            )

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

    def _delete_storage_rows(self, job_id: str, *, preserve_job: bool = False) -> None:
        db_path = self.runtime_dir / "agent_state.db"
        if not db_path.exists():
            return
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            tables = ["agent_artifacts", "agent_events"]
            if not preserve_job:
                tables.append("agent_jobs")
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
            conn.commit()

    def _storage_job_has_operation(self, job_id: str) -> bool:
        db_path = self.runtime_dir / "agent_state.db"
        if not db_path.exists():
            return False
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT operation_id FROM agent_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return bool(row and str(row[0] or "").strip())

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
        extra = state.job.extra
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
            "created_by_user_id": extra.get("created_by_user_id"),
            "workflow_task_id": extra.get("workflow_task_id"),
            "project_id": extra.get("project_id"),
        }
        if include_events:
            data["events"] = state.events
        return data

    def _format_event(self, event: dict[str, Any]) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(float(event.get("ts") or time.time())))
        level = str(event.get("level") or "info").upper()
        message = str(event.get("message") or "")
        return f"[{ts}] [{level}] {message}"
