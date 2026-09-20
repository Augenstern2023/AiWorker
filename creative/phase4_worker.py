# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
from pathlib import Path
import threading
import time
from typing import Any

import config

from analysis.llm import (
    LLMOutputTruncatedError,
    create_llm_provider_from_config,
    get_llm_usage_snapshot,
)
from core.events import emit_event
from core.job import CrawlJob
from creative.imitation_service import complete_with_one_repair, generate_blueprints
from creative.llm_observability import (
    build_attempt_reporter,
    dump_terminal_error,
    public_failure_summary,
    set_attempt_reporter,
)
from creative.original_direction_service import (
    derive_original_direction,
    generate_original_directions,
)
from creative.outline_overview import normalize_series_overview
from creative.postgres_repository import PostgresCreativeRepository
from creative.prompt_templates import PROMPT_TEMPLATE_VERSION, series_overview_messages
from creative.repository import (
    GENERATION_OPERATION_LEASE_SECONDS,
    CreativeRepository,
    canonical_request_hash,
)
from creative.script_service import (
    build_continuity_ledger,
    generate_character_bundle,
    generate_episode_beats,
    generate_episode_beats_chunk,
    generate_episode_script,
    generate_episode_script_chunk,
    generate_outline_bundle,
)
from system_database import SystemDatabase, SystemDatabaseSettings


MAX_INLINE_SOURCE_SNAPSHOT_BYTES = 2 * 1024 * 1024
BEATS_SERIES_CHUNK_SIZE = 10
SCRIPT_SERIES_CHUNK_SIZE = 2


def create_provider(model_record_id: str | None = None):
    if model_record_id:
        return create_llm_provider_from_config(
            config, model_record_id=model_record_id
        )
    return create_llm_provider_from_config(config)


def _create_generation_provider(extra: dict[str, Any]) -> Any:
    model_record_id = str(extra.get("llm_model_record_id") or "").strip()
    if model_record_id:
        return create_provider(model_record_id)
    return create_provider()


def _provider_generation_meta(
    provider: Any, model_record_id: str = ""
) -> dict[str, str]:
    return {
        "llm_provider_id": str(getattr(provider, "provider_id", "") or ""),
        "llm_provider_name": str(
            getattr(provider, "provider_display_name", "")
            or getattr(provider, "provider_name", "")
            or ""
        ),
        "llm_model_record_id": str(
            getattr(provider, "model_record_id", "") or model_record_id
        ),
        "llm_model_id": str(getattr(provider, "model", "") or ""),
        "llm_model_display_name": str(
            getattr(provider, "model_display_name", "")
            or getattr(provider, "model", "")
            or ""
        ),
    }


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "creative-phase4-worker", "creative", level, message, payload or {})


