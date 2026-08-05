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
import json
from pathlib import Path
from typing import Any

import config

from analysis.llm import create_llm_provider_from_config
from core.events import emit_event
from core.job import CrawlJob
from creative.imitation_service import generate_blueprints
from creative.repository import CreativeRepository
from creative.script_service import (
    build_continuity_ledger,
    generate_character_bundle,
    generate_episode_beats,
    generate_episode_beats_chunk,
    generate_episode_script,
    generate_episode_script_chunk,
    generate_outline_bundle,
)


def create_provider():
    return create_llm_provider_from_config(config)


def emit(job: CrawlJob, event: str, message: str, level: str = "info", payload: dict[str, Any] | None = None) -> None:
    emit_event(event, job.job_id, "creative-phase4-worker", "creative", level, message, payload or {})


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


def _continuity_ledger_before(project: dict[str, Any], episode_number: int) -> list[dict[str, Any]]:
    latest_by_episode: dict[int, dict[str, Any]] = {}
    for item in project["episodes"]:
        if (
            item["stage"] != "beats"
            or item.get("status") not in {"locked", "approved"}
            or int(item["episode_number"]) >= episode_number
        ):
            continue
        number = int(item["episode_number"])
        current = latest_by_episode.get(number)
        if current is None or (int(item.get("version") or 0), int(item["id"])) > (
            int(current.get("version") or 0), int(current["id"])
        ):
            latest_by_episode[number] = item
    return build_continuity_ledger(list(latest_by_episode.values()))


def _continuity_ledger_through(project: dict[str, Any], episode_number: int) -> list[dict[str, Any]]:
    latest_by_episode: dict[int, dict[str, Any]] = {}
    for item in project["episodes"]:
        if (
            item["stage"] != "beats"
            or item.get("status") not in {"locked", "approved"}
            or int(item["episode_number"]) > episode_number
        ):
            continue
        number = int(item["episode_number"])
        current = latest_by_episode.get(number)
        if current is None or (int(item.get("version") or 0), int(item["id"])) > (
            int(current.get("version") or 0), int(current["id"])
        ):
            latest_by_episode[number] = item
    return build_continuity_ledger(list(latest_by_episode.values()))


