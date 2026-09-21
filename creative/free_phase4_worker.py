# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import config

from analysis.llm import (
    LLMOutputTruncatedError,
    apply_reasoning_effort_override,
    create_llm_provider_from_config,
    get_llm_usage_snapshot,
)
from core.job import CrawlJob
from creative.free_mode_service import FreeCreativeModeService
from creative.original_direction_service import generate_original_directions
from creative.repository import CreativeRepository, canonical_request_hash
from creative.script_service import (
    build_continuity_ledger,
    generate_character_bundle,
    generate_episode_beats,
    generate_episode_beats_chunk,
    generate_episode_script,
    generate_episode_script_chunk,
    generate_outline_bundle,
)
from creative.script_series import (
    SCRIPT_SERIES_MAX_CONCURRENCY,
    resolve_script_series_chunk_size,
    run_ordered_script_windows,
)


BEATS_SERIES_CHUNK_SIZE = 10


async def _wait_if_paused(job: CrawlJob, emit: Callable[..., None]) -> None:
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


def _create_generation_provider(extra: dict[str, Any]) -> Any:
    model_record_id = str(extra.get("llm_model_record_id") or "").strip()
    if model_record_id:
        provider = create_llm_provider_from_config(
            config, model_record_id=model_record_id
        )
    else:
        provider = create_llm_provider_from_config(config)
    apply_reasoning_effort_override(
        provider, str(extra.get("reasoning_effort") or "")
    )
    return provider


def _llm_generation_meta(
    provider: Any, model_record_id: str = ""
) -> dict[str, str]:
    values = {
        "llm_provider_id": str(getattr(provider, "provider_id", "") or ""),
        "llm_provider_name": str(
            getattr(provider, "provider_display_name", "")
            or getattr(provider, "provider_name", "")
            or ""
        ),
        "llm_model_record_id": str(
            getattr(provider, "model_record_id", "")
            or model_record_id
            or ""
        ),
        "llm_model_id": str(getattr(provider, "model", "") or ""),
        "llm_model_display_name": str(
            getattr(provider, "model_display_name", "")
            or getattr(provider, "model", "")
            or ""
        ),
        "reasoning_effort": str(
            getattr(provider, "reasoning_effort", "") or ""
        ),
    }
    return {key: value for key, value in values.items() if value}


def _usage_payload(job: CrawlJob, payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "llm_usage": get_llm_usage_snapshot(
        *getattr(job, "_llm_usage_providers", ())
    )}


