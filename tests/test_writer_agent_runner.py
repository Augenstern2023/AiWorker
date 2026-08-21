# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Behavior tests for the durable Writer Agent action runner."""

from pathlib import Path
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.job import CrawlJob
from creative.application_service import CreativeApplicationService
from creative.repository import CreativeRepository
from identity.schema import Principal
from runtime.job_manager import JobManager
from writer_agent.orchestrator import WriterProjectOrchestrator
from writer_agent.runner import WriterActionRunner, partition_episode_range
from writer_agent.schema import WriterActionError
from writer_agent.store import WriterAgentStore


def seed_runner_action(
    store: WriterAgentStore,
    session: dict,
    actor: Principal,
    *,
    stage: str,
    episodes: range,
    project_revision: int,
    dependencies: dict[str, list[str]] | None = None,
) -> dict:
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key=(
            f"{stage}:{project_revision}:{episodes.start}:{episodes.stop}"
        ),
        request_hash=f"{stage}:{project_revision}:{episodes.start}:{episodes.stop}",
        payload={"target_stage": stage},
        expected_context={"project_revision": project_revision},
        source_snapshot_id="snapshot-1",
    )
    store.claim_pending_action(action["id"], expected_revision=1)
    for order, episode in enumerate(episodes, start=1):
        key = f"{stage}.generate:{episode}"
        store.upsert_step(
            action_id=action["id"],
            step_key=key,
            stage=stage,
            episode_start=episode,
            episode_end=episode,
            status="pending",
            operation_id=f"{action['id']}:{key}",
            depends_on=(dependencies or {}).get(key, []),
            attempt=1,
            step_order=order,
        )
    return store.require_action(action["id"], actor.user_id)


@pytest.fixture
def runner_context(tmp_path: Path) -> SimpleNamespace:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    session = store.create(actor.user_id, "执行测试", "生成剧本")
    context = SimpleNamespace(
        store=store,
        actor=actor,
        session=session,
        project_revision=4,
        fail_once=set(),
        failed_once=set(),
        executed=[],
        creative_calls=[],
    )
    orchestrator = MagicMock()

    def assert_expected(action: dict, _actor: Principal) -> None:
        expected = int(action["expected_context"]["project_revision"])
        if expected != context.project_revision:
            raise WriterActionError(
                "STALE_PROJECT_CONTEXT", "项目上下文已变化", retryable=False
            )

    def execute_step(
        _action: dict,
        step: dict,
        _actor: Principal,
        *,
        operation_id: str,
    ) -> dict:
        key = step["step_key"]
        context.executed.append(key)
        if key in context.fail_once and key not in context.failed_once:
            context.failed_once.add(key)
            raise WriterActionError("GENERATION_FAILED", key, retryable=True)
        context.creative_calls.append(
            {"step_key": key, "operation_id": operation_id}
        )
        return {"resource_refs": [{"resource_id": key, "status": "draft"}]}

    orchestrator.assert_expected_context = MagicMock(side_effect=assert_expected)
    orchestrator.execute_action_step = MagicMock(side_effect=execute_step)
    jobs = MagicMock()
    runner = WriterActionRunner(
        store=store,
        orchestrator=orchestrator,
        job_manager=jobs,
        principal_loader=lambda user_id: actor if user_id == actor.user_id else None,
    )
    context.orchestrator = orchestrator
    context.jobs = jobs
    context.runner = runner
    return context


