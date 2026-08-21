# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import pytest

from creative.repository import canonical_request_hash
from writer_agent.schema import WriterActionError
from writer_agent.store import WriterAgentStore


def seed_action(
    store: WriterAgentStore, *, key: str, request_hash: str
) -> dict[str, Any]:
    session = store.create("writer-1", "测试动作", "一句话灵感")
    return store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="project.link",
        project_id=9,
        idempotency_key=key,
        request_hash=request_hash,
        payload={"project_id": 9},
        expected_context={"session_revision": session["context_revision"]},
        source_snapshot_id=None,
    )


def seed_run_plan(
    store: WriterAgentStore, *, key: str, project_id: int, episodes: tuple[int, int]
) -> dict[str, Any]:
    session = store.create("writer-1", key, "生成剧本")
    payload = {
        "target_stage": "script",
        "episode_start": episodes[0],
        "episode_end": episodes[1],
    }
    return store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="creative.run_plan",
        project_id=project_id,
        idempotency_key=key,
        request_hash=canonical_request_hash(payload),
        payload=payload,
        expected_context={"session_revision": session["context_revision"]},
        source_snapshot_id=None,
    )


def test_store_migrates_session_and_claims_action_once(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "旧剧本", "测试")
    action = store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="project.link",
        project_id=9,
        idempotency_key="session-1-link-9",
        request_hash="hash-1",
        payload={"project_id": 9},
        expected_context={},
        source_snapshot_id=None,
    )

    claimed = store.claim_pending_action(action["id"], expected_revision=1)
    assert claimed["status"] == "queued"
    with pytest.raises(WriterActionError, match="ACTION_NOT_PENDING"):
        store.claim_pending_action(action["id"], expected_revision=1)