def _prompt_inputs(
    context: dict[str, Any], episode_number: int | None
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    snapshot = dict(context["context_snapshot"])
    seed = dict(snapshot.get("seed") or {})
    instruction = str(snapshot.get("instruction") or "").strip()
    idea = instruction or str(seed.get("seed_text") or "").strip()
    brief_content = dict(snapshot.get("brief") or {})
    if not brief_content:
        brief_content = {
            "title": str((snapshot.get("project") or {}).get("name") or "自由创作"),
            "logline": idea,
            "synopsis": idea,
        }
    brief = {
        "id": (context.get("context_manifest") or {}).get("brief", {}).get("resource_ref"),
        "content": brief_content,
        "generation_context": {
            "instruction": instruction,
            "seed": seed,
            "context_id": context["id"],
        },
    }
    characters = [dict(item) for item in snapshot.get("characters") or []]
    relationships = [dict(item) for item in snapshot.get("relationships") or []]
    selected_outline: dict[str, Any] = {}
    for item in snapshot.get("outlines") or []:
        if int(item.get("episode") or 0) == int(episode_number or 0):
            selected_outline = dict(item)
            break
    if not selected_outline:
        selected_outline = {
            "episode": int(episode_number or 1),
            "core_event": idea,
            "conflict": instruction,
            "character_progress": "",
            "highlight": "",
            "reversal": "",
            "cliffhanger": "",
        }
    outline = {"episode_number": int(episode_number or 1), "content": selected_outline}
    beats = dict(snapshot.get("beats") or {"beats": []})
    return brief, characters, relationships, outline, beats


async def _run_free_episode_series(
    job: CrawlJob,
    repository: CreativeRepository,
    service: FreeCreativeModeService,
    emit: Callable[..., None],
    *,
    stage: str,
    project_id: int,
    actor_user_id: str,
    extra: dict[str, Any],
) -> None:
    if stage not in {"beats", "script"}:
        raise ValueError("自由创作全剧生成阶段无效")
    resource_label = "Beats" if stage == "beats" else "场次剧本"
    project = repository.get_project(project_id) or {}
    total = int(project.get("target_episode_count") or 0)
    if total < 1:
        raise ValueError(f"自由{resource_label}全剧生成缺少有效集数")
    if int(extra.get("episode_start") or 0) != 1 or int(
        extra.get("episode_end") or 0
    ) != total:
        raise ValueError(f"自由{resource_label}全剧生成范围无效")
    try:
        context_ids = {
            int(number): str(context_id)
            for number, context_id in dict(extra.get("free_context_ids") or {}).items()
        }
        expected_head_revisions = {
            int(number): int(revision)
            for number, revision in dict(
                extra.get("expected_head_revisions") or {}
            ).items()
        }
    except (TypeError, ValueError) as exc:
        raise ValueError(f"自由{resource_label}全剧生成版本信息无效") from exc
    required_episodes = set(range(1, total + 1))
    if (
        set(context_ids) != required_episodes
        or set(expected_head_revisions) != required_episodes
        or any(revision < 0 for revision in expected_head_revisions.values())
    ):
        raise ValueError(
            f"自由{resource_label}全剧生成必须包含全部集上下文和 Head 版本"
        )

    contexts: dict[int, dict[str, Any]] = {}
    completed_resources: dict[int, dict[str, Any]] = {}
    operation_id = str(extra.get("operation_id") or "")
    for episode_number in range(1, total + 1):
        context = service.get_generation_context(context_ids[episode_number])
        if (
            int(context["project_id"]) != project_id
            or context["target_stage"] != stage
            or int(context.get("episode_number") or 0) != episode_number
        ):
            raise ValueError(f"第 {episode_number} 集自由创作上下文与目标不匹配")
        head = service.get_head(project_id, stage, episode_number)
        current_revision = int(head["revision"]) if head is not None else 0
        if current_revision != expected_head_revisions[episode_number]:
            resource = (
                repository.get_episode(int(head["resource_ref"]))
                if head is not None
                else None
            )
            generation_meta = dict((resource or {}).get("generation_meta") or {})
            if (
                current_revision == expected_head_revisions[episode_number] + 1
                and resource is not None
                and str(generation_meta.get("operation_id") or "") == operation_id
                and str(generation_meta.get("generation_context_id") or "")
                == str(context["id"])
            ):
                completed_resources[episode_number] = resource
            else:
                raise ValueError(
                    f"第 {episode_number} 集 {resource_label} 已发生变化，请刷新后重试"
                )
        contexts[episode_number] = context

    provider = _create_generation_provider(extra)
    job._llm_usage_providers.append(provider)
    llm_meta = _llm_generation_meta(
        provider, str(extra.get("llm_model_record_id") or "")
    )
    script_series_chunk_size = resolve_script_series_chunk_size(provider)
    emit(job, "job_started", f"开始自由创作：全剧{resource_label}")
    emit(
        job,
        "progress",
        f"准备生成全部集{resource_label}",
        payload={"stage": stage, "completed": 0, "total": total},
    )
    if completed_resources:
        completed_count = len(completed_resources)
        emit(
            job,
            "log",
            f"接续已保存的自由创作{resource_label}：已恢复 {completed_count}/{total} 集",
        )
        emit(
            job,
            "progress",
            f"已恢复 {completed_count}/{total} 集",
            payload={"stage": stage, "completed": completed_count, "total": total},
        )
    prepared: dict[int, dict[str, Any]] = {}
    for episode_number in range(1, total + 1):
        context = contexts[episode_number]
        snapshot = dict(context["context_snapshot"])
        prompt_snapshot = snapshot.get("prompt_snapshot")
        episode_project = dict(project)
        episode_project.update(snapshot.get("project") or {})
        brief, characters, relationships, outline, beats = _prompt_inputs(
            context, episode_number
        )
        batch_brief = dict(brief)
        generation_context = dict(batch_brief.get("generation_context") or {})
        generation_context.pop("context_id", None)
        batch_brief["generation_context"] = generation_context
        prepared[episode_number] = {
            "context": context,
            "prompt_snapshot": (
                dict(prompt_snapshot) if isinstance(prompt_snapshot, dict) else None
            ),
            "project": episode_project,
            "brief": brief,
            "batch_brief": batch_brief,
            "characters": characters,
            "relationships": relationships,
            "outline": outline,
            "beats": beats,
        }

    episode_ids: list[int] = [
        int(completed_resources[number]["id"])
        for number in sorted(completed_resources)
    ]
    generated_beats: list[dict[str, Any]] = (
        [completed_resources[number] for number in sorted(completed_resources)]
        if stage == "beats"
        else []
    )

    def save_episode(
        episode_number: int,
        generated: dict[str, Any],
        meta: dict[str, Any],
        *,
        batch_start: int | None = None,
        batch_end: int | None = None,
    ) -> None:
        item = prepared[episode_number]
        context = item["context"]
        prompt_snapshot = item["prompt_snapshot"]
        result = service.create_episode_resource(
            project_id,
            stage,
            episode_number,
            generated,
            expected_head_revision=expected_head_revisions[episode_number],
            actor_user_id=actor_user_id,
            source_refs=dict(context.get("context_manifest") or {}),
            generation_meta={
                **meta,
                **llm_meta,
                "job_id": job.job_id,
                "operation_id": str(extra.get("operation_id") or ""),
                "generation_context_id": context["id"],
                "generation_context_hash": context["context_hash"],
                "prompt_template_version": context.get("prompt_schema_version") or "",
                "resource_scope": "free",
                "series_generation": True,
                **(
                    {
                        "preferred_chunk_size": (
                            BEATS_SERIES_CHUNK_SIZE
                            if stage == "beats"
                            else script_series_chunk_size
                        ),
                        "actual_chunk_size": int(batch_end) - int(batch_start) + 1,
                        "checkpoint_start": int(batch_start),
                        "checkpoint_end": int(batch_end),
                    }
                    if batch_start is not None and batch_end is not None
                    else {}
                ),
                "prompt_source": "template" if isinstance(prompt_snapshot, dict) else "system",
                **(
                    {
                        "prompt_snapshot": prompt_snapshot,
                        "prompt_template_id": str(
                            prompt_snapshot.get("template_id") or ""
                        ),
                        "prompt_template_key": str(
                            prompt_snapshot.get("template_key") or ""
                        ),
                        "prompt_template_revision": int(
                            prompt_snapshot.get("revision") or 0
                        ),
                        "prompt_content_hash": str(
                            prompt_snapshot.get("content_hash") or ""
                        ),
                    }
                    if isinstance(prompt_snapshot, dict)
                    else {}
                ),
            },
        )
        episode_ids.append(int(result["resource"]["id"]))
        if stage == "beats":
            generated_beats.append(result["resource"])
        emit(
            job,
            "progress",
            f"已生成并保存第 {episode_number}/{total} 集{resource_label}",
            payload={
                "stage": stage,
                "completed": episode_number,
                "total": total,
                "episode_number": episode_number,
            },
        )

    project_context_fields = (
        "id",
        "name",
        "genre",
        "target_platform",
        "target_audience",
        "target_episode_count",
        "episode_duration_seconds",
        "locked_brief_id",
        "character_batch_id",
        "outline_batch_id",
    )

    def compatibility_signature(episode_number: int) -> str:
        item = prepared[episode_number]
        return canonical_request_hash(
            {
                "project": {
                    key: item["project"].get(key) for key in project_context_fields
                },
                "brief": item["batch_brief"],
                "characters": item["characters"],
                "relationships": item["relationships"],
                "prompt_snapshot": item["prompt_snapshot"],
            }
        )

    series_chunk_size = (
        BEATS_SERIES_CHUNK_SIZE if stage == "beats" else script_series_chunk_size
    )
    compatible_groups: list[list[int]] = []
    current_group: list[int] = []
    current_signature = ""
    pending_episode_numbers = [
        episode_number
        for episode_number in range(1, total + 1)
        if episode_number not in completed_resources
    ]
    for episode_number in pending_episode_numbers:
        signature = compatibility_signature(episode_number)
        if current_group and (
            len(current_group) >= series_chunk_size
            or signature != current_signature
            or episode_number != current_group[-1] + 1
        ):
            compatible_groups.append(current_group)
            current_group = []
        if not current_group:
            current_signature = signature
        current_group.append(episode_number)
    if current_group:
        compatible_groups.append(current_group)

    if stage == "beats":

        async def generate_beats_range(episode_numbers: list[int]) -> None:
            first_number = episode_numbers[0]
            last_number = episode_numbers[-1]
            first = prepared[first_number]
            try:
                rows, meta = await generate_episode_beats_chunk(
                    provider,
                    first["project"],
                    first["batch_brief"],
                    [prepared[number]["outline"] for number in episode_numbers],
                    first["characters"],
                    first["relationships"],
                    continuity_ledger=build_continuity_ledger(generated_beats),
                    prompt_snapshot=first["prompt_snapshot"],
                )
            except LLMOutputTruncatedError as exc:
                if len(episode_numbers) == 1:
                    raise LLMOutputTruncatedError(
                        f"第 {first_number} 集自由 Beats 生成仍达到模型输出上限；"
                        "请提高模型输出上限或减少单集字段内容",
                        finish_reason=exc.finish_reason,
                        returned_chars=exc.returned_chars,
                    ) from exc
                midpoint = len(episode_numbers) // 2
                left = episode_numbers[:midpoint]
                right = episode_numbers[midpoint:]
                emit(
                    job,
                    "progress",
                    f"第 {first_number}-{last_number} 集自由 Beats 输出达到上限，"
                    f"自动拆分为第 {left[0]}-{left[-1]} 集和"
                    f"第 {right[0]}-{right[-1]} 集",
                    level="warning",
                    payload={
                        "stage": stage,
                        "completed": len(episode_ids),
                        "total": total,
                        "split_start": first_number,
                        "split_end": last_number,
                        "left_start": left[0],
                        "left_end": left[-1],
                        "right_start": right[0],
                        "right_end": right[-1],
                        "finish_reason": exc.finish_reason,
                        "returned_chars": exc.returned_chars,
                    },
                )
                await generate_beats_range(left)
                await generate_beats_range(right)
                return
            for episode_number, generated in rows:
                save_episode(
                    episode_number,
                    generated,
                    meta,
                    batch_start=first_number,
                    batch_end=last_number,
                )

        for episode_numbers in compatible_groups:
            await _wait_if_paused(job, emit)
            await generate_beats_range(episode_numbers)
    else:
        continuity_beats = [
            {
                "episode_number": episode_number,
                "content": prepared[episode_number]["beats"],
            }
            for episode_number in range(1, total + 1)
        ]

        async def generate_script_group(
            episode_numbers: list[int],
        ) -> list[
            tuple[list[int], list[tuple[int, dict[str, Any]]], dict[str, Any]]
        ]:
            await _wait_if_paused(job, emit)
            first_number = episode_numbers[0]
            last_number = episode_numbers[-1]
            first = prepared[first_number]
            request_provider = _create_generation_provider(extra)
            job._llm_usage_providers.append(request_provider)
            try:
                rows, meta = await generate_episode_script_chunk(
                    request_provider,
                    first["project"],
                    first["batch_brief"],
                    [prepared[number]["outline"] for number in episode_numbers],
                    first["characters"],
                    first["relationships"],
                    {
                        number: prepared[number]["beats"]
                        for number in episode_numbers
                    },
                    continuity_ledger=build_continuity_ledger(
                        [
                            item
                            for item in continuity_beats
                            if int(item["episode_number"]) < first_number
                        ]
                    ),
                    prompt_snapshot=first["prompt_snapshot"],
                )
            except LLMOutputTruncatedError as exc:
                if len(episode_numbers) == 1:
                    raise LLMOutputTruncatedError(
                        f"第 {first_number} 集自由场次剧本生成仍达到模型输出上限；"
                        "请提高模型输出上限或减少单集正文长度",
                        finish_reason=exc.finish_reason,
                        returned_chars=exc.returned_chars,
                    ) from exc
                midpoint = len(episode_numbers) // 2
                left = episode_numbers[:midpoint]
                right = episode_numbers[midpoint:]
                emit(
                    job,
                    "progress",
                    f"第 {first_number}-{last_number} 集自由场次剧本输出达到上限，"
                    f"自动拆分为第 {left[0]}-{left[-1]} 集和"
                    f"第 {right[0]}-{right[-1]} 集",
                    level="warning",
                    payload={
                        "stage": stage,
                        "completed": len(episode_ids),
                        "total": total,
                        "split_start": first_number,
                        "split_end": last_number,
                        "left_start": left[0],
                        "left_end": left[-1],
                        "right_start": right[0],
                        "right_end": right[-1],
                        "finish_reason": exc.finish_reason,
                        "returned_chars": exc.returned_chars,
                    },
                )
                return [
                    *await generate_script_group(left),
                    *await generate_script_group(right),
                ]
            return [(episode_numbers, rows, meta)]

        async def persist_script_group(
            _episode_numbers: list[int],
            checkpoints: list[
                tuple[
                    list[int],
                    list[tuple[int, dict[str, Any]]],
                    dict[str, Any],
                ]
            ],
        ) -> None:
            for checkpoint_numbers, rows, meta in checkpoints:
                for episode_number, generated in rows:
                    save_episode(
                        episode_number,
                        generated,
                        meta,
                        batch_start=checkpoint_numbers[0],
                        batch_end=checkpoint_numbers[-1],
                    )

        def report_parallel_downgrade(exc: Exception) -> None:
            emit(
                job,
                "progress",
                "自由创作并行生成遇到模型限流或网络波动，已自动降为串行续跑",
                level="warning",
                payload={
                    "stage": stage,
                    "completed": len(episode_ids),
                    "total": total,
                    "script_batch_concurrency": 1,
                    "reason": str(exc),
                },
            )

        emit(
            job,
            "log",
            f"自由场次剧本将按每批 {series_chunk_size} 集、最多 "
            f"{SCRIPT_SERIES_MAX_CONCURRENCY} 批并行生成",
        )
        await run_ordered_script_windows(
            compatible_groups,
            generate=generate_script_group,
            persist=persist_script_group,
            on_downgrade=report_parallel_downgrade,
        )
    await _wait_if_paused(job, emit)
    emit(
        job,
        "job_finished",
        f"自由创作全部集{resource_label}生成完成",
        payload={
            **_usage_payload(
                job,
                {
                    "action": stage,
                    "series_generation": True,
                    "episode_count": total,
                    "episode_ids": episode_ids,
                },
            )
        },
    )


async def run_free_generation(
    job: CrawlJob,
    repository: CreativeRepository,
    emit: Callable[..., None],
) -> None:
    job._llm_usage_providers = []
    extra = dict(job.extra or {})
    action = str(extra.get("action") or "")
    stage = str(extra.get("normalized_stage") or "")
    project_id = int(extra.get("project_id") or 0)
    episode_number = (
        int(extra["episode_number"])
        if extra.get("episode_number") is not None
        else None
    )
    expected_head_revision = int(extra.get("expected_head_revision") or 0)
    context_id = str(extra.get("free_context_id") or "")
    actor_user_id = str(extra.get("created_by_user_id") or "")
    series_generation = bool(extra.get("series"))
    if action not in {"blueprints", "characters", "outlines", "beats", "script"}:
        raise ValueError("自由创作任务 action 无效")
    expected_stage = {
        "blueprints": "brief",
        "characters": "characters",
        "outlines": "outline",
        "beats": "beats",
        "script": "script",
    }[action]
    if stage != expected_stage or project_id < 1:
        raise ValueError("自由创作任务上下文无效")
    service = FreeCreativeModeService(repository)
    service.assert_free_project(project_id)
    if series_generation:
        if stage not in {"beats", "script"} or episode_number is not None:
            raise ValueError("自由创作全剧生成任务范围无效")
        await _run_free_episode_series(
            job,
            repository,
            service,
            emit,
            stage=stage,
            project_id=project_id,
            actor_user_id=actor_user_id,
            extra=extra,
        )
        return
    if not context_id:
        raise ValueError("自由创作任务上下文无效")
    context = service.get_generation_context(context_id)
    if int(context["project_id"]) != project_id or context["target_stage"] != stage:
        raise ValueError("自由创作任务上下文与目标不匹配")
    if episode_number != context.get("episode_number"):
        raise ValueError("自由创作任务集数与上下文不匹配")
    project = repository.get_project(project_id) or {}
    snapshot = dict(context["context_snapshot"])
    prompt_snapshot = snapshot.get("prompt_snapshot")
    prompt_ab_experiment_id = str(
        extra.get("prompt_ab_experiment_id") or ""
    ).strip()
    prompt_ab_arm = str(extra.get("prompt_ab_arm") or "").strip()
    prompt_ab_source_fingerprint = str(
        extra.get("prompt_ab_source_fingerprint") or ""
    ).strip()
    select_as_head = not bool(prompt_ab_experiment_id)
    prompt_kwargs = (
        {"prompt_snapshot": dict(prompt_snapshot)}
        if isinstance(prompt_snapshot, dict)
        else {}
    )
    project.update(snapshot.get("project") or {})
    provider = _create_generation_provider(extra)
    job._llm_usage_providers.append(provider)
    meta_base = {
        **_llm_generation_meta(
            provider, str(extra.get("llm_model_record_id") or "")
        ),
        "job_id": job.job_id,
        "operation_id": str(extra.get("operation_id") or ""),
        "generation_context_id": context_id,
        "generation_context_hash": context["context_hash"],
        "prompt_template_version": context.get("prompt_schema_version") or "",
        "resource_scope": "free",
        "prompt_source": "template" if isinstance(prompt_snapshot, dict) else "system",
        "prompt_ab_experiment_id": prompt_ab_experiment_id,
        "prompt_ab_arm": prompt_ab_arm,
        "prompt_ab_source_fingerprint": prompt_ab_source_fingerprint,
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
    source_refs = dict(context.get("context_manifest") or {})
    emit(job, "job_started", f"开始自由创作：{stage}")
    emit(job, "progress", "准备自由创作模型请求", payload={"stage": stage, "completed": 0, "total": 1})

    if stage == "brief":
        count = int(extra.get("count", 1))
        if not 1 <= count <= 5:
            raise ValueError("单次自由 Brief 生成数量必须在 1-5 之间")
        seed = dict(snapshot["seed"])
        seed["constraints"] = {
            **dict(seed.get("constraints") or {}),
            "generation_instruction": str(snapshot.get("instruction") or ""),
        }
        generated, meta = await generate_original_directions(
            provider,
            project,
            seed,
            [],
            [],
            count,
            prompt_snapshot=(
                dict(prompt_snapshot)
                if isinstance(prompt_snapshot, dict)
                else None
            ),
        )
        if len(generated) != count:
            raise ValueError(f"自由 Brief 生成数量不足或超出请求：期望 {count}，实际 {len(generated)}")
        result = service.create_briefs(
            project_id,
            [dict(item.get("content") or item) for item in generated],
            expected_head_revision=expected_head_revision,
            actor_user_id=actor_user_id,
            source_refs=source_refs,
            generation_meta={**meta, **meta_base},
            select_as_head=select_as_head,
        )
        result_payload = {
            "action": action,
            "blueprint_id": result["resources"][0]["id"],
            "blueprint_ids": [item["id"] for item in result["resources"]],
        }
    else:
        brief, characters, relationships, outline, beats = _prompt_inputs(
            context, episode_number
        )
        if stage == "characters":
            generated, meta = await generate_character_bundle(
                provider, project, brief, **prompt_kwargs
            )
            result = service.create_characters(
                project_id,
                generated["characters"],
                generated["relationships"],
                expected_head_revision=expected_head_revision,
                actor_user_id=actor_user_id,
                source_refs=source_refs,
                generation_meta={**meta, **meta_base},
                select_as_head=select_as_head,
            )
            result_payload = {"action": action, "batch_id": result["resource"]["batch_id"]}
        elif stage == "outline":
            def progress(done: int, total: int) -> None:
                emit(
                    job,
                    "progress",
                    f"已生成 {done}/{total} 集自由大纲",
                    payload={"stage": stage, "completed": done, "total": total},
                )

            generated, meta = await generate_outline_bundle(
                provider,
                project,
                brief,
                characters,
                relationships,
                on_progress=progress,
                reasoning_effort=str(
                    getattr(provider, "reasoning_effort", "") or ""
                ),
                **prompt_kwargs,
            )
            result = service.create_outline(
                project_id,
                generated["episodes"],
                series_arc=generated["series_arc"],
                series_overview=generated.get("series_overview"),
                expected_head_revision=expected_head_revision,
                actor_user_id=actor_user_id,
                source_refs=source_refs,
                generation_meta={**meta, **meta_base, "stages": generated.get("stages", [])},
                select_as_head=select_as_head,
            )
            result_payload = {
                "action": action,
                "batch_id": result["resource"]["batch_id"],
                "episode_count": len(result["resource"]["episodes"]),
            }
        elif stage == "beats":
            if episode_number is None:
                raise ValueError("自由 Beats 生成缺少集数")
            generated, meta = await generate_episode_beats(
                provider,
                project,
                outline,
                characters,
                episode_number,
                brief=brief,
                relationships=relationships,
                **prompt_kwargs,
            )
            result = service.create_episode_resource(
                project_id,
                "beats",
                episode_number,
                generated,
                expected_head_revision=expected_head_revision,
                actor_user_id=actor_user_id,
                source_refs=source_refs,
                generation_meta={**meta, **meta_base},
                select_as_head=select_as_head,
            )
            result_payload = {"action": action, "episode_id": result["resource"]["id"], "episode_number": episode_number}
        else:
            if episode_number is None:
                raise ValueError("自由场次剧本生成缺少集数")
            generated, meta = await generate_episode_script(
                provider,
                project,
                outline,
                characters,
                beats,
                episode_number,
                brief=brief,
                relationships=relationships,
                **prompt_kwargs,
            )
            result = service.create_episode_resource(
                project_id,
                "script",
                episode_number,
                generated,
                expected_head_revision=expected_head_revision,
                actor_user_id=actor_user_id,
                source_refs=source_refs,
                generation_meta={**meta, **meta_base},
                select_as_head=select_as_head,
            )
            result_payload = {"action": action, "episode_id": result["resource"]["id"], "episode_number": episode_number}

    emit(job, "progress", "自由创作生成完成", payload={"stage": stage, "completed": 1, "total": 1})
    emit(
        job,
        "job_finished",
        "自由创作内容生成完成",
        payload=_usage_payload(job, result_payload),
    )