def test_runner_keeps_successful_episodes_and_retries_only_failures(
    runner_context,
) -> None:
    """Dropping successful steps or copying them into a retry is a partial-loss bug."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 4),
        project_revision=4,
    )
    runner_context.fail_once.add("script.generate:2")

    runner_context.runner.run(action["id"], runner_context.actor)
    failed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert failed["status"] == "failed"
    assert failed["result"]["partial"] is True
    assert failed["result"]["succeeded_steps"] == [
        "script.generate:1", "script.generate:3"
    ]
    assert failed["result"]["failed_steps"] == ["script.generate:2"]
    assert failed["result"]["executed_episodes"] == [1, 3]
    assert {
        step["step_key"]
        for step in runner_context.store.list_steps(action["id"])
        if step["status"] == "succeeded"
    } == {"script.generate:1", "script.generate:3"}

    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)
    runner_context.runner.run(retry["id"], runner_context.actor)
    assert [
        step["step_key"]
        for step in runner_context.store.list_steps(retry["id"])
    ] == ["script.generate:2"]
    retried = runner_context.store.require_action(
        retry["id"], runner_context.actor.user_id
    )
    assert retried["result"] == {
        "partial": False,
        "succeeded_steps": ["script.generate:2"],
        "failed_steps": [],
        "executed_episodes": [2],
    }


@pytest.mark.parametrize("action_type", ["project.create_original", "project.link"])
def test_runner_executes_project_action_before_marking_it_succeeded(
    runner_context, action_type: str
) -> None:
    project_id = None if action_type == "project.create_original" else 17
    action = runner_context.store.create_action(
        session_id=runner_context.session["id"],
        actor_user_id=runner_context.actor.user_id,
        action_type=action_type,
        project_id=project_id,
        idempotency_key=f"execute:{action_type}",
        request_hash=f"execute:{action_type}",
        payload=(
            {
                "seed_text": "情绪决定工资",
                "name": "情绪公司",
                "genre": "都市奇幻",
                "target_platform": "hg",
                "target_audience": "18-35岁",
                "target_episode_count": 12,
                "episode_duration_seconds": 120,
                "cost_level": "low",
                "content_boundaries": [],
                "reference_refs": [],
                "assumptions": [],
            }
            if action_type == "project.create_original"
            else {}
        ),
        expected_context={"session_revision": 0},
        source_snapshot_id="snapshot-project-action",
    )
    runner_context.store.claim_pending_action(action["id"], expected_revision=1)
    runner_context.orchestrator.execute_project_action.return_value = {
        "project_id": 17,
        "name": "情绪公司",
        "open_url": "/planning/projects/17/brief",
    }

    runner_context.runner.run(action["id"], runner_context.actor)

    completed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    runner_context.orchestrator.execute_project_action.assert_called_once_with(
        action["id"], runner_context.actor
    )
    assert completed["status"] == "succeeded"
    assert completed["result"]["project_id"] == 17


def test_runner_materializes_a_confirmed_plan_when_no_steps_exist(
    runner_context,
) -> None:
    action = runner_context.store.create_action(
        session_id=runner_context.session["id"],
        actor_user_id=runner_context.actor.user_id,
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key="materialize-plan",
        request_hash="materialize-plan",
        payload={"target_stage": "outline"},
        expected_context={"project_revision": 4},
        source_snapshot_id="snapshot-materialize",
    )
    runner_context.store.claim_pending_action(action["id"], expected_revision=1)

    def materialize(action_id: str, _actor: Principal) -> list[dict]:
        runner_context.store.upsert_step(
            action_id=action_id,
            step_key="outline.generate",
            stage="outline",
            episode_start=None,
            episode_end=None,
            status="pending",
            operation_id=f"{action_id}:outline.generate",
            depends_on=[],
            attempt=1,
            step_order=1,
        )
        return runner_context.store.list_steps(action_id)

    runner_context.orchestrator.prepare_action_plan.side_effect = materialize

    runner_context.runner.run(action["id"], runner_context.actor)

    runner_context.orchestrator.prepare_action_plan.assert_called_once_with(
        action["id"], runner_context.actor
    )
    assert runner_context.executed == ["outline.generate"]
    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "succeeded"


def test_project_creation_can_continue_to_outline_in_one_confirmed_action(
    runner_context,
) -> None:
    action = runner_context.store.create_action(
        session_id=runner_context.session["id"],
        actor_user_id=runner_context.actor.user_id,
        action_type="project.create_original",
        project_id=None,
        idempotency_key="create-and-outline",
        request_hash="create-and-outline",
        payload={
            "seed_text": "情绪决定工资",
            "name": "情绪公司",
            "genre": "都市奇幻",
            "target_platform": "hg",
            "target_audience": "18-35岁",
            "target_episode_count": 12,
            "episode_duration_seconds": 120,
            "cost_level": "low",
            "content_boundaries": [],
            "reference_refs": [],
            "assumptions": [],
            "target_stage": "outline",
        },
        expected_context={"session_revision": 0},
        source_snapshot_id="snapshot-create-outline",
    )
    runner_context.store.claim_pending_action(action["id"], expected_revision=1)
    runner_context.orchestrator.execute_project_action.return_value = {
        "project_id": 17,
        "name": "情绪公司",
        "open_url": "/planning/projects/17/brief",
        "project_context": {"project_revision": 4},
    }

    def materialize(action_id: str, _actor: Principal) -> list[dict]:
        current = runner_context.store.require_action(action_id, None)
        assert current["project_id"] == 17
        runner_context.store.upsert_step(
            action_id=action_id,
            step_key="outline.generate",
            stage="outline",
            episode_start=None,
            episode_end=None,
            status="pending",
            operation_id=f"{action_id}:outline.generate",
            depends_on=[],
            attempt=1,
            step_order=1,
        )
        return runner_context.store.list_steps(action_id)

    runner_context.orchestrator.prepare_action_plan.side_effect = materialize

    runner_context.runner.run(action["id"], runner_context.actor)

    completed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert completed["status"] == "succeeded"
    assert completed["project_id"] == 17
    assert runner_context.executed == ["outline.generate"]


def test_project_creation_retry_reuses_project_and_only_runs_copied_failures(
    runner_context,
) -> None:
    """Retrying a continued create action must not create a second project."""
    store = runner_context.store
    action = store.create_action(
        session_id=runner_context.session["id"],
        actor_user_id=runner_context.actor.user_id,
        action_type="project.create_original",
        project_id=17,
        idempotency_key="create-script-partial",
        request_hash="create-script-partial",
        payload={"target_stage": "script", "episode_start": 1, "episode_end": 2},
        expected_context={"project_revision": 4},
        source_snapshot_id="snapshot-create-script-partial",
    )
    store.claim_pending_action(action["id"], expected_revision=1)
    store.start_action(action["id"])
    for order, episode in enumerate((1, 2), start=1):
        step_key = f"script.generate:{episode}"
        store.upsert_step(
            action_id=action["id"],
            step_key=step_key,
            stage="script",
            episode_start=episode,
            episode_end=episode,
            status="pending",
            operation_id=f"{action['id']}:{step_key}",
            depends_on=[],
            attempt=1,
            step_order=order,
        )
    store.finish_step(
        action["id"],
        "script.generate:1",
        {"resource_refs": [{"resource_id": "script-1"}]},
    )
    store.fail_step(
        action["id"],
        "script.generate:2",
        code="GENERATION_FAILED",
        message="episode 2",
        retryable=True,
    )
    store.fail_action(
        action["id"],
        code="GENERATION_FAILED",
        message="episode 2",
        retryable=True,
        result={"project_id": 17, "name": "情绪公司", "partial": True},
    )
    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)
    runner_context.orchestrator.execute_project_action.side_effect = AssertionError(
        "retry must not create another project"
    )
    runner_context.orchestrator.prepare_action_plan.side_effect = AssertionError(
        "retry must keep the copied failure-only plan"
    )

    runner_context.runner.run(retry["id"], runner_context.actor)

    completed = store.require_action(retry["id"], runner_context.actor.user_id)
    assert completed["status"] == "succeeded"
    assert completed["project_id"] == 17
    assert completed["result"]["project_id"] == 17
    assert completed["result"]["executed_episodes"] == [2]
    assert runner_context.executed == ["script.generate:2"]


@pytest.mark.parametrize(
    ("resource_refs", "explicit_resource_id", "expect_success"),
    [
        ([{"resource_id": "brief-101", "status": "draft"}], None, True),
        ([], None, False),
        (
            [
                {"resource_id": "brief-101", "status": "draft"},
                {"resource_id": "brief-102", "status": "draft"},
            ],
            None,
            False,
        ),
        ([{"resource_id": "brief-101", "status": "draft"}], "brief-999", False),
    ],
)
def test_lock_step_resolves_only_one_succeeded_dependency_resource(
    runner_context,
    resource_refs: list[dict],
    explicit_resource_id: str | None,
    expect_success: bool,
) -> None:
    """A lock must consume exactly one durable resource produced by its dependency."""
    snapshot = runner_context.store.save_source_snapshot({"source": "lock-test"})
    action = runner_context.store.create_action(
        session_id=runner_context.session["id"],
        actor_user_id=runner_context.actor.user_id,
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key=f"lock-resource:{len(resource_refs)}:{explicit_resource_id}",
        request_hash=f"lock-resource:{len(resource_refs)}:{explicit_resource_id}",
        payload={
            "target_stage": "characters",
            "step_payloads": (
                {"brief.lock": {"resource_id": explicit_resource_id}}
                if explicit_resource_id is not None
                else {}
            ),
        },
        expected_context={"project_revision": 4},
        source_snapshot_id=snapshot["id"],
    )
    runner_context.store.upsert_step(
        action_id=action["id"],
        step_key="brief.persist",
        stage="brief",
        episode_start=None,
        episode_end=None,
        status="pending",
        operation_id=f"{action['id']}:brief.persist",
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    runner_context.store.finish_step(
        action["id"], "brief.persist", {"resource_refs": resource_refs}
    )
    lock_step = runner_context.store.upsert_step(
        action_id=action["id"],
        step_key="brief.lock",
        stage="brief",
        episode_start=None,
        episode_end=None,
        status="pending",
        operation_id=f"{action['id']}:brief.lock",
        depends_on=["brief.persist"],
        attempt=1,
        step_order=2,
    )
    creative_service = MagicMock()
    creative_service.ensure_stage_task.return_value = {"id": "brief-task"}
    creative_service.lock_resource.return_value = {
        "resource_id": "brief-101", "status": "locked"
    }
    creative_service.advance_plan_context.return_value = {"revision": 5}
    orchestrator = _real_orchestrator(runner_context.store, creative_service)
    current_action = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )

    if not expect_success:
        with pytest.raises(WriterActionError) as exc:
            orchestrator.execute_action_step(
                current_action, lock_step, runner_context.actor,
                operation_id=lock_step["operation_id"],
            )
        assert exc.value.code == "RESULT_VALIDATION_FAILED"
        return

    orchestrator.execute_action_step(
        current_action, lock_step, runner_context.actor,
        operation_id=lock_step["operation_id"],
    )

    assert creative_service.lock_resource.call_args.kwargs["resource_id"] == "brief-101"


def test_runner_audits_success_failure_and_cancel_without_material_content(
    runner_context,
) -> None:
    records = []
    runner_context.runner.audit_service = SimpleNamespace(
        record=lambda **kwargs: records.append(kwargs)
    )

    succeeded = seed_runner_action(
        runner_context.store, runner_context.session, runner_context.actor,
        stage="outline", episodes=range(1, 2), project_revision=4,
    )
    runner_context.runner.run(succeeded["id"], runner_context.actor)

    failed = seed_runner_action(
        runner_context.store, runner_context.session, runner_context.actor,
        stage="beats", episodes=range(1, 2), project_revision=4,
    )
    runner_context.fail_once.add("beats.generate:1")
    runner_context.runner.run(failed["id"], runner_context.actor)

    cancelled = seed_runner_action(
        runner_context.store, runner_context.session, runner_context.actor,
        stage="script", episodes=range(1, 2), project_revision=4,
    )
    runner_context.runner.cancel(cancelled["id"], runner_context.actor)

    by_status = {record["after"]["status"]: record for record in records}
    assert set(by_status) == {"succeeded", "failed", "cancelled"}
    assert by_status["failed"]["after"]["error_code"] == "GENERATION_FAILED"
    assert all("content" not in record["after"] for record in records)


def test_runner_marks_stale_upstream_without_writing(runner_context) -> None:
    """Skipping the pre-side-effect context check would write against stale upstream."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="outline",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.project_revision = 5

    runner_context.runner.run(action["id"], runner_context.actor)

    result = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert result["error_code"] == "STALE_PROJECT_CONTEXT"
    assert runner_context.creative_calls == []


