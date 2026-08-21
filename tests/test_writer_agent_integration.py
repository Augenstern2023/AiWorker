# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from creative.application_service import (
    CreativeApplicationService,
    CreativeContextConflict,
)
from creative import phase4_worker
from creative.repository import CreativeRepository
from creative.workflow_adapter import CreativeWorkflowAdapter
from identity.schema import Principal
from workflow.transition_service import TransitionService
from writer_agent.orchestrator import WriterProjectOrchestrator
from writer_agent.schema import CreativePlan, PlanStep, WriterActionError


def test_creative_plan_keeps_dependency_and_authority_contract() -> None:
    """Would catch a serialized plan that loses its execution ordering contract."""
    plan = CreativePlan(
        project_id=17,
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-1",
        assumptions=[],
        steps=[
            PlanStep(
                step_key="beats.lock:1", stage="beats", kind="lock",
                episode_number=1, depends_on=["beats.generate:1"],
                expected_context={"revision": 3},
            ),
        ],
    )

    restored = CreativePlan.model_validate(plan.model_dump(mode="json"))

    assert restored.steps[0].depends_on == ["beats.generate:1"]
    assert restored.steps[0].expected_context == {"revision": 3}


class RecordingJobManager:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def create_job(self, payload: dict[str, Any]) -> SimpleNamespace:
        self.payloads.append(payload)
        return SimpleNamespace(job_id=f"job-{len(self.payloads)}", status="pending")


