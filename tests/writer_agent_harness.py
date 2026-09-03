# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Deterministic HTTP acceptance harness for Writer Agent project creation."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
from typing import Any, Iterator
from urllib.parse import quote
import uuid

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.engine import make_url

from core.job import CrawlJob
from creative import phase4_worker
from creative.application_service import CreativeApplicationService
from creative.market_reference_service import MarketReferenceService
from creative.original_project_service import OriginalProjectService
from creative.repository import CreativeRepository
from creative.schema import OriginalProjectCreateRequest
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.dependencies import current_principal
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from system_database import SystemDatabase, SystemDatabaseSettings
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService
from writer_agent import api as writer_agent_api
from writer_agent.action_service import ActionService
from writer_agent.orchestrator import WriterProjectOrchestrator
from writer_agent.runner import WriterActionRunner
from writer_agent.store import WriterAgentStore


class DeterministicCreativeProvider:
    """Supplies both chat cards and Phase 4 payloads without network access."""

    def brief(self) -> dict[str, Any]:
        return {
            "title": "情绪公司", "logline": "公司按情绪积分发工资，林岚带同事反抗",
            "theme": "人在制度中保持选择", "protagonist": "林岚",
            "protagonist_desire": "夺回尊严", "protagonist_flaw": "独自承担",
            "pressure_response": "学会组织同伴", "main_opposition": "情绪考核系统",
            "core_conflict": "工资被情绪评分支配", "main_goal": "废除情绪积分",
            "background": "近未来职场", "worldview": "情绪不能定义价值",
            "story_engine": "每集破解一条情绪考核规则", "first_three_hooks": ["工资清零"],
            "highlights": ["规则反杀"], "differentiators": ["情绪劳动具象化"],
            "audience": "18-35岁", "market_fit": "强设定短剧", "cost_level": "low",
            "originality_statement": "从原创种子独立发展", "risks": ["设定解释成本"],
            "assumptions": ["观众理解情绪积分"], "open_questions": ["制度来源"],
        }

    def characters(self) -> dict[str, Any]:
        return {
            "characters": [
                {"name": "林岚", "age_range": "28-32", "identity": "数据分析师",
                 "external_tags": ["冷静", "倔强"], "desire": "夺回工资解释权",
                 "secret": "曾协助设计评分模型", "growth_arc": "从独行到组织",
                 "dialogue_style": "克制直接", "appearance_prompt": "职业装",
                 "voice_prompt": "冷静坚定", "action_goal": "公开评分漏洞",
                 "fear_of_loss": "失去同伴", "behavioral_contradiction": "嘴硬心软",
                 "pressure_response": "主动求助", "bottom_line": "不牺牲无辜同事"},
                {"name": "周衡", "age_range": "35-40", "identity": "人事主管",
                 "external_tags": ["严谨", "焦虑"], "desire": "维护系统",
                 "secret": "家人也受系统伤害", "growth_arc": "从回避到承担",
                 "dialogue_style": "官腔克制", "appearance_prompt": "深色西装",
                 "voice_prompt": "低沉压抑", "action_goal": "压住危机",
                 "fear_of_loss": "失去职位", "behavioral_contradiction": "维护规则却同情受害者",
                 "pressure_response": "推诿拖延", "bottom_line": "不伤害家人"},
            ],
            "relationships": [{"from": "林岚", "to": "周衡", "relation": "上下级对抗",
                               "conflict": "规则与人的冲突", "change_arc": "从对抗到共同反思"}],
        }

    def outline(self, count: int) -> dict[str, Any]:
        return {
            "series_arc": "林岚从工资清零到带领同事夺回情绪解释权",
            "stages": [{"name": "反抗", "episodes": [1, count]}],
            "episodes": [self._outline_episode(number) for number in range(1, count + 1)],
        }

    @staticmethod
    def _outline_episode(number: int) -> dict[str, Any]:
        return {
            "episode": number, "core_event": f"第{number}集揭开一条评分规则",
            "conflict": "林岚与系统正面冲突", "character_progress": "林岚学会求助",
            "highlight": "评分反噬", "reversal": "盟友身份暴露",
            "cliffhanger": "新的工资危机", "estimated_seconds": 120,
        }

    @staticmethod
    def beats(episode: int) -> dict[str, Any]:
        return {"beats": [{
            "order": 1, "scene": "办公室", "characters": ["林岚"],
            "goal": "查明评分", "obstacle": "系统封锁", "emotion_change": "恐惧转为决心",
            "turn": "发现漏洞", "hook": "工资再次清零", "state_change": "掌握证据",
            "cost": "失去奖金", "opposition_tactic": "周衡施压",
            "continuity_updates": [{"kind": "evidence", "name": "评分漏洞", "event": f"第{episode}集发现", "holder": "林岚", "location": "办公室", "known_by": ["林岚"], "status": "active"}],
        }]}

    @staticmethod
    def script(episode: int) -> dict[str, Any]:
        return {"episode_title": f"第{episode}集", "scenes": [{
            "sequence_no": 1, "scene_no": 1, "time_of_day": "日", "interior_exterior": "内",
            "location": "办公室", "props": [], "characters": ["林岚"],
            "blocks": [{"type": "action", "text": "林岚查看情绪积分。"}],
        }]}

    async def complete_json(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        text = str(messages)
        system_prompt = next(
            (
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "system"
            ),
            "",
        )
        user = next(
            (
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        try:
            round_message = str(json.loads(user).get("本轮问题") or "")
        except (TypeError, ValueError, json.JSONDecodeError):
            round_message = user
        is_writer_chat = any(
            "你是短剧/漫剧编剧大师" in str(message.get("content") or "")
            for message in messages
        )
        if is_writer_chat and "剧本" in round_message and "创建项目" in round_message:
            return {
                "reply": "已整理创建项目并生成剧本的计划",
                "cards": [{
                    "card_id": "brief-script-1",
                    "card_type": "brief",
                    "content": self.brief(),
                    "assumptions": ["低成本"],
                }],
                "proposed_actions": [{
                    "action_type": "project.create_original",
                    "title": "创建原创项目并生成分集剧本",
                    "source_card_id": "brief-script-1",
                    "target_stage": "script",
                    "episode_start": 1,
                    "episode_end": 5,
                    "payload": {
                        "seed_text": "未来公司按照员工情绪发工资",
                        "name": "情绪公司",
                        "genre": "都市奇幻",
                        "target_platform": "hg",
                        "target_audience": "18-35岁",
                        "target_episode_count": 5,
                        "episode_duration_seconds": 120,
                        "cost_level": "low",
                        "content_boundaries": [],
                        "reference_refs": [],
                    },
                    "assumptions": ["低成本"],
                }],
            }
        if is_writer_chat and "剧本" in round_message:
            return {
                "reply": "已整理剧本生成计划",
                "proposed_actions": [{
                    "action_type": "creative.run_plan", "title": "生成分集剧本",
                    "target_stage": "script", "episode_start": 1, "episode_end": 5,
                    "payload": {}, "assumptions": ["沿用已锁定上游成果"],
                }],
            }
        beats_match = re.search(
            r"为第\s*(\d+)\s*集生成 Beats。顶层必须返回 JSON 对象",
            system_prompt,
        )
        if beats_match:
            return self.beats(int(beats_match.group(1)))
        beats_chunk_match = re.search(
            r"批量生成第\s*(\d+)\s*[-–]\s*(\d+)\s*集 Beats。顶层必须返回",
            system_prompt,
        )
        if beats_chunk_match:
            start, end = (int(value) for value in beats_chunk_match.groups())
            return {
                "episodes": [
                    {"episode": episode, **self.beats(episode)}
                    for episode in range(start, end + 1)
                ]
            }
        script_match = re.search(
            r"根据第\s*(\d+)\s*集已锁定 Beats 生成可拍摄的场次剧本",
            system_prompt,
        )
        if script_match:
            return self.script(int(script_match.group(1)))
        if "人物卡和人物关系" in text:
            return self.characters()
        if "汇总全集剧情概览" in text:
            return {"series_overview": {
                "core_event": "林岚公开情绪评分漏洞", "character_progress": "从独行到组织同事",
                "conflict": "员工与情绪考核系统对抗", "reversal": "周衡承认系统伤害",
                "highlight": "评分规则被反向利用", "cliffhanger": "制度幕后人现身",
            }}
        if "规划全剧阶段弧线" in text or "只返回 series_arc 对象" in text:
            return {
                "series_arc": "林岚从工资清零到带领同事夺回情绪解释权",
                "stages": [{"name": "反抗", "episodes": [1, 20]}],
                "series_overview": {
                    "core_event": "林岚公开情绪评分漏洞", "character_progress": "从独行到组织同事",
                    "conflict": "员工与情绪考核系统对抗", "reversal": "周衡承认系统伤害",
                    "highlight": "评分规则被反向利用", "cliffhanger": "制度幕后人现身",
                },
            }
        if "分集大纲" in text and "estimated_seconds" in text:
            chinese_range = re.findall(r"第\s*(\d+)\s*-\s*(\d+)\s*集", text)
            if chinese_range:
                start, end = (int(value) for value in chinese_range[-1])
            else:
                numbers = [
                    int(value)
                    for value in re.findall(
                        r"(?:episode_start|start)['\"]?\s*[:：]\s*(\d+)",
                        text,
                    )
                ]
                start = numbers[-1] if numbers else 1
                end_match = re.findall(
                    r"(?:episode_end|end)['\"]?\s*[:：]\s*(\d+)", text
                )
                end = int(end_match[-1]) if end_match else start + 9
            return {
                "episodes": [
                    self._outline_episode(number) for number in range(start, end + 1)
                ]
            }
        if "给我5个不同方向" in text and "采用第2个" not in text:
            dimensions = [
                ("林岚", "未来公司", "情绪考核"), ("周屿", "海上社区", "资源配给"),
                ("许真", "虚拟学校", "人格评分"), ("唐青", "夜班医院", "记忆交易"),
                ("沈越", "直播工厂", "情绪竞价"),
            ]
            return {"reply": "五个方向如下", "cards": [{"card_id": "directions-1", "card_type": "directions", "items": [
                {"title": f"方向{index}", "protagonist": item[0], "background": item[1],
                 "core_conflict": item[2], "relationship_engine": "同事联盟", "emotional_payoff": "夺回尊严",
                 "difference_dimensions": ["protagonist", "background", "conflict"]}
                for index, item in enumerate(dimensions, 1)
            ]}]}
        if any(token in text for token in ("创建20集项目", "跳过方向比较", "基于全部材料")):
            return {
                "reply": "已整理创作计划",
                "cards": [{"card_id": "brief-1", "card_type": "brief", "content": self.brief(), "assumptions": ["低成本"]}],
                "proposed_actions": [{
                    "action_type": "project.create_original", "title": "创建原创项目并创作",
                    "source_card_id": "brief-1", "target_stage": "outline",
                    "payload": {"seed_text": "未来公司按照员工情绪发工资", "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg", "target_audience": "18-35岁", "target_episode_count": 20, "episode_duration_seconds": 120, "cost_level": "low", "content_boundaries": [], "reference_refs": []},
                    "assumptions": ["低成本"],
                }],
            }
        if "人物关系" in text or "角色关系" in text:
            return self.characters()
        if "阶段弧" in text or "series_arc" in text:
            return {"series_arc": "反抗情绪制度", "stages": [{"name": "反抗", "episodes": [1, 20]}]}
        if "分集大纲" in text or "episodes" in text and "estimated_seconds" in text:
            numbers = [int(value) for value in re.findall(r"(?:episode_start|start)['\"]?\s*[:：]\s*(\d+)", text)]
            start = numbers[-1] if numbers else 1
            end_match = re.findall(r"(?:episode_end|end)['\"]?\s*[:：]\s*(\d+)", text)
            end = int(end_match[-1]) if end_match else start + 9
            return {"episodes": [self._outline_episode(number) for number in range(start, end + 1)]}
        if "Beats" in text or "节拍" in text:
            episode = int(re.findall(r"episode_number[^0-9]*(\d+)", text)[-1]) if re.findall(r"episode_number[^0-9]*(\d+)", text) else 1
            return self.beats(episode)
        if "场次剧本" in text or "episode_title" in text:
            episode_match = re.findall(r"episode_number[^0-9]*(\d+)", text)
            return self.script(int(episode_match[-1]) if episode_match else 1)
        target_script = "剧本" in round_message
        if target_script:
            return {
                "reply": "已整理剧本生成计划",
                "proposed_actions": [{
                    "action_type": "creative.run_plan", "title": "生成分集剧本",
                    "target_stage": "script", "episode_start": 1, "episode_end": 5,
                    "payload": {}, "assumptions": ["沿用已锁定上游成果"],
                }],
            }
        return {
            "reply": "已整理创作计划", "cards": [{"card_id": "brief-1", "card_type": "brief", "content": self.brief(), "assumptions": ["低成本"]}],
            "proposed_actions": [{
                "action_type": "project.create_original" if "创建项目" in round_message or "Brief" in round_message or "大纲" in round_message else "creative.run_plan",
                "title": "创建原创项目并创作", "source_card_id": "brief-1",
                "target_stage": "script" if target_script else "outline",
                "episode_start": 1 if target_script else None, "episode_end": 5 if target_script else None,
                "payload": ({"seed_text": "未来公司按照员工情绪发工资", "name": "情绪公司", "genre": "都市奇幻", "target_platform": "hg", "target_audience": "18-35岁", "target_episode_count": 20, "episode_duration_seconds": 120, "cost_level": "low", "content_boundaries": [], "reference_refs": []} if "创建项目" in round_message or "Brief" in round_message or "大纲" in round_message else {}),
                "assumptions": ["低成本"],
            }],
        }


class DeterministicJobManager:
    def __init__(self, provider: DeterministicCreativeProvider) -> None:
        self.provider = provider
        self.jobs: dict[str, dict[str, Any]] = {}
        self._by_operation: dict[str, str] = {}
        self._fail_once: set[str] = set()
        self.status_history: dict[str, list[str]] = {}
        self.job_created = threading.Event()
        self.wake = lambda: None

    def fail_once(self, step_key: str) -> None:
        self._fail_once.add(step_key)

    def get_or_create_job(self, operation_id: str, payload: dict[str, Any], **_: Any) -> SimpleNamespace:
        existing = self._by_operation.get(operation_id)
        retry = bool(payload.get("extra", {}).get("retry_failed_operation"))
        if existing and not (
            retry and self.jobs[existing]["status"] in {"failed", "cancelled"}
        ):
            return SimpleNamespace(job_id=existing, status=self.jobs[existing]["status"])
        return self._create(operation_id, payload)

    def create_job(self, payload: dict[str, Any]) -> SimpleNamespace:
        return self._create(str(payload.get("extra", {}).get("operation_id") or uuid.uuid4().hex), payload)

    def _create(self, operation_id: str, payload: dict[str, Any]) -> SimpleNamespace:
        job_id = f"deterministic-{len(self.jobs) + 1}"
        self.jobs[job_id] = {"job_id": job_id, "status": "pending", "payload": payload}
        self.status_history[job_id] = ["pending"]
        self._by_operation[operation_id] = job_id
        self.job_created.set()
        return SimpleNamespace(job_id=job_id, status="pending")

    def find_job_by_operation(self, operation_id: str) -> dict[str, Any] | None:
        job_id = self._by_operation.get(operation_id)
        return self.jobs.get(job_id) if job_id else None

    def get_job(self, job_id: str) -> dict[str, Any]:
        return dict(self.jobs[job_id])

    def stop(self, job_id: str) -> None:
        self._set_status(self.jobs[job_id], "cancelled")

    def _set_status(self, job: dict[str, Any], status: str) -> None:
        job["status"] = status
        history = self.status_history.setdefault(job["job_id"], [])
        if not history or history[-1] != status:
            history.append(status)

    def advance(self) -> bool:
        terminal_changed = False
        # Advancing a terminal job wakes the runner, which can enqueue the
        # next job concurrently.  Iterate over a stable snapshot so one tick
        # models a scheduler cycle instead of observing a mutable dict view.
        for job in list(self.jobs.values()):
            if job["status"] == "pending":
                self._set_status(job, "running")
                continue
            if job["status"] != "running":
                continue
            extra = job["payload"]["extra"]
            key = f"{extra.get('normalized_stage')}.generate:{extra.get('episode_number')}"
            if key in self._fail_once:
                self._fail_once.remove(key)
                self._set_status(job, "failed")
                terminal_changed = True
                continue
            try:
                data = dict(job["payload"])
                data["job_id"] = job["job_id"]
                asyncio.run(phase4_worker.run(CrawlJob.from_dict(data)))
            except Exception as exc:
                job["error"] = repr(exc)
                self._set_status(job, "failed")
                terminal_changed = True
            else:
                self._set_status(job, "succeeded")
                terminal_changed = True
        self.wake()
        return terminal_changed


class _SimulatedRunnerStop(Exception):
    """End one in-process runner without terminalizing its durable action."""


class _RestartableWriterActionRunner(WriterActionRunner):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stop_requested = threading.Event()
        self.stopped = threading.Event()
        self.refreshed = threading.Event()

    def request_process_stop(self) -> None:
        self._stop_requested.set()
        self._poll_event.set()

    def _refresh_job_steps(self, action_id: str, actor: Principal) -> dict[str, Any]:
        if self._stop_requested.is_set():
            raise _SimulatedRunnerStop
        action = super()._refresh_job_steps(action_id, actor)
        self.refreshed.set()
        return action

    def run(self, action_id: str, actor: Principal) -> None:
        try:
            super().run(action_id, actor)
        except _SimulatedRunnerStop:
            return
        finally:
            if self._stop_requested.is_set():
                self.stopped.set()


class WriterAgentE2EHarness:
    creative_repository: CreativeRepository
    creative_service: CreativeApplicationService
    original_project_service: OriginalProjectService
    store: WriterAgentStore
    actor: Principal
    provider: DeterministicCreativeProvider
    job_manager: DeterministicJobManager
    workflow_repository: WorkflowRepository
    database: SystemDatabase
    orchestrator: WriterProjectOrchestrator
    runner: _RestartableWriterActionRunner
    action_service: ActionService
    job_bound: threading.Event

    @classmethod
    def create(cls, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "WriterAgentE2EHarness":
        postgres_url = os.environ.get("AIWORKER_TEST_SYSTEM_DATABASE_URL")
        if not postgres_url:
            pytest.skip("AIWORKER_TEST_SYSTEM_DATABASE_URL is required")
        if make_url(postgres_url).database == "aiworker_system":
            raise ValueError("tests must not run against production")
        monkeypatch.setenv("AIWORKER_SYSTEM_DATABASE_URL", postgres_url)
        command.upgrade(Config("alembic.ini"), "head")
        harness = cls()
        harness.database = SystemDatabase(SystemDatabaseSettings(postgres_url))
        identity = IdentityRepository(harness.database)
        identity.seed_defaults()
        harness.workflow_repository = WorkflowRepository(identity)
        harness.creative_repository = CreativeRepository(tmp_path / "creative.db")
        sessions = SessionService(identity, secure_cookie=False)
        permissions = PermissionService(identity, sessions)
        user = identity.create_user(f"writer-e2e-{uuid.uuid4().hex}", "编剧验收", "hash")
        screenwriter = identity.get_role_by_code("screenwriter")
        assert screenwriter is not None
        identity.replace_user_roles(user["id"], [screenwriter["id"]])
        harness.actor = permissions.principal_for_user(user["id"], "writer-e2e")
        assert harness.actor is not None
        adapter = CreativeWorkflowAdapter(harness.creative_repository, harness.workflow_repository)
        transitions = TransitionService(harness.workflow_repository, creative_adapter=adapter)
        harness.original_project_service = OriginalProjectService(harness.creative_repository, harness.workflow_repository, MarketReferenceService(harness.creative_repository))
        harness.provider = DeterministicCreativeProvider()
        harness.job_manager = DeterministicJobManager(harness.provider)
        harness.creative_service = CreativeApplicationService(harness.creative_repository, harness.workflow_repository, transitions, harness.job_manager, tmp_path, harness.original_project_service, adapter, permissions)
        harness.store = WriterAgentStore(tmp_path / "writer-agent.db")
        harness.job_bound = threading.Event()
        harness.action_changed = threading.Event()
        assign_step_job = harness.store.assign_step_job

        def record_bound_job(
            action_id: str,
            step_key: str,
            job_id: str,
            *,
            execution_owner: str | None = None,
        ) -> dict[str, Any]:
            step = assign_step_job(
                action_id,
                step_key,
                job_id,
                execution_owner=execution_owner,
            )
            harness.job_bound.set()
            harness.action_changed.set()
            return step

        harness.store.assign_step_job = record_bound_job
        for method_name in (
            "attach_project_to_action",
            "complete_step_with_context",
            "fail_action",
            "fail_step",
            "finish_action",
            "prepare_action_finalization",
            "publish_action_finalization",
            "record_action_result",
        ):
            original = getattr(harness.store, method_name)

            def mark_action_change(
                *args: Any,
                _original: Any = original,
                **kwargs: Any,
            ) -> Any:
                result = _original(*args, **kwargs)
                harness.action_changed.set()
                return result

            setattr(harness.store, method_name, mark_action_change)
        harness.orchestrator = WriterProjectOrchestrator(harness.store, harness.creative_service, permissions, harness.workflow_repository, lambda _actor, _kind, _id: True)
        harness.runner = _RestartableWriterActionRunner(store=harness.store, orchestrator=harness.orchestrator, job_manager=harness.job_manager, principal_loader=lambda user_id: harness.actor if user_id == harness.actor.user_id else None)
        harness.action_service = ActionService(store=harness.store, runner=harness.runner)
        harness.job_manager.wake = harness.runner._poll_event.set
        app = FastAPI()
        app.dependency_overrides[current_principal] = lambda: harness.actor
        app.include_router(writer_agent_api.build_writer_agent_router(tmp_path, creative_repository=harness.creative_repository, creative_application_service=harness.creative_service, store=harness.store, action_service=harness.action_service, runner=harness.runner, permission_service=permissions, workflow_repository=harness.workflow_repository))
        harness.client = TestClient(app)
        monkeypatch.setattr(writer_agent_api, "create_provider", lambda: harness.provider)
        monkeypatch.setattr(phase4_worker, "create_provider", lambda: harness.provider)
        return harness

    def create_session(self, content: str) -> dict[str, Any]:
        return self._ok(self.client.post("/api/writer-agent/sessions", json={"title": "集成测试", "content": content}))

    def upload_material(self, session: dict[str, Any], name: str, content: str) -> dict[str, Any]:
        return self._ok(self.client.post(f"/api/writer-agent/sessions/{session['id']}/materials/file", content=content.encode("utf-8"), headers={"X-Writer-Filename": quote(name)}))

    def chat(self, session: dict[str, Any], message: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message}
        reference_match = re.search(r"参考选题\s*(\d+)", message)
        if reference_match:
            payload["reference_refs"] = [{
                "resource_type": "topic",
                "resource_id": int(reference_match.group(1)),
            }]
        return self._ok(self.client.post(f"/api/writer-agent/sessions/{session['id']}/messages", json=payload))

    def confirm_and_wait(self, response: dict[str, Any]) -> dict[str, Any]:
        if not response.get("actions"):
            raise AssertionError(response.get("messages"))
        action = response["actions"][-1]
        self._accepted(self.client.post(f"/api/writer-agent/sessions/{action['session_id']}/actions/{action['id']}/decision", json={"decision": "confirm", "expected_revision": action["revision"], "client_request_id": f"confirm:{action['id']}"}))
        return self._terminal(action["session_id"], action["id"])

    def retry_and_wait(self, session_id: int, action_id: str) -> dict[str, Any]:
        retry = self._accepted(self.client.post(f"/api/writer-agent/sessions/{session_id}/actions/{action_id}/retry", json={"client_request_id": f"retry:{action_id}"}))
        return self._terminal(session_id, retry["id"])

    def confirm_until_running(self, response: dict[str, Any]) -> dict[str, Any]:
        action = response["actions"][-1]
        self.job_manager.job_created.clear()
        self.job_bound.clear()
        self._accepted(self.client.post(
            f"/api/writer-agent/sessions/{action['session_id']}/actions/{action['id']}/decision",
            json={
                "decision": "confirm",
                "expected_revision": action["revision"],
                "client_request_id": f"confirm:{action['id']}",
            },
        ))
        assert self.job_manager.job_created.wait(timeout=5), self.store.list_steps(action["id"])
        assert self.job_bound.wait(timeout=5), self.store.list_steps(action["id"])
        self.job_manager.advance()
        assert any(job["status"] == "running" for job in self.job_manager.jobs.values())
        self.runner.request_process_stop()
        assert self.runner.stopped.wait(timeout=5), self.store.list_steps(action["id"])
        return self.store.require_action(action["id"], self.actor.user_id)

    def restart_runner_and_wait(self, session_id: int, action_id: str) -> dict[str, Any]:
        self.runner = _RestartableWriterActionRunner(
            store=self.store,
            orchestrator=self.orchestrator,
            job_manager=self.job_manager,
            principal_loader=lambda user_id: (
                self.actor if user_id == self.actor.user_id else None
            ),
        )
        self.action_service.runner = self.runner
        self.job_manager.wake = self.runner._poll_event.set
        self.runner.enqueue(action_id, self.actor)
        return self._terminal(session_id, action_id)

    def project_with_locked_outline_and_beats(self, episodes: range) -> dict[str, Any]:
        numbers = list(episodes)
        project, _ = self.original_project_service.create(OriginalProjectCreateRequest(seed_text="情绪公司", name="情绪公司", genre="都市奇幻", target_platform="hg", target_audience="18-35岁", target_episode_count=len(numbers), episode_duration_seconds=120, cost_level="low", content_boundaries=[], reference_refs=[], creation_request_id=f"seed:{uuid.uuid4().hex}"), self.actor, can_read_reference=lambda _kind, _id: True)
        for stage, payload in [("brief", self.provider.brief()), ("characters", self.provider.characters()), ("outline", self.provider.outline(len(numbers)))]:
            task = self.creative_service.ensure_stage_task(project["id"], stage, self.actor)
            resource = self.creative_service.persist_draft(project_id=project["id"], stage=stage, payload=payload, workflow_task_id=task["id"], operation_id=f"seed:{stage}", actor=self.actor)
            if task["status"] == "assigned":
                self.workflow_repository.start_task(task["id"], self.actor)
            self.creative_service.lock_resource(project_id=project["id"], stage=stage, resource_id=resource["resource_id"], workflow_task_id=task["id"], operation_id=f"seed:{stage}:lock", actor=self.actor)
        task = self.creative_service.ensure_stage_task(project["id"], "beats", self.actor)
        for episode in numbers:
            resource = self.creative_service.persist_draft(project_id=project["id"], stage="beats", payload=self.provider.beats(episode), workflow_task_id=task["id"], operation_id=f"seed:beats:{episode}", actor=self.actor, episode_number=episode)
            current_task = self.workflow_repository.get_task(task["id"])
            if current_task and current_task["status"] == "assigned":
                self.workflow_repository.start_task(task["id"], self.actor)
            self.creative_service.lock_resource(project_id=project["id"], stage="beats", resource_id=resource["resource_id"], workflow_task_id=task["id"], operation_id=f"seed:beats:{episode}:lock", actor=self.actor, episode_number=episode)
        self.creative_service.ensure_stage_task(project["id"], "script", self.actor)
        return self.creative_repository.get_project(project["id"])

    def link_project(self, session: dict[str, Any], project_id: int) -> dict[str, Any]:
        return self.confirm_and_wait(self._ok(self.client.post(f"/api/writer-agent/sessions/{session['id']}/messages", json={"message": f"关联已有项目 {project_id}", "link_project_id": project_id})))

    def fail_script_episode_once(self, episode_number: int) -> None:
        self.job_manager.fail_once(f"script.generate:{episode_number}")

    def script_versions(self, project_id: int) -> dict[int, int]:
        project = self.creative_repository.get_project(project_id)
        return {episode: len(self.creative_repository.list_episode_versions(project_id, "script", episode)) for episode in sorted({int(item["episode_number"]) for item in project["episodes"] if item["stage"] == "script"})}

    def script_episode_titles(self, project_id: int) -> dict[int, str]:
        project = self.creative_repository.get_project(project_id)
        return {
            int(item["episode_number"]): str(item["content"]["episode_title"])
            for item in project["episodes"]
            if item["stage"] == "script"
        }

    def projects(self) -> list[dict[str, Any]]:
        return self.creative_repository.list_projects_for_bootstrap(
            self.actor.user_id
        )

    def seed_readable_topic_reference(self, title: str) -> dict[str, Any]:
        return self.creative_repository.upsert_topics([{ "platform": "hg", "job_id": f"ref:{uuid.uuid4().hex}", "source_path": "integration.json", "captured_at": "2026-08-20", "title": title, "description": "", "category": "都市", "rank_type": "热播榜", "score": 90, "confidence": "high", "score_details": {}, "recommendation": "推荐", "evidence": [] }])[0]

    def source_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self.store.require_source_snapshot(snapshot_id)["snapshot"]

    @staticmethod
    def _ok(response: Any) -> dict[str, Any]:
        assert response.status_code in {200, 201}, response.text
        return response.json()

    @staticmethod
    def _accepted(response: Any) -> dict[str, Any]:
        assert response.status_code in {200, 202}, response.text
        return response.json()

    def _terminal(self, session_id: int, action_id: str) -> dict[str, Any]:
        for _attempt in range(100):
            self.runner.refreshed.clear()
            self.action_changed.clear()
            action = self._ok(self.client.get(f"/api/writer-agent/sessions/{session_id}/actions/{action_id}"))
            if action["status"] in {"succeeded", "failed", "cancelled"}:
                return action
            if self.job_manager.advance():
                assert self.runner.refreshed.wait(timeout=5), {
                    "action": action,
                    "steps": self.store.list_steps(action_id),
                }
            elif self.store.active_job_ids(action_id):
                continue
            else:
                # A recovered runner may be between durable, non-job steps.
                # Wait for its observable next transition, never for a fixed
                # interval, before polling the action again.
                self.runner._poll_event.set()
                assert self.action_changed.wait(timeout=5), {
                    "action": action,
                    "steps": self.store.list_steps(action_id),
                }
        raise AssertionError({"action": action, "steps": self.store.list_steps(action_id)})

    def close(self) -> None:
        for project in self.creative_repository.list_projects_for_bootstrap(self.actor.user_id):
            self.workflow_repository.delete_project_data(str(project["id"]))
        self.client.close()
        self.database.dispose()


@pytest.fixture
def e2e_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[WriterAgentE2EHarness]:
    del request
    harness = WriterAgentE2EHarness.create(tmp_path, monkeypatch)
    yield harness
    harness.close()