def test_series_generation_is_partitioned_into_ten_episode_batches() -> None:
    """Off-by-one partitioning would omit or duplicate an episode."""
    assert partition_episode_range(1, 23, batch_size=10) == [
        (1, 10),
        (11, 20),
        (21, 23),
    ]


def test_retry_preserves_failed_step_operation_id(runner_context) -> None:
    """Recomputing operation_id on retry can launch duplicate creative work."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    original = runner_context.store.list_steps(action["id"])[0]
    runner_context.fail_once.add(original["step_key"])
    runner_context.runner.run(action["id"], runner_context.actor)

    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)

    copied = runner_context.store.list_steps(retry["id"])[0]
    assert copied["operation_id"] == original["operation_id"]


def test_retry_runs_step_depending_on_successful_ancestor(runner_context) -> None:
    """A copied retry step must inherit successful dependencies from its source."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 3),
        project_revision=4,
        dependencies={"script.generate:2": ["script.generate:1"]},
    )
    runner_context.fail_once.add("script.generate:2")
    runner_context.runner.run(action["id"], runner_context.actor)

    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)
    runner_context.runner.run(retry["id"], runner_context.actor)

    assert runner_context.store.require_action(
        retry["id"], runner_context.actor.user_id
    )["status"] == "succeeded"
    assert [
        step["step_key"] for step in runner_context.store.list_steps(retry["id"])
    ] == ["script.generate:2"]


def test_retry_copies_only_retryable_failures(runner_context) -> None:
    """A permanent sibling failure must not poison the retryable subset."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 3),
        project_revision=4,
    )
    runner_context.store.start_action(action["id"])
    runner_context.store.fail_step(
        action["id"],
        "script.generate:1",
        code="GENERATION_FAILED",
        message="transient",
        retryable=True,
    )
    runner_context.store.fail_step(
        action["id"],
        "script.generate:2",
        code="STALE_PROJECT_CONTEXT",
        message="permanent",
        retryable=False,
    )
    runner_context.store.fail_action(
        action["id"],
        code="GENERATION_FAILED",
        message="partial",
        retryable=True,
    )

    retry = runner_context.runner.retry_failed(action["id"], runner_context.actor)

    assert [
        step["step_key"]
        for step in runner_context.store.list_steps(retry["id"])
    ] == ["script.generate:1"]


def test_failed_dependency_is_blocked_but_independent_episode_continues(
    runner_context,
) -> None:
    """Leaving dependents pending forever would prevent action finalization."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 4),
        project_revision=4,
        dependencies={"script.generate:2": ["script.generate:1"]},
    )
    runner_context.fail_once.add("script.generate:1")

    runner_context.runner.run(action["id"], runner_context.actor)

    states = {
        item["step_key"]: item["status"]
        for item in runner_context.store.list_steps(action["id"])
    }
    assert states == {
        "script.generate:1": "failed",
        "script.generate:2": "blocked",
        "script.generate:3": "succeeded",
    }


def test_two_runners_claim_a_non_job_step_only_once(runner_context) -> None:
    """A read-only runnable query lets two recovery processes repeat a side effect."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingOrchestrator:
        def assert_expected_context(self, _action, _actor):
            return None

        def execute_action_step(self, _action, step, _actor, *, operation_id):
            calls.append((step["step_key"], operation_id))
            entered.set()
            if len(calls) == 1:
                assert release.wait(5)
            return {"resource_refs": [{"resource_id": step["step_key"]}]}

    first = WriterActionRunner(
        store=runner_context.store,
        orchestrator=BlockingOrchestrator(),
        job_manager=runner_context.jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    second = WriterActionRunner(
        store=runner_context.store,
        orchestrator=BlockingOrchestrator(),
        job_manager=runner_context.jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    first_thread, first_done, first_errors = _run_in_thread(
        first, action["id"], runner_context.actor
    )
    assert entered.wait(5)
    second_thread, second_done, second_errors = _run_in_thread(
        second, action["id"], runner_context.actor
    )
    try:
        release.set()
        assert first_done.wait(5)
        assert second_done.wait(5)
        assert calls == [
            ("script.generate:1", runner_context.store.list_steps(action["id"])[0]["operation_id"])
        ]
    finally:
        release.set()
        first_thread.join(5)
        second_thread.join(5)
    assert first_done.is_set()
    assert first_errors == []
    assert second_errors == []
    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "succeeded"


def test_runner_reclaims_only_an_expired_non_job_step_lease(runner_context):
    """A crash after claim must be recoverable without stealing a live lease."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.store.start_action(action["id"])
    step = runner_context.store.list_steps(action["id"])[0]
    runner_context.store.claim_runnable_step(
        action["id"],
        step["step_key"],
        owner_id="dead-runner",
        lease_seconds=-1,
    )

    runner_context.runner.run(action["id"], runner_context.actor)

    assert runner_context.executed == ["script.generate:1"]
    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "succeeded"


class ConcreteCreativeService:
    """Small in-process creative boundary used with the real orchestrator."""

    def __init__(self, revision=4):
        self.project = {
            "id": 17,
            "revision": revision,
            "locked_brief_id": None,
            "character_batch_id": None,
            "outline_batch_id": None,
        }
        self.calls = []
        self.episode_beats = {}

    def get_project(self, project_id):
        assert project_id == 17
        return dict(self.project)

    def current_plan_context(self, project_id, episode_number=None):
        assert project_id == 17
        return {
            "revision": self.project["revision"],
            "locked_brief_id": self.project["locked_brief_id"],
            "character_batch_id": self.project["character_batch_id"],
            "outline_batch_id": self.project["outline_batch_id"],
            "beats": self.episode_beats.get(episode_number),
        }

    def assert_plan_context(self, project_id, expected_context, episode_number=None):
        current = self.current_plan_context(project_id, episode_number)
        if current != expected_context:
            from creative.application_service import CreativeContextConflict

            raise CreativeContextConflict("project authority changed")
        return current

    def ensure_stage_task(
        self, project_id, stage, actor, *, expected_context, episode_number=None
    ):
        del actor
        self.assert_plan_context(project_id, expected_context, episode_number)
        return {"id": f"{stage}-task"}

    def start_generation(self, **kwargs):
        self.assert_plan_context(
            kwargs["project_id"],
            kwargs["expected_context"],
            kwargs.get("episode_number"),
        )
        self.calls.append(("generate", kwargs["operation_id"]))
        if kwargs["stage"] == "brief":
            return {"resource_id": "101", "status": "succeeded"}
        return {"status": "succeeded"}

    def lock_resource(self, **kwargs):
        self.assert_plan_context(
            kwargs["project_id"],
            kwargs["expected_context"],
            kwargs.get("episode_number"),
        )
        self.project["revision"] += 1
        self.project["locked_brief_id"] = int(kwargs["resource_id"])
        self.calls.append(("lock", kwargs["operation_id"]))
        return {"resource_id": str(kwargs["resource_id"]), "status": "locked"}

    def advance_plan_context(
        self,
        project_id,
        expected_context,
        *,
        stage,
        kind,
        resource_id,
        episode_number,
    ):
        del expected_context, stage, kind, resource_id
        return self.current_plan_context(project_id, episode_number)