class RepositoryTransitions:
    def __init__(self, repository: CreativeRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[str, str]] = []

    def reconcile_creative_without_review(
        self, workflow_task_id: str, stage: str, resource_id: str
    ) -> None:
        del workflow_task_id, stage, resource_id
        return None

    def complete_creative_without_review(
        self,
        workflow_task_id: str,
        stage: str,
        resource_id: str,
        actor: Principal,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del workflow_task_id, actor
        self.calls.append((stage, resource_id))
        authority_kwargs = {
            "expected_revision": kwargs.get("expected_project_revision"),
            "expected_authority": kwargs.get("expected_authority"),
        }
        if stage == "brief":
            resource = self.repository.lock_brief(
                int(resource_id),
                kwargs.get("difference_note", ""),
                **authority_kwargs,
            )
        elif stage == "characters":
            project_id, batch_id = resource_id.split(":", 1)
            resource = self.repository.lock_character_batch(
                int(project_id), batch_id, **authority_kwargs
            )
        elif stage == "outline":
            project_id, batch_id = resource_id.split(":", 1)
            resource = self.repository.lock_outline_batch(
                int(project_id), batch_id, **authority_kwargs
            )
        elif stage == "beats":
            resource = self.repository.lock_episode_beats(
                int(resource_id), **authority_kwargs
            )
        else:
            resource = self.repository.lock_episode_script(
                int(resource_id), **authority_kwargs
            )
        return {
            "resource_type": stage,
            "resource_id": resource_id,
            "status": resource.get("status", "locked"),
        }


@pytest.fixture
def real_writer_context(tmp_path: Path) -> SimpleNamespace:
    repository = CreativeRepository(tmp_path / "creative.db")
    workflow = MagicMock()
    workflow.ensure_project_task.side_effect = (
        lambda project_id, topic_id, *, task_type, role_code, actor: {
            "id": f"{task_type}-task",
            "task_type": task_type,
            "status": "in_progress",
            "project_id": str(project_id),
            "assignee_user_id": actor.user_id,
        }
    )
    workflow.is_active_project_member.return_value = True
    workflow_adapter = MagicMock(unsafe=True)
    permissions = MagicMock()
    permissions.can_create_project_content.return_value = True
    jobs = RecordingJobManager()
    transitions = RepositoryTransitions(repository)
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    service = CreativeApplicationService(
        repository=repository,
        workflow_repository=workflow,
        transitions=transitions,  # type: ignore[arg-type]
        job_manager=jobs,  # type: ignore[arg-type]
        runtime_dir=tmp_path,
        original_project_service=MagicMock(),
        workflow_adapter=workflow_adapter,
        permission_service=permissions,
    )
    orchestrator = WriterProjectOrchestrator(
        store=MagicMock(),
        creative_service=service,
        permission_service=permissions,
        workflow_repository=workflow,
        can_read_reference=lambda _actor, _resource_type, _resource_id: True,
    )

    def create_project(
        request_id: str = "writer-integration", episode_count: int = 1
    ) -> dict[str, Any]:
        project, _ = repository.ensure_original_project(
            seed_text="未来公司按照员工情绪发工资",
            name="情绪公司",
            target_episode_count=episode_count,
            created_by=actor.user_id,
            creation_request_id=request_id,
        )
        return project

    return SimpleNamespace(
        repository=repository,
        workflow=workflow,
        workflow_adapter=workflow_adapter,
        permissions=permissions,
        jobs=jobs,
        transitions=transitions,
        actor=actor,
        service=service,
        orchestrator=orchestrator,
        create_project=create_project,
    )


def valid_brief(title: str = "情绪公司") -> dict[str, Any]:
    return {
        "title": title,
        "logline": "未来公司按员工情绪发工资",
        "theme": "人在制度中保持选择",
        "protagonist": "林岚",
        "protagonist_desire": "获得自主权",
        "protagonist_flaw": "过度独立",
        "pressure_response": "先独自承担再学习协作",
        "main_opposition": "僵化制度",
        "core_conflict": "情绪考核",
        "main_goal": "改变制度",
        "background": "未来公司",
        "worldview": "情绪可量化但不能定义人",
        "story_engine": "每集解决一种情绪考核",
        "first_three_hooks": ["工资清零"],
        "highlights": ["规则反杀"],
        "differentiators": ["情绪劳动具象化"],
        "audience": "18-35岁",
        "market_fit": "创作判断：适合强设定短剧",
        "cost_level": "low",
        "originality_statement": "从种子独立发展",
        "risks": ["设定解释成本"],
        "assumptions": ["观众理解积分"],
        "open_questions": ["制度由谁建立"],
    }


def lock_competing_brief(context: SimpleNamespace, project_id: int) -> dict[str, Any]:
    brief = context.repository.create_blueprints(
        project_id,
        [{"content": valid_brief("并发 Brief")}],
        direction_type="original",
    )[0]
    return context.repository.lock_brief(brief["id"])


def seed_locked_episode_chain(context: SimpleNamespace, request_id: str) -> dict[str, Any]:
    project = context.create_project(request_id)
    brief = context.repository.create_blueprints(
        project["id"], [{"content": valid_brief()}], direction_type="original"
    )[0]
    context.repository.lock_brief(brief["id"])
    characters = context.repository.create_character_batch(
        project["id"], [{"name": "林岚", "identity": "职员"}], []
    )
    context.repository.lock_character_batch(project["id"], characters["batch_id"])
    outline = context.repository.create_outline_batch(
        project["id"], [{"episode": 1, "summary": "开端"}], "主线"
    )
    context.repository.lock_outline_batch(project["id"], outline["batch_id"])
    beats = context.repository.create_episode_beats(
        project["id"], 1, {"beats": [{"beat": "开场"}]}
    )
    context.repository.lock_episode_beats(beats["id"])
    script = context.repository.create_episode_script(
        project["id"], 1, {"scenes": []}
    )
    context.repository.lock_episode_script(script["id"])
    return {
        "project": context.repository.get_project(project["id"]),
        "brief": brief,
        "characters": characters,
        "outline": outline,
        "beats": beats,
        "script": script,
    }


def seed_locked_two_episode_authority(
    context: SimpleNamespace, request_id: str
) -> dict[str, Any]:
    project = context.create_project(request_id, episode_count=2)
    brief = context.repository.create_blueprints(
        project["id"], [{"content": valid_brief()}], direction_type="original"
    )[0]
    context.repository.lock_brief(brief["id"])
    characters = context.repository.create_character_batch(
        project["id"], [{"name": "林岚", "identity": "职员"}], []
    )
    context.repository.lock_character_batch(project["id"], characters["batch_id"])
    outline = context.repository.create_outline_batch(
        project["id"],
        [
            {"episode": 1, "summary": "第一集"},
            {"episode": 2, "summary": "第二集"},
        ],
        "两集主线",
    )
    context.repository.lock_outline_batch(project["id"], outline["batch_id"])
    beats = []
    for episode_number in (1, 2):
        item = context.repository.create_episode_beats(
            project["id"],
            episode_number,
            {"beats": [{"beat": f"第 {episode_number} 集开场"}]},
        )
        context.repository.lock_episode_beats(item["id"])
        beats.append(item)
    return {
        "project": context.repository.get_project(project["id"]),
        "brief": brief,
        "characters": characters,
        "outline": outline,
        "beats": beats,
    }


def test_real_repository_fully_locked_chain_has_no_executable_steps(
    real_writer_context,
) -> None:
    """Protect the five-stage shortest chain against real repository snapshots."""
    seeded = seed_locked_episode_chain(real_writer_context, "all-locked-real")

    plan = real_writer_context.orchestrator.build_plan(
        project=seeded["project"],
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-all-locked-real",
    )

    assert plan.steps == []
    assert [item["resource_type"] for item in plan.reused_resources] == [
        "brief", "characters", "outline", "beats", "script",
    ]


def test_real_repository_stale_beats_is_not_reused(real_writer_context) -> None:
    """Protect planning when a new active outline leaves old locked Beats historical."""
    seeded = seed_locked_episode_chain(real_writer_context, "stale-beats-real")
    project_id = seeded["project"]["id"]
    replacement = real_writer_context.repository.create_outline_batch(
        project_id, [{"episode": 1, "summary": "新版开端"}], "新版主线"
    )
    real_writer_context.repository.lock_outline_batch(
        project_id, replacement["batch_id"]
    )

    plan = real_writer_context.orchestrator.build_plan(
        project=real_writer_context.repository.get_project(project_id),
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-stale-beats-real",
    )

    assert [step.step_key for step in plan.steps] == [
        "beats.generate:1", "beats.lock:1", "script.generate:1",
    ]
    assert {item["resource_type"] for item in plan.reused_resources} == {
        "brief", "characters", "outline",
    }


def test_execution_uses_highest_compatible_beats_not_higher_stale_version(
    real_writer_context,
) -> None:
    """Would catch execution context comparing against a stale highest locked row."""
    seeded = seed_locked_episode_chain(real_writer_context, "compatible-beats-execute")
    project_id = seeded["project"]["id"]
    stale = real_writer_context.repository.create_episode_beats(
        project_id, 1, {"beats": [{"beat": "将被标记为过期"}]}
    )
    stale_refs = dict(stale["upstream_refs"])
    stale_refs["outline_id"] = 999999
    with real_writer_context.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET upstream_refs_json = ? WHERE id = ?",
            (json.dumps(stale_refs, ensure_ascii=False), stale["id"]),
        )
        connection.execute(
            "UPDATE creative_episodes SET status = 'historical' WHERE id = ?",
            (seeded["script"]["id"],),
        )
    real_writer_context.repository.lock_episode_beats(stale["id"])
    plan = real_writer_context.orchestrator.build_plan(
        project=real_writer_context.repository.get_project(project_id),
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-compatible-beats-execute",
    )

    assert [step.step_key for step in plan.steps] == ["script.generate:1"]
    assert plan.steps[0].expected_context["beats"]["resource_id"] == str(
        seeded["beats"]["id"]
    )
    generated = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=plan.steps[0],
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-compatible-beats-execute"},
        payload={"generation_instruction": "基于兼容 Beats 生成"},
    )
    assert generated["status"] == "pending"
    assert real_writer_context.jobs.payloads[-1]["extra"]["source_authority"][
        "locked_beats"
    ] == [
        {
            "episode_number": 1,
            "id": seeded["beats"]["id"],
            "fingerprint": real_writer_context.repository._content_fingerprint(
                seeded["beats"]["content"]
            ),
        }
    ]


