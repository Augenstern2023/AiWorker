# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from creative.postgres_repository import PostgresCreativeRepository
from creative.repository import CreativeRepository
from creative.transcript_assets import find_transcript_bundle, normalize_title
from system_database import SystemDatabase, SystemDatabaseSettings


class SelectionStageService:
    """Records immutable completion evidence for approval-free selection stages."""

    def __init__(
        self, repository: CreativeRepository, workspace_root: str | Path
    ) -> None:
        self.repository = repository
        self.workspace_root = Path(workspace_root).resolve()

    def record_topic_decision(
        self,
        topic_id: int,
        outcome: str,
        decision_note: str,
        actor_user_id: str,
    ) -> dict[str, Any]:
        topic = self.repository.get_topic(topic_id)
        if topic is None:
            raise ValueError("选题不存在")
        return self.repository.append_selection_stage_snapshot(
            "topic_decision",
            topic_id,
            outcome,
            {
                "topic_id": topic_id,
                "title": topic["title"],
                "outcome": outcome,
                "decision_note": decision_note,
            },
            resource_type="topic",
            resource_id=topic_id,
            actor_user_id=actor_user_id,
        )

    def record_source_readiness(
        self,
        topic_id: int,
        *,
        source_job_id: str = "",
        actor_user_id: str = "",
    ) -> dict[str, Any] | None:
        topic = self.repository.get_topic(topic_id)
        if topic is None:
            raise ValueError("选题不存在")
        bundle = find_transcript_bundle(
            self.workspace_root, topic["title"], topic.get("platform") or ""
        )
        valid = []
        for episode in bundle.get("episodes") or []:
            text = str(episode.get("text") or "").strip()
            if not text:
                continue
            valid.append(
                {
                    "episode": int(episode.get("episode_number") or 0),
                    "transcript_source": str(
                        episode.get("transcript_source") or "transcript"
                    ),
                    "script_path": str(episode.get("script_path") or ""),
                    "transcript_path": str(episode.get("transcript_path") or ""),
                    "text_fingerprint": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "character_count": len(text),
                }
            )
        if not valid:
            return None
        skipped_paid, failed = self._manifest_failures(topic["title"])
        snapshot = {
            "topic_id": topic_id,
            "title": topic["title"],
            "valid_transcript_episodes": sorted(
                {item["episode"] for item in valid if item["episode"] > 0}
            ),
            "transcripts": sorted(valid, key=lambda item: item["episode"]),
            "skipped_paid": skipped_paid,
            "failed": failed,
            "legacy_workflow_task_id": "",
        }
        return self.repository.append_selection_stage_snapshot(
            "source_package",
            topic_id,
            "ready",
            snapshot,
            resource_type="transcript_bundle",
            resource_id=topic_id,
            actor_user_id=actor_user_id,
            source_job_id=source_job_id,
            deduplicate=True,
        )

    def record_breakdown_completion(
        self, breakdown_id: int, actor_user_id: str
    ) -> dict[str, Any]:
        breakdown = self.repository.get_breakdown(breakdown_id)
        if breakdown is None:
            raise ValueError("拆解不存在")
        if breakdown["status"] not in {"locked", "approved"}:
            raise ValueError("拆解尚未锁定")
        return self.repository.append_selection_stage_snapshot(
            "breakdown",
            int(breakdown["topic_id"]),
            "completed",
            {
                "breakdown_id": breakdown_id,
                "version": breakdown["version"],
                "status": breakdown["status"],
                "content": breakdown["content"],
                "field_sources": breakdown["field_sources"],
                "risks": breakdown["risks"],
            },
            resource_type="breakdown",
            resource_id=breakdown_id,
            actor_user_id=actor_user_id,
            deduplicate=True,
        )

    def _manifest_failures(
        self, title: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        target = normalize_title(title)
        skipped_paid: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        data_root = self.workspace_root / "data"
        if not data_root.exists():
            return skipped_paid, failed
        for path in data_root.rglob("manifest.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            manifest_title = str(payload.get("title") or "")
            path_matches = any(
                normalize_title(part) == target for part in path.parts
            )
            if normalize_title(manifest_title) != target and not path_matches:
                continue
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "")
                record = {
                    key: item.get(key)
                    for key in ("episode", "index", "aweme_id", "status", "reason")
                    if item.get(key) not in (None, "")
                }
                if status == "skipped_paid":
                    skipped_paid.append(record)
                elif status == "failed":
                    failed.append(record)
        return skipped_paid, failed


def record_source_readiness_for_job(
    job: Any, repository: CreativeRepository | None = None
) -> dict[str, Any] | None:
    extra = job.extra or {}
    topic_id = int(extra.get("topic_id") or 0)
    if topic_id < 1:
        return None
    workspace_root = Path(str(extra.get("workspace_root") or ".")).resolve()
    database: SystemDatabase | None = None
    if repository is None:
        database = SystemDatabase(SystemDatabaseSettings.from_environment())
        repository = PostgresCreativeRepository(database)
    try:
        return SelectionStageService(repository, workspace_root).record_source_readiness(
            topic_id,
            source_job_id=str(job.job_id),
            actor_user_id=str(extra.get("created_by_user_id") or "workflow:system"),
        )
    finally:
        if database is not None:
            database.dispose()