async def run(job: CrawlJob) -> None:
    extra = job.extra or {}
    action = str(extra.get("action") or "").strip()
    project_id = int(extra.get("project_id") or 0)
    db_path = Path(str(extra.get("db_path") or ".runtime/creative/creative.db"))
    if action not in {"blueprints", "characters", "outlines", "beats", "script"} or project_id < 1:
        raise ValueError("Phase 4 任务缺少有效 action 或 project_id")

    repository = CreativeRepository(db_path)
    inputs = repository.get_project_inputs(project_id)
    project = inputs["project"]
    provider = create_provider()
    series_generation = bool(extra.get("series")) and action in {"beats", "script"}
    total_episodes = int(project.get("target_episode_count") or 0) if series_generation else 1
    retry_completed = 0

    def report_retry(retry_number: int, max_retries: int, reason: str) -> None:
        emit(
            job,
            "progress",
            f"模型服务暂时不可用，正在进行第 {retry_number}/{max_retries} 次自动重试：{reason}",
            "warning",
            {
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
        generated, meta = await generate_blueprints(provider, project, inputs["topic"], inputs["breakdown"], count)
        resources = repository.create_blueprints(project_id, generated, job.job_id, meta)
        result: dict[str, Any] = {"action": action, "blueprint_ids": [item["id"] for item in resources]}
    elif action == "characters":
        brief = _locked_brief(project)
        generated, meta = await generate_character_bundle(provider, project, brief)
        bundle = repository.create_character_batch(
            project_id,
            generated["characters"],
            generated["relationships"],
            job.job_id,
            meta,
        )
        result = {"action": action, "batch_id": bundle["batch_id"]}
    elif action == "outlines":
        brief = _locked_brief(project)
        characters, relationships = _locked_characters(project)
        resumable = repository.find_resumable_outline_batch(project_id)
        outline_batch_id: str | None = resumable["batch_id"] if resumable else None
        resume_episodes = [item["content"] for item in resumable["episodes"]] if resumable else None
        resume_series_arc = resumable["series_arc"] if resumable else None
        resume_stages = resumable["stages"] if resumable else None
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

        def save_outline_checkpoint(
            arc: dict[str, Any], episodes: list[dict[str, Any]], meta: dict[str, Any]
        ) -> None:
            nonlocal outline_batch_id
            saved = repository.save_outline_batch_checkpoint(
                project_id,
                episodes,
                str(arc["series_arc"]),
                job.job_id,
                {
                    **meta,
                    "stages": arc["stages"],
                    "checkpoint_episode_count": len(episodes),
                },
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                batch_id=outline_batch_id,
            )
            outline_batch_id = saved["batch_id"]

        def progress(done: int, total: int) -> None:
            emit(
                job,
                "progress",
                f"已生成 {done}/{total} 集大纲",
                payload={"stage": action, "completed": done, "total": total},
            )

        generated, meta = await generate_outline_bundle(
            provider,
            project,
            brief,
            characters,
            relationships,
            on_progress=progress,
            on_checkpoint=save_outline_checkpoint,
            checkpoint=lambda: wait_if_paused(job),
            resume_episodes=resume_episodes,
            resume_series_arc=resume_series_arc,
            resume_stages=resume_stages,
        )
        if outline_batch_id is None:
            raise RuntimeError("分集大纲未创建任何生成检查点")
        bundle = repository.get_outline_batch(project_id, outline_batch_id)
        result = {"action": action, "batch_id": bundle["batch_id"], "episode_count": len(bundle["episodes"])}
    elif series_generation:
        brief = _locked_brief(project)
        characters, relationships = _locked_characters(project)
        if action == "script":
            missing_beats = repository.missing_current_locked_beats(project_id)
            if missing_beats:
                raise ValueError(f"全剧场次剧本生成前必须锁定全部 Beats；未锁定集数：{','.join(map(str, missing_beats))}")
        resumable = repository.find_resumable_episode_series(project_id, action)
        series_batch_id: str | None = resumable["batch_id"] if resumable else None
        completed = int(resumable["completed_episode_count"]) if resumable else 0
        generated_beats = list(resumable["episodes"]) if resumable and action == "beats" else []
        if completed:
            emit(job, "log", f"接续已保存的{action}草稿：从第 {completed + 1} 集继续")
            emit(
                job, "progress", f"已恢复 {completed}/{total_episodes} 集",
                payload={"stage": action, "completed": completed, "total": total_episodes},
            )
        for start in range(completed + 1, total_episodes + 1, 10):
            end = min(total_episodes, start + 9)
            retry_completed = start - 1
            await wait_if_paused(job)
            outlines = [
                item for item in project["episodes"]
                if item["stage"] == "outline"
                and item.get("batch_id") == project.get("outline_batch_id")
                and start <= int(item["episode_number"]) <= end
                and item.get("status") in {"locked", "approved"}
            ]
            if sorted(int(item["episode_number"]) for item in outlines) != list(range(start, end + 1)):
                raise ValueError(f"未找到第 {start}-{end} 集的锁定大纲")
            if action == "beats":
                prior = [
                    item for item in project["episodes"]
                    if item["stage"] == "beats"
                    and item.get("status") in {"locked", "approved"}
                    and int(item["episode_number"]) < start
                ] + generated_beats
                checkpoint_items, meta = await generate_episode_beats_chunk(
                    provider, project, brief, outlines, characters, relationships,
                    continuity_ledger=build_continuity_ledger(prior),
                )
            else:
                beats_by_episode = {
                    episode_number: repository._locked_beats_for_episode(project, episode_number)["content"]
                    for episode_number in range(start, end + 1)
                }
                checkpoint_items, meta = await generate_episode_script_chunk(
                    provider, project, brief, outlines, characters, relationships, beats_by_episode,
                    continuity_ledger=_continuity_ledger_through(project, start - 1),
                )
            saved = repository.save_episode_generation_checkpoint(
                project_id, action, checkpoint_items, job.job_id,
                {
                    **meta,
                    "series_generation": True,
                    "chunk_size": 10,
                    "checkpoint_start": start,
                    "checkpoint_end": end,
                    "checkpoint_episode_count": end,
                },
                workflow_task_id=str(extra.get("workflow_task_id") or "") or None,
                batch_id=series_batch_id,
            )
            series_batch_id = saved[0]["batch_id"]
            if action == "beats":
                generated_beats.extend(saved)
            emit(
                job, "progress", f"已生成并保存第 {start}-{end} 集{'Beats' if action == 'beats' else '场次剧本'}",
                payload={"stage": action, "completed": end, "total": total_episodes, "chunk_start": start, "chunk_end": end},
            )
        result = {
            "action": action, "series_generation": True, "batch_id": series_batch_id,
            "episode_count": total_episodes, "resumed_from_episode": completed,
            "brief_id": brief["id"],
        }
    else:
        brief = _locked_brief(project)
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
                continuity_ledger=_continuity_ledger_before(project, episode_number),
                brief=brief,
                relationships=relationships,
            )
            resource = repository.create_episode_beats(project_id, episode_number, generated, job.job_id, meta)
        else:
            beats = repository._locked_beats_for_episode(project, episode_number)
            generated, meta = await generate_episode_script(
                provider,
                project,
                outline,
                characters,
                beats["content"],
                episode_number,
                continuity_ledger=_continuity_ledger_through(project, episode_number),
                brief=brief,
                relationships=relationships,
            )
            resource = repository.create_episode_script(project_id, episode_number, generated, job.job_id, meta)
        result = {"action": action, "episode_id": resource["id"], "episode_number": episode_number, "brief_id": brief["id"]}

    await wait_if_paused(job)
    target_dir = Path("data") / "creative" / "jobs" / job.job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / "generation.json"
    report_path.write_text(
        json.dumps({"job_id": job.job_id, "project_id": project_id, **result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    emit(job, "artifact", "Phase 4 生成记录", payload={"path": str(report_path), "artifact_type": "json"})
    emit(job, "progress", "生成完成", payload={"stage": action, "completed": total_episodes, "total": total_episodes})
    emit(job, "job_finished", "创作内容生成完成", payload=result)


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
        emit(job, "job_failed", str(exc), "error")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