def test_workflow_script_lock_uses_highest_compatible_beats_not_stale_version(
    real_writer_context,
) -> None:
    """A Script created from compatible Beats must survive the real adapter lock."""
    seeded = seed_locked_episode_chain(
        real_writer_context, "compatible-beats-workflow-lock"
    )
    project_id = seeded["project"]["id"]
    stale = real_writer_context.repository.create_episode_beats(
        project_id, 1, {"beats": [{"beat": "过期但版本更高"}]}
    )
    stale_refs = dict(stale["upstream_refs"])
    stale_refs["outline_id"] = 999999
    with real_writer_context.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET upstream_refs_json = ? WHERE id = ?",
            (json.dumps(stale_refs, ensure_ascii=False), stale["id"]),
        )
        connection.execute(
            "UPDATE creative_episodes SET status = 'historical' WHERE id = ?",
            (seeded["script"]["id"],),
        )
    real_writer_context.repository.lock_episode_beats(stale["id"])
    script = real_writer_context.repository.create_episode_script(
        project_id, 1, {"scenes": [{"scene": 1, "summary": "兼容场次"}]}
    )
    assert script["upstream_refs"]["beats_id"] == seeded["beats"]["id"]

    task = {
        "id": "script-task",
        "task_type": "script",
        "project_id": str(project_id),
        "assignee_user_id": real_writer_context.actor.user_id,
    }
    adapter = CreativeWorkflowAdapter(
        real_writer_context.repository,
        SimpleNamespace(
            get_task=lambda _task_id: task,
            get_latest_resource_submission=lambda *_args: None,
        ),
    )

    adapter.lock_without_review(
        "script-task", "script", str(script["id"]), real_writer_context.actor
    )

    assert real_writer_context.repository.get_episode(script["id"])["status"] == "locked"