def _usage_payload(job: CrawlJob, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {**(payload or {}), "llm_usage": get_llm_usage_snapshot(
        *getattr(job, "_llm_usage_providers", ())
    )}


def _resolve_outline_reasoning_effort(job: CrawlJob) -> str:
    configured = str(
        getattr(config, "CREATIVE_OUTLINE_REASONING_EFFORT", "high") or "high"
    ).strip().lower()
    if configured == "high":
        return "high"
    emit(
        job,
        "log",
        "CREATIVE_OUTLINE_REASONING_EFFORT 仅支持 high，已回退为 high",
        "warning",
        {"stage": "outlines", "configured_reasoning_effort": configured, "reasoning_effort": "high"},
    )
    return "high"


async def wait_if_paused(job: CrawlJob) -> None:
    runtime_dir = Path(str((job.extra or {}).get("runtime_dir") or ".runtime"))
    marker = runtime_dir / "jobs" / f"{job.job_id}.pause"
    announced = False
    while marker.exists():
        if not announced:
            emit(job, "log", "任务已暂停，将在当前模型请求完成后停在安全检查点")
            announced = True
        await asyncio.sleep(0.5)
    if announced:
        emit(job, "log", "任务已继续")


def _locked_brief(project: dict[str, Any]) -> dict[str, Any]:
    brief_id = project.get("locked_brief_id")
    brief = next((item for item in project["blueprints"] if item["id"] == brief_id), None)
    if not brief:
        raise ValueError("项目尚未锁定 Brief")
    return brief


def _locked_characters(project: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    batch_id = project.get("character_batch_id")
    if not batch_id:
        raise ValueError("项目尚未锁定人物关系")
    readable_statuses = {"locked", "approved"}
    characters = [item for item in project["characters"] if item["batch_id"] == batch_id and item["status"] in readable_statuses]
    relationships = [item for item in project["relationships"] if item["batch_id"] == batch_id and item["status"] in readable_statuses]
    return characters, relationships


def _continuity_beats(
    project: dict[str, Any],
    episode_numbers: range,
    *,
    repository: CreativeRepository | None = None,
) -> list[dict[str, Any]]:
    if repository is not None:
        return repository.compatible_locked_beats(project, episode_numbers)
    latest_by_episode: dict[int, dict[str, Any]] = {}
    for item in project["episodes"]:
        if (
            item["stage"] != "beats"
            or item.get("status") not in {"locked", "approved"}
            or int(item["episode_number"]) not in episode_numbers
        ):
            continue
        number = int(item["episode_number"])
        current = latest_by_episode.get(number)
        if current is None or (int(item.get("version") or 0), int(item["id"])) > (
            int(current.get("version") or 0), int(current["id"])
        ):
            latest_by_episode[number] = item
    return list(latest_by_episode.values())


def _continuity_ledger_before(
    project: dict[str, Any],
    episode_number: int,
    *,
    repository: CreativeRepository | None = None,
) -> list[dict[str, Any]]:
    return build_continuity_ledger(
        _continuity_beats(
            project,
            range(1, int(episode_number)),
            repository=repository,
        )
    )


def _continuity_ledger_through(
    project: dict[str, Any],
    episode_number: int,
    *,
    repository: CreativeRepository | None = None,
) -> list[dict[str, Any]]:
    return build_continuity_ledger(
        _continuity_beats(
            project,
            range(1, int(episode_number) + 1),
            repository=repository,
        )
    )


def _load_source_snapshot(extra: dict[str, Any]) -> dict[str, Any] | None:
    snapshot_id = str(extra.get("source_snapshot_id") or "").strip()
    expected_sha = str(extra.get("source_snapshot_sha256") or "").strip()
    inline = extra.get("source_snapshot")
    reference = extra.get("source_snapshot_ref")
    if not snapshot_id and inline is None and reference is None:
        return None
    if not snapshot_id or not expected_sha:
        raise ValueError("source snapshot id and fingerprint are required")
    if inline is not None and reference is not None:
        raise ValueError("source snapshot must use one storage mode")
    if inline is not None:
        if not isinstance(inline, dict):
            raise ValueError("source snapshot must be an object")
        snapshot = dict(inline)
        canonical = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(canonical) > MAX_INLINE_SOURCE_SNAPSHOT_BYTES:
            raise ValueError("inline source snapshot exceeds 2 MiB")
    else:
        if not isinstance(reference, dict):
            raise ValueError("source snapshot content is required")
        if reference.get("read_only") is not True:
            raise ValueError("source snapshot reference must be read_only")
        if set(reference) != {"paths", "sha256", "size", "read_only"}:
            raise ValueError("source snapshot reference has invalid shape")
        paths = reference.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError("source snapshot chunks are required")
        runtime_root = Path(str(extra.get("runtime_dir") or ".runtime")).resolve()
        chunks: list[bytes] = []
        for raw_path in paths:
            relative_path = Path(str(raw_path))
            if relative_path.is_absolute():
                raise ValueError("source snapshot path escapes runtime root")
            chunk_path = (runtime_root / relative_path).resolve()
            if not chunk_path.is_relative_to(runtime_root):
                raise ValueError("source snapshot path escapes runtime root")
            chunks.append(chunk_path.read_bytes())
        content = b"".join(chunks)
        if int(reference.get("size") or -1) != len(content):
            raise ValueError("source snapshot size mismatch")
        if str(reference.get("sha256") or "") != expected_sha:
            raise ValueError("source snapshot reference fingerprint mismatch")
        try:
            snapshot = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("source snapshot content is invalid") from exc
        if not isinstance(snapshot, dict):
            raise ValueError("source snapshot must be an object")
    embedded_id = snapshot.get("snapshot_id") or snapshot.get("id")
    if embedded_id is not None and str(embedded_id) != snapshot_id:
        raise ValueError("source snapshot id mismatch")
    actual_sha = canonical_request_hash(snapshot)
    if not hmac.compare_digest(actual_sha, expected_sha):
        raise ValueError("source snapshot fingerprint mismatch")
    return snapshot


def _validate_source_authority(
    extra: dict[str, Any], repository: CreativeRepository, project_id: int
) -> dict[str, Any]:
    authority = extra.get("source_authority")
    expected_sha = str(extra.get("source_authority_sha256") or "").strip()
    if not isinstance(authority, dict) or not expected_sha:
        is_pre_envelope_job = (
            extra.get("legacy_authority_compat") is True
            and not extra.get("authority_schema_version")
            and not str(extra.get("operation_id") or "").strip()
            and not str(extra.get("source_snapshot_id") or "").strip()
            and not str(extra.get("generation_request_hash") or "").strip()
        )
        if is_pre_envelope_job:
            return repository.generation_authority_envelope(project_id)
        raise ValueError("server source authority is required")
    supplied_sha = canonical_request_hash(authority)
    if not hmac.compare_digest(supplied_sha, expected_sha):
        raise ValueError("server source authority fingerprint mismatch")
    if int(authority.get("project_id") or 0) != int(project_id):
        raise ValueError("server source authority project mismatch")
    scoped_episode = authority.get("episode_number")
    if scoped_episode is not None and int(extra.get("episode_number") or 0) != int(
        scoped_episode
    ):
        raise ValueError("server source authority episode mismatch")
    authority_stage = authority.get("stage")
    worker_stage = str(extra.get("normalized_stage") or "").strip() or {
        "blueprints": "brief",
        "blueprint_variant": "brief",
        "characters": "characters",
        "outlines": "outline",
        "outline_overview": "outline",
        "beats": "beats",
        "script": "script",
    }.get(str(extra.get("action") or "").strip())
    if authority_stage is not None and (
        authority_stage not in {"brief", "characters", "outline", "beats", "script"}
        or worker_stage != authority_stage
    ):
        raise ValueError("server source authority stage mismatch")
    if scoped_episode is not None and authority_stage is None:
        raise ValueError("server source authority stage is required")
    repository.assert_generation_authority(project_id, authority)
    return dict(authority)


def _generation_context(
    extra: dict[str, Any], source_snapshot: dict[str, Any] | None
) -> dict[str, Any] | None:
    instruction = str(extra.get("generation_instruction") or "").strip()
    if source_snapshot is None and not instruction:
        return None
    return {
        "generation_instruction": instruction,
        "source_snapshot_id": str(extra.get("source_snapshot_id") or ""),
        "source_snapshot": source_snapshot or {},
    }


def _generation_meta(
    meta: dict[str, Any], extra: dict[str, Any]
) -> dict[str, Any]:
    prompt_snapshot = extra.get("prompt_snapshot")
    return {
        **meta,
        "generation_instruction": str(extra.get("generation_instruction") or ""),
        "source_snapshot_id": str(extra.get("source_snapshot_id") or ""),
        "source_snapshot_sha256": str(extra.get("source_snapshot_sha256") or ""),
        "operation_id": str(extra.get("operation_id") or ""),
        "llm_provider_id": str(extra.get("llm_provider_id") or ""),
        "llm_provider_name": str(extra.get("llm_provider_name") or ""),
        "llm_model_record_id": str(extra.get("llm_model_record_id") or ""),
        "llm_model_id": str(extra.get("llm_model_id") or ""),
        "llm_model_display_name": str(
            extra.get("llm_model_display_name") or ""
        ),
        "prompt_source": "template" if isinstance(prompt_snapshot, dict) else "system",
        "prompt_ab_experiment_id": str(
            extra.get("prompt_ab_experiment_id") or ""
        ),
        "prompt_ab_arm": str(extra.get("prompt_ab_arm") or ""),
        "prompt_ab_source_fingerprint": str(
            extra.get("prompt_ab_source_fingerprint") or ""
        ),
        **(
            {
                "prompt_snapshot": prompt_snapshot,
                "prompt_template_id": str(prompt_snapshot.get("template_id") or ""),
                "prompt_template_key": str(prompt_snapshot.get("template_key") or ""),
                "prompt_template_revision": int(prompt_snapshot.get("revision") or 0),
                "prompt_content_hash": str(prompt_snapshot.get("content_hash") or ""),
            }
            if isinstance(prompt_snapshot, dict)
            else {}
        ),
    }


async def generate_series_overview(
    provider: Any,
    project: dict[str, Any],
    episodes: list[dict[str, Any]],
    series_arc: str,
    prompt_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    overview, meta = await complete_with_one_repair(
        provider,
        series_overview_messages(
            {
                "project": project,
                "episodes": episodes,
                "series_arc": series_arc,
            },
            prompt_snapshot,
        ),
        lambda payload: normalize_series_overview(
            payload.get("series_overview") if "series_overview" in payload else payload
        ),
    )
    meta["prompt_template_version"] = PROMPT_TEMPLATE_VERSION
    return overview, meta


async def _run_once(job: CrawlJob, repository: CreativeRepository) -> None:
    extra = job.extra or {}
    action = str(extra.get("action") or "").strip()
    project_id = int(extra.get("project_id") or 0)
    if action not in {"blueprints", "blueprint_variant", "characters", "outlines", "outline_overview", "beats", "script"} or project_id < 1:
        raise ValueError("Phase 4 任务缺少有效 action 或 project_id")

    if extra.get("workflow_mode") == "free":
        from creative.free_phase4_worker import run_free_generation

        await run_free_generation(job, repository, emit)
        return
    source_authority = _validate_source_authority(extra, repository, project_id)
    inputs = repository.get_project_inputs(project_id)
    source_snapshot = _load_source_snapshot(extra)
    prompt_snapshot = (
        dict(source_snapshot.get("prompt_snapshot") or {})
        if isinstance(source_snapshot, dict)
        else {}
    )
    if prompt_snapshot:
        expected_prompt_stage = {
            "blueprints": "brief",
            "blueprint_variant": "brief",
            "characters": "characters",
            "outlines": "outline",
            "outline_overview": "outline",
            "beats": "beats",
            "script": "script",
        }.get(action)
        expected_mode = (
            "rewrite"
            if action in {"blueprint_variant", "outline_overview"}
            else "series"
            if action == "outlines"
            else "series"
            if action == "blueprints" and int(extra.get("count") or 1) > 1
            else "series"
            if bool(extra.get("series"))
            else "single"
        )
        if (
            str(prompt_snapshot.get("stage") or "") != expected_prompt_stage
            or str(prompt_snapshot.get("mode") or "") != expected_mode
        ):
            raise ValueError("server prompt snapshot does not match generation task")
        extra["prompt_snapshot"] = prompt_snapshot
    context = _generation_context(extra, source_snapshot)
    project = dict(inputs["project"])
    if context is not None:
        authority = source_snapshot.get("project") if source_snapshot else None
        if isinstance(authority, dict):
            if int(authority.get("id") or project_id) != project_id:
                raise ValueError("source snapshot project mismatch")
            if (
                authority.get("revision") is not None
                and int(authority["revision"]) != int(project.get("revision") or 0)
            ):
                raise ValueError("source snapshot project revision changed")
        project["generation_context"] = context
    series_generation = bool(extra.get("series")) and action in {"beats", "script"}
    generation_reservation: dict[str, Any] | None = None
    reserved_generation = action == "outlines" or series_generation
    if reserved_generation:
        operation_id = str(extra.get("operation_id") or "").strip()
        generation_request_hash = str(
            extra.get("generation_request_hash") or ""
        ).strip()
        if not operation_id or not generation_request_hash:
            raise ValueError("generation operation identity and request hash are required")
        generation_reservation = repository.reserve_generation_operation(
            operation_id=operation_id,
            project_id=project_id,
            stage="outline" if action == "outlines" else action,
            request_hash=generation_request_hash,
            owner_id=job.job_id,
            predecessor_owner_id=(
                str(extra.get("retry_of_job_id") or "").strip() or None
            ),
        )
        if generation_reservation["status"] == "complete":
            emit(
                job,
                "job_finished",
                "创作内容生成已完成，返回幂等结果",
                payload=_usage_payload(job, generation_reservation["result"]),
            )
            return
    provider = _create_generation_provider(extra)
    job._llm_usage_providers.append(provider)
    extra.update(
        _provider_generation_meta(
            provider, str(extra.get("llm_model_record_id") or "")
        )
    )
    outline_reasoning_effort = "high"
    if action == "outlines":
        outline_reasoning_effort = _resolve_outline_reasoning_effort(job)
        if hasattr(provider, "reasoning_effort"):
            provider.reasoning_effort = outline_reasoning_effort
    total_episodes = (
        int(project.get("target_episode_count") or 0)
        if action == "outlines" or series_generation
        else 1
    )
    retry_completed = 0
    active_outline_request: dict[str, Any] = {}

    def report_retry(retry_number: int, max_retries: int, reason: str) -> None:
        active_request = dict(active_outline_request) if action == "outlines" else {}
        if active_request.get("batch_started_at"):
            active_request["request_elapsed_seconds"] = max(
                0.0,
                time.time() - float(active_request["batch_started_at"]),
            )
        emit(
            job,
            "progress",
            f"模型服务暂时不可用，正在进行第 {retry_number}/{max_retries} 次自动重试：{reason}",
            "warning",
            {
                **active_request,
                "stage": action,
                "completed": retry_completed,
                "total": total_episodes,
                "indeterminate": True,
                "retry_attempt": retry_number,
                "max_retries": max_retries,
            },
        )

    setattr(provider, "retry_callback", report_retry)
    emit(job, "job_started", f"开始生成：{action}")
    emit(job, "progress", "准备模型请求", payload={"stage": action, "completed": 0, "total": total_episodes})
    await wait_if_paused(job)

    if action == "blueprints":
        count = int(extra.get("count") or 3)
        if project.get("creation_mode") == "original":
            seed = repository.get_seed(int(extra.get("seed_id") or 0))
            if seed is None or seed.get("status") != "frozen":
                raise ValueError("原创方向任务缺少已冻结的种子版本")
            seed = dict(seed)
            if context is not None:
                seed["constraints"] = {
                    **dict(seed.get("constraints") or {}),
                    "generation_context": context,
                }
            generated, meta = await generate_original_directions(
                provider, project, seed,
                list(extra.get("market_context") or []),
                list(extra.get("blocked_terms") or []),
                count,
                prompt_snapshot=prompt_snapshot or None,
            )
            source_refs = {
                "seed_id": seed["id"],
                "seed_version": seed["version"],
                "reference_ids": [
                    f"{item.get('resource_type')}:{item.get('resource_id')}"
                    for item in list(extra.get("reference_snapshot") or [])
                ],
            }
            meta = _generation_meta(meta, extra)
            resources = repository.create_blueprints(
                project_id, generated, job.job_id, meta,
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                direction_type="original", source_refs=source_refs,
                operation_id=str(extra.get("operation_id") or "") or None,
                expected_authority=source_authority,
            )
        else:
            breakdown = dict(inputs["breakdown"])
            if context is not None:
                breakdown["content"] = {
                    **dict(breakdown.get("content") or {}),
                    "generation_context": context,
                }
            generated, meta = await generate_blueprints(
                provider,
                project,
                inputs["topic"],
                breakdown,
                count,
                prompt_snapshot=prompt_snapshot or None,
            )
            resources = repository.create_blueprints(
                project_id,
                generated,
                job.job_id,
                _generation_meta(meta, extra),
                operation_id=str(extra.get("operation_id") or "") or None,
                expected_authority=source_authority,
            )
        result: dict[str, Any] = {"action": action, "blueprint_ids": [item["id"] for item in resources]}
    elif action == "blueprint_variant":
        seed = repository.get_seed(int(extra.get("seed_id") or 0))
        if seed is None or seed.get("status") != "frozen":
            raise ValueError("原创方向任务缺少已冻结的种子版本")
        parent = repository.get_blueprint(int(extra.get("parent_blueprint_id") or 0))
        if (
            parent is None
            or int(parent.get("project_id") or 0) != project_id
            or parent.get("direction_type") != "original"
        ):
            raise ValueError("派生任务缺少有效原创父方向")
        generated, meta = await derive_original_direction(
            provider, project, seed, parent,
            instruction=str(extra.get("instruction") or ""),
            market_context=list(extra.get("market_context") or []),
            blocked_terms=list(extra.get("blocked_terms") or []),
            prompt_snapshot=prompt_snapshot or None,
        )
        resources = repository.create_blueprints(
            project_id, [generated], job.job_id, _generation_meta(meta, extra),
            workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
            direction_type="original", source_refs=generated["source_refs"],
            operation_id=str(extra.get("operation_id") or "") or None,
            expected_authority=source_authority,
        )
        result = {"action": action, "blueprint_ids": [resources[0]["id"]]}
    elif action == "characters":
        brief = dict(_locked_brief(project))
        if context is not None:
            brief["generation_context"] = context
        generated, meta = await generate_character_bundle(
            provider, project, brief, prompt_snapshot=prompt_snapshot or None
        )
        bundle = repository.create_character_batch(
            project_id,
            generated["characters"],
            generated["relationships"],
            job.job_id,
            _generation_meta(meta, extra),
            workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
            operation_id=str(extra.get("operation_id") or "") or None,
            expected_authority=source_authority,
        )
        result = {"action": action, "batch_id": bundle["batch_id"]}
    elif action == "outlines":
        brief = dict(_locked_brief(project))
        if context is not None:
            brief["generation_context"] = context
        characters, relationships = _locked_characters(project)
        operation_id = str(extra.get("operation_id") or "") or None
        resumable = repository.find_resumable_outline_batch(
            project_id,
            operation_id=operation_id,
            include_complete=operation_id is not None,
        )
        outline_batch_id: str | None = (
            str(generation_reservation["batch_id"])
            if generation_reservation is not None
            else (resumable["batch_id"] if resumable else None)
        )
        resume_episodes = [item["content"] for item in resumable["episodes"]] if resumable else None
        resume_series_arc = resumable["series_arc"] if resumable else None
        resume_stages = resumable["stages"] if resumable else None
        resume_series_overview = resumable["series_overview"] if resumable else None
        if resumable:
            completed = len(resume_episodes or [])
            total = int(project["target_episode_count"])
            emit(job, "log", f"接续已保存的大纲草稿：从第 {completed + 1} 集继续")
            emit(
                job,
                "progress",
                f"已恢复 {completed}/{total} 集大纲",
                payload={"stage": action, "completed": completed, "total": total},
            )

        outline_completed = len(resume_episodes or [])
        outline_total = int(project["target_episode_count"])
        retry_completed = outline_completed

        def report_outline_request_progress(request: dict[str, Any]) -> None:
            phase = str(request.get("phase") or "")
            request_kind = str(request.get("request_kind") or "")
            if phase == "started":
                active_outline_request.clear()
                active_outline_request.update(
                    {
                        key: request.get(key)
                        for key in (
                            "request_kind", "batch_start", "batch_end",
                            "batch_started_at", "reasoning_effort", "prompt_chars",
                            "request_elapsed_seconds", "batch_in_flight",
                        )
                    }
                )
            scope = "全剧阶段弧线" if request_kind == "series_arc" else (
                f"第 {request.get('batch_start')}-{request.get('batch_end')} 集"
            )
            if phase == "truncated":
                message = f"{scope}输出达到上限，准备拆分"
            elif phase == "split":
                message = (
                    f"已将第 {request.get('batch_start')}-{request.get('batch_end')} 集拆分为"
                    f"第 {request.get('left_start')}-{request.get('left_end')} 集和"
                    f"第 {request.get('right_start')}-{request.get('right_end')} 集"
                )
            elif request_kind == "series_arc":
                message = "正在规划全剧阶段弧线" if phase == "started" else (
                    f"{scope}模型响应{'失败' if phase == 'failed' else '完成'}"
                )
            else:
                message = f"正在生成{scope}大纲" if phase == "started" else (
                    f"{scope}大纲模型响应{'失败' if phase == 'failed' else '完成'}"
                )
            payload = {
                **request,
                "stage": action,
                "completed": outline_completed,
                "total": outline_total,
            }
            emit(
                job,
                "progress",
                message,
                "error" if phase == "failed" else "warning" if phase == "truncated" else "info",
                payload,
            )
            if phase in {"finished", "failed", "truncated"}:
                active_outline_request.clear()

        def save_outline_checkpoint(
            arc: dict[str, Any], episodes: list[dict[str, Any]], meta: dict[str, Any]
        ) -> None:
            nonlocal outline_batch_id
            if generation_reservation is not None:
                repository.validate_generation_reservation(
                    operation_id=str(extra["operation_id"]),
                    request_hash=str(extra["generation_request_hash"]),
                    batch_id=str(generation_reservation["batch_id"]),
                    owner_id=job.job_id,
                )
            saved = repository.save_outline_batch_checkpoint(
                project_id,
                episodes,
                str(arc["series_arc"]),
                job.job_id,
                {
                    **_generation_meta(meta, extra),
                    "stages": arc["stages"],
                    "checkpoint_episode_count": len(episodes),
                },
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                batch_id=outline_batch_id,
                series_overview=arc.get("series_overview"),
                reservation_operation_id=(
                    str(extra["operation_id"])
                    if generation_reservation is not None
                    else None
                ),
                reservation_request_hash=(
                    str(extra["generation_request_hash"])
                    if generation_reservation is not None
                    else None
                ),
                reservation_owner_id=(
                    job.job_id if generation_reservation is not None else None
                ),
                expected_authority=source_authority,
            )
            outline_batch_id = saved["batch_id"]

        def progress(done: int, total: int) -> None:
            nonlocal outline_completed, retry_completed
            outline_completed = done
            retry_completed = done
            emit(
                job,
                "progress",
                f"已生成 {done}/{total} 集大纲",
                payload={"stage": action, "completed": done, "total": total},
            )

        if len(resume_episodes or []) < int(project["target_episode_count"]):
            await generate_outline_bundle(
                provider,
                project,
                brief,
                characters,
                relationships,
                on_progress=progress,
                on_request_progress=report_outline_request_progress,
                on_checkpoint=save_outline_checkpoint,
                checkpoint=lambda: wait_if_paused(job),
                resume_episodes=resume_episodes,
                resume_series_arc=resume_series_arc,
                resume_stages=resume_stages,
                resume_series_overview=resume_series_overview,
                reasoning_effort=outline_reasoning_effort,
                prompt_snapshot=prompt_snapshot or None,
            )
        if outline_batch_id is None:
            raise RuntimeError("分集大纲未创建任何生成检查点")
        bundle = repository.get_outline_batch(project_id, outline_batch_id)
        result = {"action": action, "batch_id": bundle["batch_id"], "episode_count": len(bundle["episodes"])}
    elif action == "outline_overview":
        batch_id = str(extra.get("batch_id") or "").strip()
        batch = repository.assert_outline_overview_generation_ready(
            project_id, batch_id
        )
        first_content = batch["episodes"][0].get("content") or {}
        overview, meta = await generate_series_overview(
            provider,
            project,
            [item.get("content") or {} for item in batch["episodes"]],
            str(first_content.get("series_arc") or ""),
            prompt_snapshot=prompt_snapshot or None,
        )
        saved = repository.update_outline_overview(
            project_id,
            batch_id,
            overview,
            only_if_missing=True,
            expected_authority=source_authority,
        )
        emit(
            job,
            "progress",
            "全集剧情概览已生成",
            payload={"stage": action, "completed": 1, "total": 1},
        )
        result = {
            "action": action,
            "batch_id": batch_id,
            "episode_count": len(saved["episodes"]),
        }
    elif series_generation:
        brief = dict(_locked_brief(project))
        if context is not None:
            brief["generation_context"] = context
        characters, relationships = _locked_characters(project)
        if action == "script":
            missing_beats = repository.missing_current_locked_beats(project_id)
            if missing_beats:
                raise ValueError(f"全剧场次剧本生成前必须锁定全部 Beats；未锁定集数：{','.join(map(str, missing_beats))}")
        resumable = repository.find_resumable_episode_series(
            project_id,
            action,
            operation_id=str(extra.get("operation_id") or "") or None,
        )
        series_batch_id: str | None = (
            str(generation_reservation["batch_id"])
            if generation_reservation is not None
            else (resumable["batch_id"] if resumable else None)
        )
        completed = int(resumable["completed_episode_count"]) if resumable else 0
        generated_beats = list(resumable["episodes"]) if resumable and action == "beats" else []
        if completed:
            emit(job, "log", f"接续已保存的{action}草稿：从第 {completed + 1} 集继续")
            emit(
                job, "progress", f"已恢复 {completed}/{total_episodes} 集",
                payload={"stage": action, "completed": completed, "total": total_episodes},
            )
        series_chunk_size = (
            SCRIPT_SERIES_CHUNK_SIZE
            if action == "script"
            else BEATS_SERIES_CHUNK_SIZE
        )

        async def save_series_checkpoint(
            checkpoint_start: int,
            checkpoint_end: int,
            checkpoint_items: list[tuple[int, dict[str, Any]]],
            meta: dict[str, Any],
        ) -> None:
            nonlocal series_batch_id
            if generation_reservation is not None:
                repository.validate_generation_reservation(
                    operation_id=str(extra["operation_id"]),
                    request_hash=str(extra["generation_request_hash"]),
                    batch_id=str(generation_reservation["batch_id"]),
                    owner_id=job.job_id,
                )
            saved = repository.save_episode_generation_checkpoint(
                project_id,
                action,
                checkpoint_items,
                job.job_id,
                {
                    **_generation_meta(meta, extra),
                    "series_generation": True,
                    "chunk_size": series_chunk_size,
                    "preferred_chunk_size": series_chunk_size,
                    "actual_chunk_size": checkpoint_end - checkpoint_start + 1,
                    "checkpoint_start": checkpoint_start,
                    "checkpoint_end": checkpoint_end,
                    "checkpoint_episode_count": checkpoint_end,
                },
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                batch_id=series_batch_id,
                reservation_operation_id=str(extra["operation_id"]),
                reservation_request_hash=str(extra["generation_request_hash"]),
                reservation_owner_id=job.job_id,
                expected_authority=source_authority,
            )
            series_batch_id = saved[0]["batch_id"]
            if action == "beats":
                generated_beats.extend(saved)
            emit(
                job,
                "progress",
                f"已生成并保存第 {checkpoint_start}-{checkpoint_end} 集"
                f"{'Beats' if action == 'beats' else '场次剧本'}",
                payload={
                    "stage": action,
                    "completed": checkpoint_end,
                    "total": total_episodes,
                    "chunk_start": checkpoint_start,
                    "chunk_end": checkpoint_end,
                    "preferred_chunk_size": series_chunk_size,
                },
            )

        def locked_outlines_for_range(
            range_start: int, range_end: int
        ) -> list[dict[str, Any]]:
            selected = [
                item
                for item in project["episodes"]
                if item["stage"] == "outline"
                and item.get("batch_id") == project.get("outline_batch_id")
                and range_start <= int(item["episode_number"]) <= range_end
                and item.get("status") in {"locked", "approved"}
            ]
            if sorted(int(item["episode_number"]) for item in selected) != list(
                range(range_start, range_end + 1)
            ):
                raise ValueError(
                    f"未找到第 {range_start}-{range_end} 集的锁定大纲"
                )
            return selected

        async def generate_and_save_beats_range(
            range_start: int, range_end: int
        ) -> None:
            nonlocal retry_completed
            retry_completed = range_start - 1
            await wait_if_paused(job)
            outlines = locked_outlines_for_range(range_start, range_end)
            prior = repository.compatible_locked_beats(
                project, range(1, range_start)
            ) + generated_beats
            try:
                checkpoint_items, meta = await generate_episode_beats_chunk(
                    provider,
                    project,
                    brief,
                    outlines,
                    characters,
                    relationships,
                    continuity_ledger=build_continuity_ledger(prior),
                    prompt_snapshot=prompt_snapshot or None,
                )
            except LLMOutputTruncatedError as exc:
                if range_start == range_end:
                    raise LLMOutputTruncatedError(
                        f"第 {range_start} 集 Beats 生成仍达到模型输出上限；"
                        "请提高模型输出上限或减少单集字段内容",
                        finish_reason=exc.finish_reason,
                        returned_chars=exc.returned_chars,
                    ) from exc
                midpoint = (range_start + range_end - 1) // 2
                emit(
                    job,
                    "progress",
                    f"第 {range_start}-{range_end} 集 Beats 输出达到上限，"
                    f"自动拆分为第 {range_start}-{midpoint} 集和"
                    f"第 {midpoint + 1}-{range_end} 集",
                    "warning",
                    {
                        "stage": action,
                        "completed": range_start - 1,
                        "total": total_episodes,
                        "split_start": range_start,
                        "split_end": range_end,
                        "left_start": range_start,
                        "left_end": midpoint,
                        "right_start": midpoint + 1,
                        "right_end": range_end,
                        "finish_reason": exc.finish_reason,
                        "returned_chars": exc.returned_chars,
                    },
                )
                await generate_and_save_beats_range(range_start, midpoint)
                await generate_and_save_beats_range(midpoint + 1, range_end)
                return
            await save_series_checkpoint(
                range_start, range_end, checkpoint_items, meta
            )

        for start in range(
            completed + 1, total_episodes + 1, series_chunk_size
        ):
            end = min(total_episodes, start + series_chunk_size - 1)
            retry_completed = start - 1
            if action == "beats":
                await generate_and_save_beats_range(start, end)
                continue
            await wait_if_paused(job)
            outlines = locked_outlines_for_range(start, end)
            selected_beats = {
                int(item["episode_number"]): item
                for item in repository.compatible_locked_beats(
                    project, range(start, end + 1)
                )
            }
            if sorted(selected_beats) != list(range(start, end + 1)):
                raise ValueError(
                    f"未找到第 {start}-{end} 集的当前兼容 locked Beats"
                )
            beats_by_episode = {
                episode_number: selected_beats[episode_number]["content"]
                for episode_number in range(start, end + 1)
            }
            checkpoint_items, meta = await generate_episode_script_chunk(
                provider,
                project,
                brief,
                outlines,
                characters,
                relationships,
                beats_by_episode,
                continuity_ledger=_continuity_ledger_through(
                    project, start - 1, repository=repository
                ),
                prompt_snapshot=prompt_snapshot or None,
            )
            await save_series_checkpoint(start, end, checkpoint_items, meta)
        result = {
            "action": action, "series_generation": True, "batch_id": series_batch_id,
            "episode_count": total_episodes, "resumed_from_episode": completed,
            "brief_id": brief["id"],
        }
    else:
        brief = dict(_locked_brief(project))
        if context is not None:
            brief["generation_context"] = context
        characters, relationships = _locked_characters(project)
        episode_number = int(extra.get("episode_number") or 0)
        outline = next(
            (
                item for item in project["episodes"]
                if item["stage"] == "outline"
                and item["batch_id"] == project.get("outline_batch_id")
                and int(item["episode_number"]) == episode_number
            ),
            None,
        )
        if not outline:
            raise ValueError("未找到指定集的锁定大纲")
        if action == "beats":
            generated, meta = await generate_episode_beats(
                provider,
                project,
                outline,
                characters,
                episode_number,
                continuity_ledger=_continuity_ledger_before(
                    project, episode_number, repository=repository
                ),
                brief=brief,
                relationships=relationships,
                prompt_snapshot=prompt_snapshot or None,
            )
            resource = repository.create_episode_beats(
                project_id,
                episode_number,
                generated,
                job.job_id,
                _generation_meta(meta, extra),
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                operation_id=str(extra.get("operation_id") or "") or None,
                expected_authority=source_authority,
            )
        else:
            compatible_beats = repository.compatible_locked_beats(
                project, [episode_number]
            )
            if not compatible_beats:
                raise ValueError("必须先锁定当前集 Beats")
            beats = compatible_beats[0]
            generated, meta = await generate_episode_script(
                provider,
                project,
                outline,
                characters,
                beats["content"],
                episode_number,
                continuity_ledger=_continuity_ledger_through(
                    project, episode_number, repository=repository
                ),
                brief=brief,
                relationships=relationships,
                prompt_snapshot=prompt_snapshot or None,
            )
            resource = repository.create_episode_script(
                project_id,
                episode_number,
                generated,
                job.job_id,
                _generation_meta(meta, extra),
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                operation_id=str(extra.get("operation_id") or "") or None,
                expected_authority=source_authority,
            )
        result = {"action": action, "episode_id": resource["id"], "episode_number": episode_number, "brief_id": brief["id"]}

    await wait_if_paused(job)
    if generation_reservation is not None:
        repository.complete_generation_operation(
            operation_id=str(extra["operation_id"]),
            request_hash=str(extra["generation_request_hash"]),
            batch_id=str(generation_reservation["batch_id"]),
            result=result,
            owner_id=job.job_id,
            expected_authority=source_authority,
        )
    target_dir = Path("data") / "creative" / "jobs" / job.job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / "generation.json"
    report_path.write_text(
        json.dumps({"job_id": job.job_id, "project_id": project_id, **result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    emit(job, "artifact", "Phase 4 生成记录", payload={"path": str(report_path), "artifact_type": "json"})
    emit(job, "progress", "生成完成", payload={"stage": action, "completed": total_episodes, "total": total_episodes})
    emit(
        job,
        "job_finished",
        "创作内容生成完成",
        payload=_usage_payload(job, result),
    )


async def run(job: CrawlJob) -> None:
    # Runtime-only references survive asyncio.run exceptions; CrawlJob.to_dict
    # serializes declared dataclass fields only, never these providers.
    job._llm_usage_providers = []
    database = SystemDatabase(SystemDatabaseSettings.from_environment())
    try:
        await _run_with_repository(job, PostgresCreativeRepository(database))
    finally:
        database.dispose()


async def _run_with_repository(job: CrawlJob, repository: CreativeRepository) -> None:
    job._llm_usage_providers = []
    extra = job.extra or {}
    action = str(extra.get("action") or "").strip()
    reserved_generation = extra.get("workflow_mode") != "free" and (
        action == "outlines" or (
            bool(extra.get("series")) and action in {"beats", "script"}
        )
    )
    operation_id = str(extra.get("operation_id") or "").strip()
    request_hash = str(extra.get("generation_request_hash") or "").strip()
    project_id = int(extra.get("project_id") or 0)
    heartbeat_stop = threading.Event()
    heartbeat: threading.Thread | None = None
    if reserved_generation and operation_id and request_hash and project_id > 0:
        heartbeat_repository = repository

        def renew_lease() -> None:
            heartbeat_interval = GENERATION_OPERATION_LEASE_SECONDS / 3
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    heartbeat_repository.renew_generation_operation(
                        operation_id=operation_id,
                        request_hash=request_hash,
                        owner_id=job.job_id,
                    )
                except Exception:
                    continue

        heartbeat = threading.Thread(target=renew_lease, daemon=True)
        heartbeat.start()
    previous_reporter = set_attempt_reporter(
        build_attempt_reporter(
            lambda message, level, payload: emit(job, "log", message, level, payload),
            job_id=job.job_id,
            stage=str(extra.get("normalized_stage") or action or ""),
        )
    )
    try:
        await _run_once(job, repository)
    except Exception:
        if reserved_generation:
            if operation_id and request_hash and project_id > 0:
                try:
                    repository.fail_generation_operation(
                        operation_id=operation_id,
                        request_hash=request_hash,
                        owner_id=job.job_id,
                    )
                except Exception:
                    pass
        raise
    finally:
        set_attempt_reporter(previous_reporter)
        heartbeat_stop.set()
        if heartbeat is not None:
            heartbeat.join(timeout=1.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Phase 4 creative assets.")
    parser.add_argument("--job-file", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    job = CrawlJob.load(args.job_file)
    try:
        asyncio.run(run(job))
    except Exception as exc:
        error_code, message = public_failure_summary(exc)
        error_debug_path = dump_terminal_error(job.job_id, exc)
        emit(
            job,
            "job_failed",
            message,
            "error",
            _usage_payload(job, {"error_code": error_code, "error_debug_path": error_debug_path}),
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