def test_store_replays_the_same_decision_request(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    action = seed_action(store, key="decision", request_hash="decision")

    first = store.claim_pending_action(
        action["id"], expected_revision=1, client_request_id="confirm-1"
    )
    second = store.claim_pending_action(
        action["id"], expected_revision=1, client_request_id="confirm-1"
    )

    assert second["id"] == first["id"]
    assert second["status"] == "queued"


def test_store_reuses_same_idempotency_key_and_rejects_new_payload(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    first = seed_action(store, key="same", request_hash="a")

    assert seed_action(store, key="same", request_hash="a")["id"] == first["id"]
    with pytest.raises(WriterActionError, match="IDEMPOTENCY_KEY_REUSED"):
        seed_action(store, key="same", request_hash="b")


def test_store_expires_only_unconfirmed_actions(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    pending = seed_action(store, key="pending", request_hash="pending")
    queued = seed_action(store, key="queued", request_hash="queued")
    store.claim_pending_action(queued["id"], expected_revision=1)

    store.expire_pending_actions(older_than=time.time() + 1)

    assert store.require_action(pending["id"], "writer-1")["status"] == "expired"
    assert store.require_action(queued["id"], "writer-1")["status"] == "queued"


def test_two_store_instances_cannot_claim_overlapping_targets(tmp_path: Path) -> None:
    path = tmp_path / "writer-agent.db"
    first_store, second_store = WriterAgentStore(path), WriterAgentStore(path)
    first = seed_run_plan(first_store, key="first", project_id=17, episodes=(1, 10))
    second = seed_run_plan(second_store, key="second", project_id=17, episodes=(10, 20))

    first_store.claim_pending_action(first["id"], expected_revision=1)
    with pytest.raises(WriterActionError) as exc:
        second_store.claim_pending_action(second["id"], expected_revision=1)

    assert exc.value.code == "ACTIVE_ACTION_CONFLICT"
    assert exc.value.conflicting_action_id == first["id"]


def test_global_stages_conflict_even_when_payload_contains_disjoint_episode_values(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    first_session = store.create("writer-1", "first", "材料")
    second_session = store.create("writer-1", "second", "材料")
    first = store.create_action(
        session_id=first_session["id"], actor_user_id="writer-1",
        action_type="creative.run_plan", project_id=17, idempotency_key="brief-1",
        request_hash="brief-1", payload={"target_stage": "brief", "episode_start": 1, "episode_end": 1},
        expected_context={}, source_snapshot_id=None,
    )
    second = store.create_action(
        session_id=second_session["id"], actor_user_id="writer-1",
        action_type="creative.run_plan", project_id=17, idempotency_key="brief-2",
        request_hash="brief-2", payload={"target_stage": "brief", "episode_start": 20, "episode_end": 20},
        expected_context={}, source_snapshot_id=None,
    )

    store.claim_pending_action(first["id"], expected_revision=1)
    with pytest.raises(WriterActionError, match="ACTIVE_ACTION_CONFLICT"):
        store.claim_pending_action(second["id"], expected_revision=1)


def test_partial_episode_range_is_rejected_before_action_can_be_claimed(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "partial", "材料")

    with pytest.raises(WriterActionError, match="RESULT_VALIDATION_FAILED"):
        store.create_action(
            session_id=session["id"], actor_user_id="writer-1",
            action_type="creative.run_plan", project_id=17, idempotency_key="partial",
            request_hash="partial", payload={"target_stage": "script", "episode_start": 1},
            expected_context={}, source_snapshot_id=None,
        )


def test_session_context_changes_and_new_messages_have_stable_ids(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "材料", "初稿")

    assert session["context_revision"] == 0
    assert session["messages"][0]["message_id"]
    bound = store.bind_project(session["id"], 17, expected_context_revision=0)
    appended = store.append_material(session["id"], "writer-1", "补充", "补充.md")

    assert bound["project_id"] == 17
    assert bound["context_revision"] == 1
    assert appended["context_revision"] == 2
    assert appended["messages"][-1]["message_id"]


def test_steps_run_in_order_only_after_dependencies_succeed_and_retry_preserves_operation_id(
    tmp_path: Path,
) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    action = seed_run_plan(store, key="step-action", project_id=17, episodes=(1, 2))
    store.claim_pending_action(action["id"], expected_revision=1)
    store.upsert_step(
        action_id=action["id"], step_key="script:1", stage="script",
        episode_start=1, episode_end=1, status="pending", operation_id="operation-1",
        depends_on=[], attempt=1, step_order=2,
    )
    store.upsert_step(
        action_id=action["id"], step_key="script:2", stage="script",
        episode_start=2, episode_end=2, status="pending", operation_id="operation-2",
        depends_on=["script:1"], attempt=1, step_order=3,
    )

    assert [step["step_key"] for step in store.list_runnable_steps(action["id"])] == ["script:1"]
    store.finish_step(action["id"], "script:1", {"resource_refs": [{"id": "one"}]})
    assert [step["step_key"] for step in store.list_runnable_steps(action["id"])] == ["script:2"]
    store.fail_step(action["id"], "script:2", code="GENERATION_FAILED", message="failed", retryable=True)
    store.fail_action(action["id"], code="GENERATION_FAILED", message="failed", retryable=True)

    retry = store.create_retry_action(
        store.require_retryable_action(action["id"], "writer-1"), "writer-1"
    )
    copied = store.list_steps(retry["id"])
    assert [step["step_key"] for step in copied] == ["script:2"]
    assert copied[0]["operation_id"] == "operation-2"
    assert copied[0]["step_order"] == 3


def test_retry_copies_only_failed_or_unstarted_steps(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    action = seed_run_plan(store, key="retry-states", project_id=17, episodes=(1, 3))
    store.claim_pending_action(action["id"], expected_revision=1)
    for key, status in (("failed", "failed"), ("pending", "pending"), ("running", "running"), ("cancelled", "cancelled")):
        store.upsert_step(
            action_id=action["id"], step_key=key, stage="script", episode_start=None,
            episode_end=None, status=status, operation_id=f"operation-{key}", depends_on=[],
            attempt=1, step_order=len(store.list_steps(action["id"])) + 1,
        )
    store.fail_action(action["id"], code="GENERATION_FAILED", message="failed", retryable=True)

    retry = store.create_retry_action(
        store.require_retryable_action(action["id"], "writer-1"), "writer-1"
    )

    assert [step["step_key"] for step in store.list_steps(retry["id"])] == ["failed", "pending"]


def test_action_result_snapshot_and_phase_state_transitions_are_idempotent(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    action = seed_action(store, key="result", request_hash="result")
    snapshot = store.save_source_snapshot({"materials": [{"name": "a", "content": "b"}]})

    assert store.require_source_snapshot(snapshot["id"])["snapshot"] == snapshot["snapshot"]
    store.record_action_result(action["id"], {"project_id": 9})
    assert store.record_action_result(action["id"], {"project_id": 9})["result"] == {"project_id": 9}
    with pytest.raises(WriterActionError, match="IDEMPOTENCY_KEY_REUSED"):
        store.record_action_result(action["id"], {"project_id": 10})

    store.claim_pending_action(action["id"], expected_revision=2)
    assert store.start_action(action["id"])["status"] == "running"
    assert store.set_action_phase(action["id"], phase="writing")["phase"] == "writing"
    finished = store.finish_action(action["id"], status="succeeded")
    assert finished["status"] == "succeeded"


def test_upsert_step_persists_job_assignment_without_changing_operation_id(tmp_path: Path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    action = seed_run_plan(store, key="job", project_id=17, episodes=(1, 1))
    store.upsert_step(
        action_id=action["id"], step_key="script:1", stage="script",
        episode_start=1, episode_end=1, status="pending", operation_id="operation-1",
        depends_on=[], attempt=1, step_order=1,
    )

    updated = store.upsert_step(
        action_id=action["id"], step_key="script:1", stage="script",
        episode_start=1, episode_end=1, status="pending", operation_id="operation-1",
        depends_on=[], attempt=1, step_order=1, job_id="job-1",
    )

    assert updated["job_id"] == "job-1"
    assert updated["operation_id"] == "operation-1"