def _real_orchestrator(store, creative_service):
    return WriterProjectOrchestrator(
        store,
        creative_service,
        MagicMock(),
        MagicMock(),
        lambda *_args: True,
    )


def test_runner_uses_real_orchestrator_contract_and_advances_its_own_authority(
    tmp_path,
):
    """A mock-only runner can call methods the production orchestrator does not expose."""
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    session = store.create(actor.user_id, "执行测试", "权威推进")
    store.save_source_snapshot(
        {"snapshot_id": "snapshot-real"}, snapshot_id="snapshot-real"
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key="real-orchestrator-action",
        request_hash="real-orchestrator-action",
        payload={
            "target_stage": "characters",
            "step_payloads": {
                "brief.generate": {
                    "generation_instruction": "生成 Brief"
                },
                "brief.lock": {"resource_id": "101"},
                "characters.generate": {
                    "generation_instruction": "生成人物"
                },
            },
        },
        expected_context={"project_revision": 4},
        source_snapshot_id="snapshot-real",
    )
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.upsert_step(
        action_id=action["id"],
        step_key="brief.generate",
        stage="brief",
        episode_start=None,
        episode_end=None,
        status="pending",
        operation_id="real:brief.generate",
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    store.upsert_step(
        action_id=action["id"],
        step_key="brief.lock",
        stage="brief",
        episode_start=None,
        episode_end=None,
        status="pending",
        operation_id="real:brief.lock",
        depends_on=["brief.generate"],
        attempt=1,
        step_order=2,
    )
    store.upsert_step(
        action_id=action["id"],
        step_key="characters.generate",
        stage="characters",
        episode_start=None,
        episode_end=None,
        status="pending",
        operation_id="real:characters.generate",
        depends_on=["brief.lock"],
        attempt=1,
        step_order=3,
    )
    creative_service = ConcreteCreativeService()
    runner = WriterActionRunner(
        store=store,
        orchestrator=_real_orchestrator(store, creative_service),
        job_manager=MagicMock(),
        principal_loader=lambda _user_id: actor,
    )

    runner.run(action["id"], actor)

    completed = store.require_action(action["id"], actor.user_id)
    assert completed["status"] == "succeeded"
    assert completed["expected_context"]["project_revision"] == 5
    assert creative_service.calls == [
        ("generate", "real:brief.generate"),
        ("lock", "real:brief.lock"),
        ("generate", "real:characters.generate"),
    ]


def test_real_orchestrator_contract_translates_locked_episode_authority(tmp_path):
    """Passing snapshot resource rows as CAS context makes valid Script work stale."""
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    actor = Principal(
        user_id="writer-episode",
        username="writer-episode",
        display_name="分集编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-episode-session",
    )
    session = store.create(actor.user_id, "分集执行", "生成第一集")
    store.save_source_snapshot(
        {"snapshot_id": "snapshot-episode"}, snapshot_id="snapshot-episode"
    )
    beats_content = {"beats": [{"beat": "开场"}]}
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key="episode-authority",
        request_hash="episode-authority",
        payload={
            "target_stage": "script",
            "episode_start": 1,
            "episode_end": 1,
            "generation_instruction": "生成第一集剧本",
        },
        expected_context={
            "project_revision": 4,
            "locked_beats": [
                {
                    "id": 301,
                    "episode_number": 1,
                    "version": 2,
                    "content": beats_content,
                }
            ],
        },
        source_snapshot_id="snapshot-episode",
    )
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.upsert_step(
        action_id=action["id"],
        step_key="script.generate:1",
        stage="script",
        episode_start=1,
        episode_end=1,
        status="pending",
        operation_id="episode:script.generate:1",
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    service = ConcreteCreativeService()
    service.episode_beats[1] = {
        "resource_id": "301",
        "version": 2,
        "fingerprint": WriterProjectOrchestrator._fingerprint(beats_content),
    }
    runner = WriterActionRunner(
        store=store,
        orchestrator=_real_orchestrator(store, service),
        job_manager=MagicMock(),
        principal_loader=lambda _user_id: actor,
    )

    runner.run(action["id"], actor)

    assert store.require_action(action["id"], actor.user_id)["status"] == "succeeded"
    assert service.calls == [("generate", "episode:script.generate:1")]


class ControlledProcess:
    pid = 43210

    def __init__(self):
        self.stdout = None
        self.waiting = threading.Event()
        self.finished = threading.Event()
        self.return_code = 0

    def wait(self, timeout=None):
        self.waiting.set()
        assert self.finished.wait(5 if timeout is None else timeout)
        return self.return_code

    def poll(self):
        return self.return_code if self.finished.is_set() else None

    def terminate(self):
        self.return_code = -15
        self.finished.set()

    def kill(self):
        self.return_code = -9
        self.finished.set()

    def complete(self):
        self.return_code = 0
        self.finished.set()

    def fail(self):
        self.return_code = 1
        self.finished.set()


class JobLaunchingOrchestrator:
    def __init__(self, jobs, process_payload):
        self.jobs = jobs
        self.process_payload = process_payload
        self.execute_calls = 0

    def assert_expected_context(self, _action, _actor):
        return None

    def execute_action_step(self, action, step, _actor, *, operation_id):
        self.execute_calls += 1
        state = self.jobs.get_or_create_job(
            operation_id,
            self.process_payload,
            created_by_user_id=action["actor_user_id"],
            workflow_task_id="script-task",
            project_id=action["project_id"],
        )
        return {"job_id": state.job_id, "status": state.status}


def _run_in_thread(runner, action_id, actor):
    done = threading.Event()
    errors = []

    def target():
        try:
            runner.run(action_id, actor)
        except Exception as exc:  # pragma: no cover - surfaced by the assertion
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=target)
    thread.start()
    return thread, done, errors


def _job_payload(job_id):
    return {
        "job_id": job_id,
        "platform": "creative",
        "crawler_type": "phase4_generate",
        "extra": {
            "action": "script",
            "project_id": 17,
            "episode_number": 1,
        },
    }