def test_real_revisions_keep_parent_lineage_and_never_patch_locked_rows(
    real_writer_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect real character, outline and Beats replacement lineage."""
    seeded = seed_locked_episode_chain(real_writer_context, "revision-lineage")
    project_id = seeded["project"]["id"]
    patch_methods = [
        "update_blueprint", "update_character", "update_relationship",
        "update_outline_overview", "update_episode",
    ]
    patch_spies: dict[str, MagicMock] = {}
    for method_name in patch_methods:
        spy = MagicMock(wraps=getattr(real_writer_context.repository, method_name))
        monkeypatch.setattr(real_writer_context.repository, method_name, spy)
        patch_spies[method_name] = spy

    cases = [
        (
            "characters",
            None,
            {
                "characters": [{"name": "林岚", "identity": "主管"}],
                "relationships": [],
            },
            f"{project_id}:{seeded['characters']['batch_id']}",
            seeded["characters"]["characters"][0]["id"],
        ),
        (
            "outline",
            None,
            {"episodes": [{"episode": 1, "summary": "新版开端"}], "series_arc": "主线"},
            f"{project_id}:{seeded['outline']['batch_id']}",
            seeded["outline"]["episodes"][0]["id"],
        ),
        (
            "beats",
            1,
            {"beats": [{"beat": "新版开场"}]},
            str(seeded["beats"]["id"]),
            seeded["beats"]["id"],
        ),
        (
            "script",
            1,
            {"scenes": [{"scene": 1, "summary": "新版场次"}]},
            str(seeded["script"]["id"]),
            seeded["script"]["id"],
        ),
    ]
    created_refs: list[dict[str, Any]] = []
    for stage, episode_number, payload, replaces_id, parent_id in cases:
        plan = real_writer_context.orchestrator.build_plan(
            project=real_writer_context.repository.get_project(project_id),
            target_stage=stage,
            episode_start=episode_number,
            episode_end=episode_number,
            source_snapshot_id=f"snapshot-revision-{stage}",
            rework_stage=stage,
        )
        step = plan.steps[0]
        assert step.replaces_resource_id == replaces_id
        result = real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=step,
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": f"snapshot-revision-{stage}"},
            payload=payload,
            operation_id=f"revision-{stage}",
        )
        ref = result["resource_refs"][0]
        created_refs.append(ref)
        hydrated = real_writer_context.service.hydrate_resource_ref(ref)
        if stage == "characters":
            assert hydrated["characters"][0]["parent_version_id"] == parent_id
        elif stage == "outline":
            assert hydrated["episodes"][0]["parent_version_id"] == parent_id
        else:
            assert hydrated["parent_version_id"] == parent_id
            if stage == "script":
                assert {
                    "brief_id",
                    "brief_version",
                    "brief_content_fingerprint",
                    "character_batch_id",
                    "character_content_fingerprint",
                    "outline_batch_id",
                    "outline_id",
                    "outline_version",
                    "outline_content_fingerprint",
                    "beats_id",
                    "beats_version",
                    "beats_content_fingerprint",
                } <= hydrated["upstream_refs"].keys()

    assert [ref["resource_type"] for ref in created_refs] == [
        "characters", "outline", "beats", "script",
    ]
    assert all(spy.call_count == 0 for spy in patch_spies.values())


def test_stale_episode_revision_is_rejected_before_task_acquisition(
    real_writer_context,
) -> None:
    """Protect revision creation with both the plan check and repository CAS boundary."""
    seeded = seed_locked_episode_chain(real_writer_context, "stale-beats-revision")
    project_id = seeded["project"]["id"]
    plan = real_writer_context.orchestrator.build_plan(
        project=seeded["project"],
        target_stage="beats",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-stale-beats-revision",
        rework_stage="beats",
    )
    replacement_outline = real_writer_context.repository.create_outline_batch(
        project_id, [{"episode": 1, "summary": "并发新版"}], "并发主线"
    )
    real_writer_context.repository.lock_outline_batch(
        project_id, replacement_outline["batch_id"]
    )

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-stale-beats-revision"},
            payload={"beats": [{"beat": "不应写入"}]},
        )

    assert exc.value.code == "STALE_PROJECT_CONTEXT"
    real_writer_context.workflow.ensure_project_task.assert_not_called()
    beats = [
        item
        for item in real_writer_context.repository.get_project(project_id)["episodes"]
        if item["stage"] == "beats"
    ]
    assert [item["id"] for item in beats] == [seeded["beats"]["id"]]


def test_same_plan_beats_lock_advances_script_authority(real_writer_context) -> None:
    """Protect per-episode authority advance when a same-plan Beats lock succeeds."""
    seeded = seed_locked_episode_chain(real_writer_context, "beats-advance")
    project_id = seeded["project"]["id"]
    replacement_outline = real_writer_context.repository.create_outline_batch(
        project_id, [{"episode": 1, "summary": "再开端"}], "再版主线"
    )
    real_writer_context.repository.lock_outline_batch(
        project_id, replacement_outline["batch_id"]
    )
    draft_beats = real_writer_context.repository.create_episode_beats(
        project_id, 1, {"beats": [{"beat": "新 Beats"}]}
    )
    plan = real_writer_context.orchestrator.build_plan(
        project=real_writer_context.repository.get_project(project_id),
        target_stage="script",
        episode_start=1,
        episode_end=1,
        source_snapshot_id="snapshot-beats-advance",
    )
    lock_step = next(step for step in plan.steps if step.step_key == "beats.lock:1")
    script_step = next(
        step for step in plan.steps if step.step_key == "script.generate:1"
    )

    locked = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=lock_step,
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-beats-advance"},
        payload={"resource_id": str(draft_beats["id"])},
    )

    assert script_step.expected_context["beats"]["resource_id"] == str(
        draft_beats["id"]
    )
    assert locked["advanced_contexts"]["script.generate:1"] == (
        script_step.expected_context
    )
    generated = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=script_step,
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-beats-advance"},
        payload={"generation_instruction": "生成剧本"},
    )
    assert generated["status"] == "pending"


def test_stale_generate_is_rejected_before_task_acquisition(real_writer_context) -> None:
    """Would catch stale authority that still claims a task and enqueues a job."""
    project = real_writer_context.create_project("stale-generate")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-stale-generate",
    )
    lock_competing_brief(real_writer_context, project["id"])

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-stale-generate"},
            payload={"generation_instruction": "生成方向"},
        )

    assert getattr(exc.value, "code", None) == "STALE_PROJECT_CONTEXT"
    real_writer_context.workflow.ensure_project_task.assert_not_called()
    assert real_writer_context.jobs.payloads == []


def test_stale_persist_is_rejected_before_task_acquisition(real_writer_context) -> None:
    """Would catch a normal persist that ignores the confirmed plan authority."""
    project = real_writer_context.create_project("stale-persist")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-stale-persist",
        confirmed_brief_payload=valid_brief(),
    )
    lock_competing_brief(real_writer_context, project["id"])

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-stale-persist"},
            payload=valid_brief(),
        )

    assert getattr(exc.value, "code", None) == "STALE_PROJECT_CONTEXT"
    real_writer_context.workflow.ensure_project_task.assert_not_called()


def test_stale_lock_is_rejected_before_task_acquisition(real_writer_context) -> None:
    """Would catch an old lock step that can repoint project authority."""
    project = real_writer_context.create_project("stale-lock")
    planned_brief = real_writer_context.repository.create_blueprints(
        project["id"], [{"content": valid_brief("计划 Brief")}],
        direction_type="original",
    )[0]
    plan = real_writer_context.orchestrator.build_plan(
        project=real_writer_context.repository.get_project(project["id"]),
        target_stage="characters",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-stale-lock",
        confirmed_brief_payload=valid_brief("计划 Brief"),
    )
    lock_step = next(step for step in plan.steps if step.step_key == "brief.lock")
    lock_competing_brief(real_writer_context, project["id"])

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=lock_step,
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-stale-lock"},
            payload={"resource_id": str(planned_brief["id"])},
        )

    assert getattr(exc.value, "code", None) == "STALE_PROJECT_CONTEXT"
    real_writer_context.workflow.ensure_project_task.assert_not_called()
    assert real_writer_context.transitions.calls == []


def test_context_is_rechecked_after_task_acquisition_before_generate(
    real_writer_context,
) -> None:
    """Would catch a TOCTOU gap between task acquisition and job enqueue."""
    project = real_writer_context.create_project("generate-toctou")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-generate-toctou",
    )
    original = real_writer_context.workflow.ensure_project_task.side_effect

    def mutate_then_return(*args: Any, **kwargs: Any) -> dict[str, Any]:
        lock_competing_brief(real_writer_context, project["id"])
        real_writer_context.workflow.ensure_project_task.side_effect = original
        return original(*args, **kwargs)

    real_writer_context.workflow.ensure_project_task.side_effect = mutate_then_return

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-generate-toctou"},
            payload={"generation_instruction": "生成方向"},
        )

    assert getattr(exc.value, "code", None) == "STALE_PROJECT_CONTEXT"
    assert real_writer_context.jobs.payloads == []


def test_context_is_rechecked_after_task_acquisition_before_persist(
    real_writer_context,
) -> None:
    """Would catch a TOCTOU gap between task acquisition and repository persist."""
    project = real_writer_context.create_project("persist-toctou")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-persist-toctou",
        confirmed_brief_payload=valid_brief(),
    )
    original = real_writer_context.workflow.ensure_project_task.side_effect

    def mutate_then_return(*args: Any, **kwargs: Any) -> dict[str, Any]:
        lock_competing_brief(real_writer_context, project["id"])
        real_writer_context.workflow.ensure_project_task.side_effect = original
        return original(*args, **kwargs)

    real_writer_context.workflow.ensure_project_task.side_effect = mutate_then_return

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-persist-toctou"},
            payload=valid_brief(),
        )

    assert getattr(exc.value, "code", None) == "STALE_PROJECT_CONTEXT"


def test_context_is_rechecked_after_task_acquisition_before_lock(
    real_writer_context,
) -> None:
    """Would catch a TOCTOU gap between task acquisition and authority lock."""
    project = real_writer_context.create_project("lock-toctou")
    planned_brief = real_writer_context.repository.create_blueprints(
        project["id"], [{"content": valid_brief("计划 Brief")}],
        direction_type="original",
    )[0]
    plan = real_writer_context.orchestrator.build_plan(
        project=real_writer_context.repository.get_project(project["id"]),
        target_stage="characters",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-lock-toctou",
        confirmed_brief_payload=valid_brief("计划 Brief"),
    )
    lock_step = next(step for step in plan.steps if step.step_key == "brief.lock")
    original = real_writer_context.workflow.ensure_project_task.side_effect

    def mutate_then_return(*args: Any, **kwargs: Any) -> dict[str, Any]:
        lock_competing_brief(real_writer_context, project["id"])
        real_writer_context.workflow.ensure_project_task.side_effect = original
        return original(*args, **kwargs)

    real_writer_context.workflow.ensure_project_task.side_effect = mutate_then_return

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=lock_step,
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-lock-toctou"},
            payload={"resource_id": str(planned_brief["id"])},
        )

    assert getattr(exc.value, "code", None) == "STALE_PROJECT_CONTEXT"
    assert real_writer_context.transitions.calls == []


def test_same_plan_lock_advances_downstream_trusted_context(real_writer_context) -> None:
    """Would catch a runner that mistakes its own preceding lock for a stale plan."""
    project = real_writer_context.create_project("same-plan-advance")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="characters",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-same-plan",
        confirmed_brief_payload=valid_brief(),
    )
    persist_step, lock_step, generate_step = plan.steps
    persisted = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=persist_step,
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-same-plan"},
        payload=valid_brief(),
    )
    resource_id = persisted["resource_refs"][0]["resource_id"]

    locked = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=lock_step,
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-same-plan"},
        payload={"resource_id": resource_id},
    )

    assert generate_step.expected_context["revision"] == 1
    assert str(generate_step.expected_context["locked_brief_id"]) == resource_id
    assert locked["advanced_contexts"]["characters.generate"] == (
        generate_step.expected_context
    )
    generated = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=generate_step,
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-same-plan"},
        payload={"generation_instruction": "生成人物"},
    )
    assert generated["status"] == "pending"
    assert len(real_writer_context.jobs.payloads) == 1


def test_authority_advance_does_not_trust_a_concurrent_post_lock_change(
    real_writer_context,
) -> None:
    """Would catch authority propagation that blesses any post-step repository state."""
    project = real_writer_context.create_project("post-lock-race")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="characters",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-post-lock-race",
        confirmed_brief_payload=valid_brief("计划 Brief"),
    )
    persist_step, lock_step, generate_step = plan.steps
    persisted = real_writer_context.orchestrator.execute_step(
        plan=plan,
        step=persist_step,
        actor=real_writer_context.actor,
        source_snapshot={"snapshot_id": "snapshot-post-lock-race"},
        payload=valid_brief("计划 Brief"),
    )
    resource_id = persisted["resource_refs"][0]["resource_id"]
    original_complete = (
        real_writer_context.transitions.complete_creative_without_review
    )

    def lock_then_compete(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_complete(*args, **kwargs)
        lock_competing_brief(real_writer_context, project["id"])
        return result

    real_writer_context.transitions.complete_creative_without_review = (
        lock_then_compete
    )

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=lock_step,
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-post-lock-race"},
            payload={"resource_id": resource_id},
        )

    assert exc.value.code == "STALE_PROJECT_CONTEXT"
    assert generate_step.expected_context["locked_brief_id"] is None


def test_project_write_denial_is_not_reported_as_task_occupation(
    real_writer_context,
) -> None:
    """Would catch project authorization failures mapped to assignment conflicts."""
    project = real_writer_context.create_project("project-denied")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-project-denied",
    )
    real_writer_context.permissions.can_create_project_content.return_value = False

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-project-denied"},
            payload={"generation_instruction": "生成方向"},
        )

    assert exc.value.code == "PROJECT_ACCESS_DENIED"
    real_writer_context.workflow.ensure_project_task.assert_not_called()


def test_project_write_denial_precedes_stale_authority_details(
    real_writer_context,
) -> None:
    """Would catch stale-authority disclosure to an actor without project access."""
    project = real_writer_context.create_project("project-denied-stale")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-project-denied-stale",
    )
    lock_competing_brief(real_writer_context, project["id"])
    real_writer_context.permissions.can_create_project_content.return_value = False

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-project-denied-stale"},
            payload={"generation_instruction": "生成方向"},
        )

    assert exc.value.code == "PROJECT_ACCESS_DENIED"
    real_writer_context.workflow.ensure_project_task.assert_not_called()


def test_unexpected_internal_error_is_sanitized_at_writer_boundary(
    real_writer_context,
) -> None:
    """Would catch repository paths or exception text escaping to the writer UI."""
    project = real_writer_context.create_project("sanitize-internal")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-sanitize",
    )
    real_writer_context.service.ensure_stage_task = MagicMock(
        side_effect=OSError(r"D:\private\creative.db: repository unavailable")
    )

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-sanitize"},
            payload={"generation_instruction": "生成方向"},
        )

    assert exc.value.code == "CREATIVE_OPERATION_FAILED"
    assert "private" not in str(exc.value)
    assert "repository" not in str(exc.value)


def test_lock_repository_cas_rejects_change_after_last_service_check(
    real_writer_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A competing Brief in the final lock window must remain authoritative."""
    project = real_writer_context.create_project("repository-lock-cas-window")
    planned = real_writer_context.repository.create_blueprints(
        project["id"], [{"content": valid_brief("计划 Brief")}],
        direction_type="original",
    )[0]
    competing = real_writer_context.repository.create_blueprints(
        project["id"], [{"content": valid_brief("并发 Brief")}],
        direction_type="original",
    )[0]
    expected_context = real_writer_context.service.current_plan_context(project["id"])

    adapter = CreativeWorkflowAdapter(
        real_writer_context.repository, MagicMock()
    )
    monkeypatch.setattr(adapter, "_assert_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, "assert_editable", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        adapter, "_assert_current_upstream", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(adapter, "_assert_brief_risk", lambda *args, **kwargs: None)
    transitions = object.__new__(TransitionService)
    transitions.creative_adapter = adapter
    transitions.repository = MagicMock()
    real_writer_context.service.transitions = transitions
    original_lock = real_writer_context.repository.lock_brief

    def compete_at_repository_boundary(
        blueprint_id: int, difference_note: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        original_lock(competing["id"])
        return original_lock(blueprint_id, difference_note, **kwargs)

    monkeypatch.setattr(
        real_writer_context.repository,
        "lock_brief",
        compete_at_repository_boundary,
    )

    with pytest.raises(CreativeContextConflict):
        real_writer_context.service.lock_resource(
            project_id=project["id"],
            stage="brief",
            resource_id=str(planned["id"]),
            workflow_task_id="brief-task",
            operation_id="repository-lock-cas-window",
            actor=real_writer_context.actor,
            expected_context=expected_context,
        )

    current = real_writer_context.repository.get_project(project["id"])
    assert current["locked_brief_id"] == competing["id"]
    assert real_writer_context.repository.get_blueprint(planned["id"])["status"] == "draft"


def test_authority_changing_lock_replay_returns_completed_reservation_first(
    real_writer_context,
) -> None:
    """The first lock's own authority advance must not block an exact replay."""
    project = real_writer_context.create_project("authority-lock-replay")
    planned = real_writer_context.repository.create_blueprints(
        project["id"], [{"content": valid_brief("可重放 Brief")}],
        direction_type="original",
    )[0]
    expected_context = real_writer_context.service.current_plan_context(project["id"])
    kwargs = {
        "project_id": project["id"],
        "stage": "brief",
        "resource_id": str(planned["id"]),
        "workflow_task_id": "brief-task",
        "operation_id": "authority-lock-replay",
        "actor": real_writer_context.actor,
        "expected_context": expected_context,
    }

    first = real_writer_context.service.lock_resource(**kwargs)
    replayed = real_writer_context.service.lock_resource(**kwargs)

    assert replayed == first
    assert real_writer_context.transitions.calls == [("brief", str(planned["id"]))]


def test_episode_worker_authority_ignores_another_episode_advance(
    real_writer_context,
) -> None:
    """Episode 1 generation must not freeze episode 2 Beats authority."""
    seeded = seed_locked_two_episode_authority(
        real_writer_context, "episode-worker-authority"
    )
    project_id = seeded["project"]["id"]
    expected_context = real_writer_context.service.current_plan_context(project_id, 1)
    real_writer_context.service.start_generation(
        project_id=project_id,
        stage="script",
        actor=real_writer_context.actor,
        workflow_task_id="script-task",
        operation_id="episode-worker-authority-1",
        generation_instruction="生成第一集剧本",
        source_snapshot_id="snapshot-episode-worker-authority",
        source_snapshot={"snapshot_id": "snapshot-episode-worker-authority"},
        episode_number=1,
        episode_start=1,
        episode_end=1,
        expected_context=expected_context,
    )
    extra = real_writer_context.jobs.payloads[-1]["extra"]

    replacement = real_writer_context.repository.create_episode_beats(
        project_id, 2, {"beats": [{"beat": "第二集并行新版"}]}
    )
    real_writer_context.repository.lock_episode_beats(replacement["id"])

    assert phase4_worker._validate_source_authority(
        extra, real_writer_context.repository, project_id
    ) == extra["source_authority"]


def test_episode_persist_cas_ignores_another_episode_advance(
    real_writer_context, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Episode 1 persistence must CAS only common upstream and episode 1 Beats."""
    seeded = seed_locked_two_episode_authority(
        real_writer_context, "episode-persist-authority"
    )
    project_id = seeded["project"]["id"]
    expected_context = real_writer_context.service.current_plan_context(project_id, 1)
    original_create = real_writer_context.repository.create_episode_script

    def advance_episode_two_then_persist(*args: Any, **kwargs: Any) -> dict[str, Any]:
        replacement = real_writer_context.repository.create_episode_beats(
            project_id, 2, {"beats": [{"beat": "第二集提交窗口新版"}]}
        )
        real_writer_context.repository.lock_episode_beats(replacement["id"])
        return original_create(*args, **kwargs)

    monkeypatch.setattr(
        real_writer_context.repository,
        "create_episode_script",
        advance_episode_two_then_persist,
    )

    persisted = real_writer_context.service.persist_draft(
        project_id=project_id,
        stage="script",
        payload={"scenes": []},
        workflow_task_id="script-task",
        operation_id="episode-persist-authority-1",
        actor=real_writer_context.actor,
        episode_number=1,
        expected_context=expected_context,
    )

    assert persisted["episode_number"] == 1
    assert real_writer_context.repository.get_episode(
        int(persisted["resource_id"])
    )["status"] == "draft"


def test_generate_rechecks_project_write_access_after_task_ensure(
    real_writer_context,
) -> None:
    """Access revoked after ensure must prevent the final job enqueue."""
    project = real_writer_context.create_project("generate-final-access")
    plan = real_writer_context.orchestrator.build_plan(
        project=project,
        target_stage="brief",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-generate-final-access",
    )
    real_writer_context.permissions.can_create_project_content.side_effect = [
        True,
        False,
    ]

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=plan.steps[0],
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-generate-final-access"},
            payload={"generation_instruction": "不应排队"},
        )

    assert exc.value.code == "PROJECT_ACCESS_DENIED"
    assert real_writer_context.jobs.payloads == []


def test_lock_rechecks_task_assignment_after_task_ensure(
    real_writer_context,
) -> None:
    """Reassignment after ensure must prevent the repository lock."""
    project = real_writer_context.create_project("lock-final-assignment")
    planned = real_writer_context.repository.create_blueprints(
        project["id"], [{"content": valid_brief("待锁 Brief")}],
        direction_type="original",
    )[0]
    plan = real_writer_context.orchestrator.build_plan(
        project=real_writer_context.repository.get_project(project["id"]),
        target_stage="characters",
        episode_start=None,
        episode_end=None,
        source_snapshot_id="snapshot-lock-final-assignment",
        confirmed_brief_payload=valid_brief("待锁 Brief"),
    )
    lock_step = next(step for step in plan.steps if step.step_key == "brief.lock")

    def ensure_then_reassign(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        real_writer_context.workflow_adapter.assert_task_for_creation.side_effect = (
            PermissionError("task reassigned")
        )
        return {
            "id": "brief-task",
            "task_type": "brief",
            "status": "in_progress",
            "project_id": str(project["id"]),
            "assignee_user_id": real_writer_context.actor.user_id,
        }

    real_writer_context.workflow.ensure_project_task.side_effect = ensure_then_reassign

    with pytest.raises(WriterActionError) as exc:
        real_writer_context.orchestrator.execute_step(
            plan=plan,
            step=lock_step,
            actor=real_writer_context.actor,
            source_snapshot={"snapshot_id": "snapshot-lock-final-assignment"},
            payload={"resource_id": str(planned["id"])},
        )

    assert exc.value.code == "TASK_ASSIGNED_TO_OTHER"
    assert real_writer_context.repository.get_blueprint(planned["id"])["status"] == "draft"


def test_script_generation_rejects_legacy_beats_without_complete_authority(
    real_writer_context,
) -> None:
    """Incomplete historical Beats refs cannot authorize a new Script."""
    seeded = seed_locked_episode_chain(
        real_writer_context, "strict-legacy-beats-authority"
    )
    project_id = seeded["project"]["id"]
    legacy_beats = real_writer_context.repository.create_episode_beats(
        project_id, 1, {"beats": [{"beat": "缺少权威字段的历史数据"}]}
    )
    refs = dict(legacy_beats["upstream_refs"])
    assert {
        "brief_version",
        "brief_content_fingerprint",
        "character_content_fingerprint",
    } <= refs.keys()
    refs.pop("brief_version", None)
    refs.pop("brief_content_fingerprint", None)
    refs.pop("character_content_fingerprint", None)
    with real_writer_context.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET upstream_refs_json = ?, status = 'locked' "
            "WHERE id = ?",
            (json.dumps(refs, ensure_ascii=False), legacy_beats["id"]),
        )
        connection.execute(
            "UPDATE creative_episodes SET status = 'historical' WHERE id = ?",
            (seeded["beats"]["id"],),
        )

    with pytest.raises(ValueError, match="Beats"):
        real_writer_context.repository.create_episode_script(
            project_id,
            1,
            {"scenes": []},
            operation_id="strict-legacy-script",
        )


def test_workflow_adapter_strictly_rejects_incomplete_episode_authority(
    real_writer_context,
) -> None:
    """Workflow locking uses the same strict legacy authority decision."""
    seeded = seed_locked_episode_chain(
        real_writer_context, "strict-legacy-workflow-authority"
    )
    beats_id = int(seeded["beats"]["id"])
    refs = dict(seeded["beats"]["upstream_refs"])
    refs.pop("brief_version", None)
    refs.pop("brief_content_fingerprint", None)
    refs.pop("character_content_fingerprint", None)
    with real_writer_context.repository._connect() as connection:
        connection.execute(
            "UPDATE creative_episodes SET upstream_refs_json = ?, status = 'draft' "
            "WHERE id = ?",
            (json.dumps(refs, ensure_ascii=False), beats_id),
        )
    adapter = CreativeWorkflowAdapter(
        real_writer_context.repository, MagicMock()
    )
    snapshot = real_writer_context.repository.get_episode(beats_id)

    with pytest.raises(ValueError, match="authority|upstream|权威|上游"):
        adapter._assert_current_upstream("beats", str(beats_id), snapshot)