def test_runner_recovers_job_created_before_step_job_id_and_polls_real_manager(
    monkeypatch, runner_context, tmp_path
):
    """A crash between job reservation and step update must not launch a duplicate."""
    process = ControlledProcess()
    launches = []

    def launch(*_args, **_kwargs):
        launches.append("launched")
        return process

    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    step = runner_context.store.list_steps(action["id"])[0]
    jobs.get_or_create_job(
        step["operation_id"],
        _job_payload("reserved-before-step"),
        created_by_user_id=runner_context.actor.user_id,
        workflow_task_id="script-task",
        project_id=17,
    )
    orchestrator = JobLaunchingOrchestrator(
        jobs, _job_payload("must-not-be-launched")
    )
    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=orchestrator,
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    assigned = threading.Event()
    original_assign = runner_context.store.assign_step_job

    def assign_step_job(action_id, step_key, job_id, **kwargs):
        result = original_assign(action_id, step_key, job_id, **kwargs)
        assigned.set()
        return result

    monkeypatch.setattr(runner_context.store, "assign_step_job", assign_step_job)
    thread, done, errors = _run_in_thread(
        runner, action["id"], runner_context.actor
    )
    assert assigned.wait(5)
    process.complete()
    assert done.wait(5)
    thread.join()

    assert errors == []
    assert launches == ["launched"]
    completed_step = runner_context.store.list_steps(action["id"])[0]
    assert completed_step["job_id"] == "reserved-before-step"
    assert completed_step["status"] == "succeeded"


def test_runner_resumes_an_assigned_job_without_executing_step_again(
    monkeypatch, runner_context, tmp_path
):
    """Treating a running persisted step as complete loses its worker outcome on restart."""
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    step = runner_context.store.list_steps(action["id"])[0]
    state = jobs.get_or_create_job(
        step["operation_id"],
        _job_payload("persisted-running-job"),
        created_by_user_id=runner_context.actor.user_id,
        workflow_task_id="script-task",
        project_id=17,
    )
    runner_context.store.start_action(action["id"])
    runner_context.store.assign_step_job(
        action["id"], step["step_key"], state.job_id
    )
    orchestrator = JobLaunchingOrchestrator(jobs, _job_payload("duplicate-job"))
    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=orchestrator,
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    reconciled = threading.Event()
    original_get_job = jobs.get_job

    def get_job(job_id):
        result = original_get_job(job_id)
        reconciled.set()
        return result

    monkeypatch.setattr(jobs, "get_job", get_job)

    thread, done, errors = _run_in_thread(
        runner, action["id"], runner_context.actor
    )
    assert process.waiting.wait(5)
    assert reconciled.wait(5)
    assert not done.is_set()
    process.complete()
    assert done.wait(5)
    thread.join()

    assert errors == []
    assert orchestrator.execute_calls == 0
    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "succeeded"


def test_succeeded_job_recovery_restores_result_and_authority(runner_context):
    """Completing a recovered job with empty refs leaves its dependent step stale."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 3),
        project_revision=4,
        dependencies={"script.generate:2": ["script.generate:1"]},
    )
    first = runner_context.store.list_steps(action["id"])[0]
    runner_context.store.start_action(action["id"])
    runner_context.store.assign_step_job(
        action["id"], first["step_key"], "completed-job"
    )
    recovered = []
    executed = []

    class CompletedJobs:
        def get_job(self, job_id):
            assert job_id == "completed-job"
            return {"job_id": job_id, "status": "succeeded"}

    class RecoveryOrchestrator:
        def recover_action_step_result(self, current_action, step, actor):
            del current_action, actor
            recovered.append(step["operation_id"])
            return {
                "resource_refs": [{"resource_id": "episode-1"}],
                "authority_revision": 5,
                "authority_refs": {
                    "script.generate:2": {"revision": 5}
                },
            }

        def assert_expected_context(self, current_action, _actor):
            assert current_action["expected_context"]["project_revision"] == 5

        def execute_action_step(self, _action, step, _actor, *, operation_id):
            executed.append((step["step_key"], operation_id))
            return {"resource_refs": [{"resource_id": "episode-2"}]}

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=RecoveryOrchestrator(),
        job_manager=CompletedJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    steps = runner_context.store.list_steps(action["id"])
    assert recovered == [first["operation_id"]]
    assert steps[0]["resource_refs"] == [{"resource_id": "episode-1"}]
    assert executed == [("script.generate:2", steps[1]["operation_id"])]
    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "succeeded"


def test_missing_assigned_job_becomes_persisted_retryable_failure(
    runner_context,
):
    """A missing state file must not leave the action permanently running."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.store.start_action(action["id"])
    runner_context.store.assign_step_job(
        action["id"], "script.generate:1", "missing-job"
    )

    class MissingJobs:
        def get_job(self, _job_id):
            from runtime.job_manager import JobNotFoundError

            raise JobNotFoundError("missing-job")

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=runner_context.orchestrator,
        job_manager=MissingJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    failed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == "JOB_NOT_FOUND"
    assert failed["retryable"] is True


def test_job_disappearing_after_binding_becomes_stable_failure(runner_context):
    """A launch-to-poll disappearance must be handled like restart reconciliation."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )

    class VanishingOrchestrator:
        def assert_expected_context(self, _action, _actor):
            return None

        def execute_action_step(self, _action, _step, _actor, *, operation_id):
            del operation_id
            return {"job_id": "vanished-job"}

    class VanishingJobs:
        def get_job(self, _job_id):
            from runtime.job_manager import JobNotFoundError

            raise JobNotFoundError("vanished-job")

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=VanishingOrchestrator(),
        job_manager=VanishingJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    failed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == "JOB_NOT_FOUND"


def test_real_orchestrator_rehydrates_completed_generation_for_dependents(
    runner_context,
):
    """The production adapter must rebuild domain refs after a worker-only success."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 3),
        project_revision=4,
        dependencies={"script.generate:2": ["script.generate:1"]},
    )
    first = runner_context.store.list_steps(action["id"])[0]
    creative_service = MagicMock()
    creative_service.recover_generation_result.return_value = {
        "resource_refs": [{"resource_id": "episode-1"}]
    }
    creative_service.advance_plan_context.return_value = {
        "revision": 5,
        "beats": {"resource_id": "beats-2"},
    }
    orchestrator = _real_orchestrator(runner_context.store, creative_service)

    result = orchestrator.recover_action_step_result(
        action, first, runner_context.actor
    )

    assert result["resource_refs"] == [{"resource_id": "episode-1"}]
    assert result["authority_revision"] == 5
    assert result["authority_refs"] == {
        "script.generate:2": {
            "revision": 5,
            "beats": {"resource_id": "beats-2"},
        }
    }
    creative_service.recover_generation_result.assert_called_once_with(
        operation_id=first["operation_id"],
        project_id=17,
        stage="script",
    )


def test_unrecoverable_completed_job_persists_a_stable_step_error(runner_context):
    """Hydration failure must not escape the runner and strand a running action."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.store.start_action(action["id"])
    runner_context.store.assign_step_job(
        action["id"], "script.generate:1", "completed-without-result"
    )

    class CompletedJobs:
        def get_job(self, job_id):
            return {"job_id": job_id, "status": "succeeded"}

    class BrokenRecovery:
        def recover_action_step_result(self, _action, _step, _actor):
            raise WriterActionError(
                "RESULT_VALIDATION_FAILED",
                "已完成生成任务的结果无法恢复",
                retryable=False,
            )

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=BrokenRecovery(),
        job_manager=CompletedJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    failed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == "RESULT_VALIDATION_FAILED"
    assert failed["retryable"] is False


def test_cancelling_action_reconciles_real_job_to_cancelled(
    monkeypatch, runner_context, tmp_path
):
    """Mapping a requested cancellation to failed leaves the public action state wrong."""
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=JobLaunchingOrchestrator(jobs, _job_payload("cancel-job")),
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    assigned = threading.Event()
    original_assign = runner_context.store.assign_step_job

    def assign_step_job(action_id, step_key, job_id, **kwargs):
        result = original_assign(action_id, step_key, job_id, **kwargs)
        assigned.set()
        return result

    monkeypatch.setattr(runner_context.store, "assign_step_job", assign_step_job)
    thread, done, errors = _run_in_thread(
        runner, action["id"], runner_context.actor
    )
    assert assigned.wait(5)

    cancelling = runner.cancel(action["id"], runner_context.actor)

    assert cancelling["phase"] == "cancelling"
    assert done.wait(5)
    thread.join()
    assert errors == []
    completed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert completed["status"] == "cancelled"
    assert runner_context.store.list_steps(action["id"])[0]["status"] == "cancelled"


def test_cancelling_action_reclaims_expired_unbound_step(runner_context):
    """A crashed non-job executor must not strand cancellation forever."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.store.start_action(action["id"])
    step = runner_context.store.list_steps(action["id"])[0]
    claimed = runner_context.store.claim_runnable_step(
        action["id"],
        step["step_key"],
        owner_id="crashed-runner",
        lease_seconds=-1,
    )
    assert claimed is not None

    cancelling = runner_context.runner.cancel(action["id"], runner_context.actor)
    assert cancelling["phase"] == "cancelling"
    assert cancelling["status"] == "running"

    reconciled = runner_context.runner.reconcile(
        action["id"], runner_context.actor
    )
    assert reconciled["status"] == "cancelled"
    assert runner_context.store.list_steps(action["id"])[0]["status"] == "cancelled"


def test_cancel_waits_for_job_created_before_step_binding(
    monkeypatch, runner_context, tmp_path
):
    """Cancelling in the launch-to-bind window must still stop the worker."""
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    job_created = threading.Event()
    release_result = threading.Event()

    class LaunchThenBlockOrchestrator(JobLaunchingOrchestrator):
        def execute_action_step(self, action, step, actor, *, operation_id):
            result = super().execute_action_step(
                action, step, actor, operation_id=operation_id
            )
            job_created.set()
            assert release_result.wait(5)
            return result

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=LaunchThenBlockOrchestrator(
            jobs, _job_payload("cancel-before-bind")
        ),
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    thread, done, errors = _run_in_thread(
        runner, action["id"], runner_context.actor
    )
    assert job_created.wait(5)

    cancelling = runner.cancel(action["id"], runner_context.actor)

    assert cancelling["status"] == "running"
    assert cancelling["phase"] == "cancelling"
    assert not process.finished.is_set()
    reconciled_during_launch = runner.reconcile(
        action["id"], runner_context.actor
    )
    assert reconciled_during_launch["status"] == "running"
    release_result.set()
    assert done.wait(5)
    thread.join(5)
    assert errors == []
    assert process.finished.is_set()
    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "cancelled"


def test_resumed_failed_job_keeps_stable_retryable_generation_error(
    monkeypatch, runner_context, tmp_path
):
    """Recovery reconciliation must not turn a retryable worker failure permanent."""
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    step = runner_context.store.list_steps(action["id"])[0]
    state = jobs.get_or_create_job(
        step["operation_id"],
        _job_payload("failed-resumed-job"),
        created_by_user_id=runner_context.actor.user_id,
        workflow_task_id="script-task",
        project_id=17,
    )
    runner_context.store.start_action(action["id"])
    runner_context.store.assign_step_job(
        action["id"], step["step_key"], state.job_id
    )
    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=JobLaunchingOrchestrator(jobs, _job_payload("duplicate-job")),
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )

    thread, done, errors = _run_in_thread(
        runner, action["id"], runner_context.actor
    )
    assert process.waiting.wait(5)
    process.fail()
    assert done.wait(5)
    thread.join()

    assert errors == []
    failed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert failed["status"] == "failed"
    assert failed["error_code"] == "GENERATION_FAILED"
    assert failed["retryable"] is True


def test_real_failed_job_retry_relaunches_same_operation(
    monkeypatch, runner_context, tmp_path
):
    """A retry action must relaunch the failed reservation and then succeed."""
    first_process = ControlledProcess()
    second_process = ControlledProcess()
    processes = [first_process, second_process]
    launches = []

    def launch(*_args, **_kwargs):
        launches.append("launched")
        return processes[len(launches) - 1]

    monkeypatch.setattr("runtime.job_manager.launch_worker", launch)
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )

    class RetryAwareJobOrchestrator(JobLaunchingOrchestrator):
        def execute_action_step(self, action, step, actor, *, operation_id):
            payload = {
                **self.process_payload,
                "extra": dict(self.process_payload["extra"]),
            }
            if int(step.get("attempt") or 1) > 1:
                payload["extra"]["retry_failed_operation"] = True
            self.process_payload = payload
            return super().execute_action_step(
                action, step, actor, operation_id=operation_id
            )

    orchestrator = RetryAwareJobOrchestrator(
        jobs, _job_payload("stable-operation-job")
    )
    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=orchestrator,
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )
    first_thread, first_done, first_errors = _run_in_thread(
        runner, action["id"], runner_context.actor
    )
    assert first_process.waiting.wait(5)
    first_process.fail()
    assert first_done.wait(5)
    first_thread.join(5)
    assert first_errors == []

    retry = runner.retry_failed(action["id"], runner_context.actor)
    retry_thread, retry_done, retry_errors = _run_in_thread(
        runner, retry["id"], runner_context.actor
    )
    assert second_process.waiting.wait(5)
    second_process.complete()
    assert retry_done.wait(5)
    retry_thread.join(5)

    assert retry_errors == []
    assert launches == ["launched", "launched"]
    assert runner_context.store.require_action(
        retry["id"], runner_context.actor.user_id
    )["status"] == "succeeded"
    retry_step = runner_context.store.list_steps(retry["id"])[0]
    assert retry_step["operation_id"] == runner_context.store.list_steps(
        action["id"]
    )[0]["operation_id"]
    assert retry_step["job_id"] == "stable-operation-job"


def test_runner_persists_stable_conflict_error_fields(runner_context) -> None:
    """Dropping the conflict identity prevents clients from resolving the blocking action."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.orchestrator.assert_expected_context.side_effect = WriterActionError(
        "ACTIVE_ACTION_CONFLICT",
        "目标范围已有进行中的动作",
        retryable=True,
        conflicting_action_id="action-blocking",
    )

    runner_context.runner.run(action["id"], runner_context.actor)

    failed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert failed["error_code"] == "ACTIVE_ACTION_CONFLICT"
    assert failed["retryable"] is True
    assert failed["conflicting_action_id"] == "action-blocking"


def test_real_job_payload_conflict_is_persisted_as_non_retryable_idempotency_error(
    monkeypatch, tmp_path
):
    """A JobManager payload clash must not be flattened into a retryable failure."""
    repository = CreativeRepository(tmp_path / "creative.db")
    workflow = MagicMock()
    workflow.is_active_project_member.return_value = True
    workflow.ensure_project_task.return_value = {
        "id": "script-task",
        "status": "in_progress",
        "assignee_user_id": "writer-1",
    }
    permissions = MagicMock()
    permissions.can_create_project_content.return_value = True
    workflow_adapter = MagicMock(unsafe=True)
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    service = CreativeApplicationService(
        repository=repository,
        workflow_repository=workflow,
        transitions=MagicMock(),
        job_manager=jobs,
        runtime_dir=tmp_path,
        original_project_service=MagicMock(),
        workflow_adapter=workflow_adapter,
        permission_service=permissions,
    )
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    project, _ = repository.ensure_original_project(
        seed_text="同一 operation 不能变更请求",
        name="幂等冲突",
        target_episode_count=1,
        created_by=actor.user_id,
        creation_request_id="job-conflict-project",
    )
    expected_context = service.current_plan_context(project["id"], 1)
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create(actor.user_id, "冲突传播", "测试")
    store.save_source_snapshot(
        {"snapshot_id": "job-conflict-snapshot"},
        snapshot_id="job-conflict-snapshot",
    )
    blocking = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="payload-conflict-blocking",
        request_hash="payload-conflict-blocking",
        payload={"target_stage": "script"},
        expected_context={"project_revision": expected_context["revision"]},
        source_snapshot_id="job-conflict-snapshot",
    )
    operation_id = f"{blocking['id']}:script.generate:1"
    store.upsert_step(
        action_id=blocking["id"],
        step_key="script.generate:1",
        stage="script",
        episode_start=1,
        episode_end=1,
        status="pending",
        operation_id=operation_id,
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    service.start_generation(
        project_id=project["id"],
        stage="script",
        actor=actor,
        workflow_task_id="script-task",
        operation_id=operation_id,
        generation_instruction="初始请求",
        source_snapshot_id="job-conflict-snapshot",
        source_snapshot={"snapshot_id": "job-conflict-snapshot"},
        episode_number=1,
        episode_start=1,
        episode_end=1,
        expected_context=expected_context,
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="payload-conflict-target",
        request_hash="payload-conflict-target",
        payload={
            "target_stage": "script",
            "episode_start": 1,
            "episode_end": 1,
            "step_payloads": {
                "script.generate:1": {"generation_instruction": "变更请求"}
            },
        },
        expected_context={
            "project_revision": expected_context["revision"],
            "authority_refs": {"script.generate:1": expected_context},
        },
        source_snapshot_id="job-conflict-snapshot",
    )
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.upsert_step(
        action_id=action["id"],
        step_key="script.generate:1",
        stage="script",
        episode_start=1,
        episode_end=1,
        status="pending",
        operation_id=operation_id,
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    orchestrator = WriterProjectOrchestrator(
        store, service, permissions, workflow, lambda *_args: True
    )
    step = store.list_steps(action["id"])[0]

    with pytest.raises(WriterActionError) as exc:
        orchestrator.execute_action_step(action, step, actor, operation_id=operation_id)

    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert exc.value.retryable is False
    process.complete()


def test_real_job_scope_conflict_keeps_owning_action_id_and_is_retryable(
    monkeypatch, tmp_path
):
    """A durable creative scope collision must identify the action holding it."""
    repository = CreativeRepository(tmp_path / "creative.db")
    workflow = MagicMock()
    workflow.is_active_project_member.return_value = True
    workflow.ensure_project_task.return_value = {
        "id": "script-task",
        "status": "in_progress",
        "assignee_user_id": "writer-1",
    }
    permissions = MagicMock()
    permissions.can_create_project_content.return_value = True
    workflow_adapter = MagicMock(unsafe=True)
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    service = CreativeApplicationService(
        repository=repository,
        workflow_repository=workflow,
        transitions=MagicMock(),
        job_manager=jobs,
        runtime_dir=tmp_path,
        original_project_service=MagicMock(),
        workflow_adapter=workflow_adapter,
        permission_service=permissions,
    )
    actor = Principal(
        user_id="writer-1",
        username="writer",
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"project.create"}),
        data_scope="project",
        session_id="writer-session",
    )
    project, _ = repository.ensure_original_project(
        seed_text="相同创作范围只能有一个活动 Worker",
        name="范围冲突",
        target_episode_count=1,
        created_by=actor.user_id,
        creation_request_id="scope-conflict-project",
    )
    expected_context = service.current_plan_context(project["id"], 1)
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create(actor.user_id, "冲突传播", "测试")
    store.save_source_snapshot(
        {"snapshot_id": "scope-conflict-snapshot"},
        snapshot_id="scope-conflict-snapshot",
    )
    blocking = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="scope-conflict-blocking",
        request_hash="scope-conflict-blocking",
        payload={"target_stage": "script"},
        expected_context={"project_revision": expected_context["revision"]},
        source_snapshot_id="scope-conflict-snapshot",
    )
    blocking_operation = f"{blocking['id']}:script.generate:1"
    store.upsert_step(
        action_id=blocking["id"],
        step_key="script.generate:1",
        stage="script",
        episode_start=1,
        episode_end=1,
        status="pending",
        operation_id=blocking_operation,
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    service.start_generation(
        project_id=project["id"],
        stage="script",
        actor=actor,
        workflow_task_id="script-task",
        operation_id=blocking_operation,
        generation_instruction="占用范围",
        source_snapshot_id="scope-conflict-snapshot",
        source_snapshot={"snapshot_id": "scope-conflict-snapshot"},
        episode_number=1,
        episode_start=1,
        episode_end=1,
        expected_context=expected_context,
    )
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=project["id"],
        idempotency_key="scope-conflict-target",
        request_hash="scope-conflict-target",
        payload={
            "target_stage": "script",
            "episode_start": 1,
            "episode_end": 1,
            "step_payloads": {
                "script.generate:1": {"generation_instruction": "冲突请求"}
            },
        },
        expected_context={
            "project_revision": expected_context["revision"],
            "authority_refs": {"script.generate:1": expected_context},
        },
        source_snapshot_id="scope-conflict-snapshot",
    )
    action = store.claim_pending_action(action["id"], expected_revision=1)
    store.upsert_step(
        action_id=action["id"],
        step_key="script.generate:1",
        stage="script",
        episode_start=1,
        episode_end=1,
        status="pending",
        operation_id=f"{action['id']}:script.generate:1",
        depends_on=[],
        attempt=1,
        step_order=1,
    )
    runner = WriterActionRunner(
        store=store,
        orchestrator=WriterProjectOrchestrator(
            store, service, permissions, workflow, lambda *_args: True
        ),
        job_manager=jobs,
        principal_loader=lambda _user_id: actor,
    )

    runner.run(action["id"], actor)

    failed = store.require_action(action["id"], actor.user_id)
    assert failed["error_code"] == "ACTIVE_ACTION_CONFLICT"
    assert failed["retryable"] is True
    assert failed["conflicting_action_id"] == blocking["id"]
    process.complete()


def test_authority_advance_preserves_unrelated_episode_contexts(runner_context):
    """Replacing the authority map after one Beats lock makes sibling episodes stale."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="beats",
        episodes=range(1, 4),
        project_revision=4,
        dependencies={
            "beats.generate:2": ["beats.generate:1"],
            "beats.generate:3": ["beats.generate:1"],
        },
    )

    class AuthorityOrchestrator:
        def assert_expected_context(self, current_action, _actor):
            if (
                runner_context.executed == [
                    "beats.generate:1",
                    "beats.generate:2",
                ]
                and "beats.generate:3"
                not in current_action["expected_context"].get(
                    "authority_refs", {}
                )
            ):
                raise WriterActionError(
                    "STALE_PROJECT_CONTEXT", "同集权威上下文丢失"
                )

        def execute_action_step(
            self, _action, step, _actor, *, operation_id
        ):
            del operation_id
            runner_context.executed.append(step["step_key"])
            if step["step_key"] == "beats.generate:1":
                return {
                    "authority_revision": 5,
                    "authority_refs": {
                        "beats.generate:2": {"revision": 5},
                        "beats.generate:3": {"revision": 5},
                    },
                }
            if step["step_key"] == "beats.generate:2":
                return {
                    "authority_revision": 5,
                    "authority_refs": {
                        "script.generate:2": {"revision": 5}
                    },
                }
            return {}

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=AuthorityOrchestrator(),
        job_manager=runner_context.jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    assert runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )["status"] == "succeeded"


def test_twenty_three_episode_plan_keeps_independent_operations(runner_context):
    """Turning a ten-episode scheduling batch into one step destroys partial retry."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 24),
        project_revision=4,
    )

    runner_context.runner.run(action["id"], runner_context.actor)

    steps = runner_context.store.list_steps(action["id"])
    assert len(steps) == 23
    assert len({step["operation_id"] for step in steps}) == 23
    assert all(step["episode_start"] == step["episode_end"] for step in steps)


def test_runner_launches_ten_episode_jobs_before_polling(runner_context):
    """Polling each episode immediately turns the ten-episode batch into serial work."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 12),
        project_revision=4,
    )
    launched = []
    poll_launch_counts = []

    class BatchOrchestrator:
        def assert_expected_context(self, _action, _actor):
            return None

        def execute_action_step(self, _action, step, _actor, *, operation_id):
            launched.append(step["step_key"])
            return {
                "job_id": f"job-{step['episode_start']}",
                "operation_id": operation_id,
            }

    class CompletedJobs:
        def get_job(self, job_id):
            poll_launch_counts.append(len(launched))
            return {"job_id": job_id, "status": "succeeded"}

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=BatchOrchestrator(),
        job_manager=CompletedJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    assert poll_launch_counts[0] == 10
    assert launched == [f"script.generate:{episode}" for episode in range(1, 12)]


@pytest.mark.asyncio
async def test_webgui_startup_resumes_writer_actions(monkeypatch):
    """Only constructing the runner leaves persisted active actions idle after restart."""
    monkeypatch.setenv(
        "AIWORKER_SYSTEM_DATABASE_URL",
        os.environ["AIWORKER_TEST_SYSTEM_DATABASE_URL"],
    )
    import WebGUI

    resumed = threading.Event()
    monkeypatch.setattr(
        WebGUI.writer_action_runner,
        "resume_active_actions",
        resumed.set,
    )
    monkeypatch.setattr(WebGUI.job_manager, "list_jobs", lambda: [])
    monkeypatch.setattr(WebGUI.state, "process", None)

    async with WebGUI.lifespan(WebGUI.app):
        assert resumed.is_set()

    assert isinstance(WebGUI.writer_action_runner, WriterActionRunner)
    assert WebGUI.app.state.writer_action_runner is WebGUI.writer_action_runner


def test_cancelling_restart_recovers_unbound_operation_job_before_finishing(
    monkeypatch, runner_context, tmp_path
):
    """A cancel must find the durable operation job before reclaiming an unbound step."""
    process = ControlledProcess()
    monkeypatch.setattr(
        "runtime.job_manager.launch_worker", lambda *_args, **_kwargs: process
    )
    jobs = JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.store.start_action(action["id"])
    step = runner_context.store.list_steps(action["id"])[0]
    runner_context.store.claim_runnable_step(
        action["id"],
        step["step_key"],
        owner_id="crashed-runner",
        lease_seconds=-1,
    )
    jobs.get_or_create_job(
        step["operation_id"],
        _job_payload("unbound-cancel-job"),
        created_by_user_id=runner_context.actor.user_id,
        workflow_task_id="script-task",
        project_id=17,
    )
    runner_context.store.set_action_phase(action["id"], phase="cancelling")
    restarted = WriterActionRunner(
        store=runner_context.store,
        orchestrator=runner_context.orchestrator,
        job_manager=jobs,
        principal_loader=lambda _user_id: runner_context.actor,
    )

    reconciled = restarted.reconcile(action["id"], runner_context.actor)

    assert reconciled["status"] == "cancelled"
    recovered = runner_context.store.list_steps(action["id"])[0]
    assert recovered["job_id"] == "unbound-cancel-job"
    assert recovered["status"] == "cancelled"
    assert process.poll() == -15


def test_runner_completes_pointer_step_before_launching_runnable_job(
    runner_context,
):
    """Mixing a lock into a generation batch can stale the job with our own revision."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )
    runner_context.store.upsert_step(
        action_id=action["id"],
        step_key="beats.lock:2",
        stage="beats",
        episode_start=2,
        episode_end=2,
        status="pending",
        operation_id=f"{action['id']}:beats.lock:2",
        depends_on=[],
        attempt=1,
        step_order=2,
    )
    executed = []

    class MixedOrchestrator:
        def assert_expected_context(self, _action, _actor):
            return None

        def execute_action_step(self, _action, step, _actor, *, operation_id):
            del operation_id
            executed.append(step["step_key"])
            if ".generate" in step["step_key"]:
                return {"job_id": "script-job"}
            return {"authority_revision": 5, "authority_refs": {}}

        def recover_action_step_result(self, _action, _step, _actor):
            return {"resource_refs": [{"resource_id": "script-1"}]}

    class CompletedJobs:
        def get_job(self, job_id):
            return {"job_id": job_id, "status": "succeeded"}

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=MixedOrchestrator(),
        job_manager=CompletedJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    assert executed == ["beats.lock:2", "script.generate:1"]


def test_unrequested_cancelled_job_cannot_finish_action_as_succeeded(
    runner_context,
):
    """A terminal cancelled step must be reflected in the public action status."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 2),
        project_revision=4,
    )

    class CancelledOrchestrator:
        def assert_expected_context(self, _action, _actor):
            return None

        def execute_action_step(self, _action, _step, _actor, *, operation_id):
            del operation_id
            return {"job_id": "externally-cancelled-job"}

    class CancelledJobs:
        def get_job(self, job_id):
            return {"job_id": job_id, "status": "cancelled"}

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=CancelledOrchestrator(),
        job_manager=CancelledJobs(),
        principal_loader=lambda _user_id: runner_context.actor,
    )

    runner.run(action["id"], runner_context.actor)

    completed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert completed["status"] == "cancelled"
    assert runner_context.store.list_steps(action["id"])[0]["status"] == "cancelled"


def test_runner_reloads_principal_before_each_step_side_effect(runner_context):
    """Disabling an account mid-action must prevent every later write."""
    action = seed_runner_action(
        runner_context.store,
        runner_context.session,
        runner_context.actor,
        stage="script",
        episodes=range(1, 3),
        project_revision=4,
    )
    loads = 0
    executed = []

    def load_principal(_user_id):
        nonlocal loads
        loads += 1
        return runner_context.actor if loads == 1 else None

    class ImmediateOrchestrator:
        def assert_expected_context(self, _action, _actor):
            return None

        def execute_action_step(self, _action, step, _actor, *, operation_id):
            del operation_id
            executed.append(step["step_key"])
            return {"resource_refs": [{"resource_id": step["step_key"]}]}

    runner = WriterActionRunner(
        store=runner_context.store,
        orchestrator=ImmediateOrchestrator(),
        job_manager=runner_context.jobs,
        principal_loader=load_principal,
    )

    runner.run(action["id"], runner_context.actor)

    completed = runner_context.store.require_action(
        action["id"], runner_context.actor.user_id
    )
    assert executed == ["script.generate:1"]
    assert completed["status"] == "failed"
    assert completed["error_code"] == "PROJECT_ACCESS_DENIED"
    assert completed["retryable"] is False
