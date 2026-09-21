# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""PostgreSQL review, project long review and optimization contracts.

Every test runs against the shared ``postgres_creative`` fixture, i.e. a fresh
isolated PostgreSQL schema with the whole creative schema migrated. Prerequisite
rows (a project and its episode versions) are inserted with raw SQL through
``isolated_system_database.transaction()`` so this suite depends on no other
domain repository.

The contracts pinned here are the ones the legacy SQLite store guaranteed:
epoch-second timestamps, native JSON values, identical Chinese transition
errors, the idempotent project long review, and single-use optimization drafts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import bindparam


# ---------------------------------------------------------------------------
# prerequisite rows (raw SQL, no other domain repository)
# ---------------------------------------------------------------------------


def _create_project(database, *, name: str = "审核项目") -> int:
    """Insert one原创 project row and return its ID."""

    with database.transaction() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO creative_projects "
                    "(name, creation_mode, created_by, status, created_at, updated_at) "
                    "VALUES (:name, 'original', 'tester', 'created', now(), now()) "
                    "RETURNING id"
                ),
                {"name": name},
            ).scalar_one()
        )


def _create_episode(
    database,
    project_id: int,
    *,
    stage: str = "script",
    episode_number: int = 1,
    version: int = 1,
    content: dict | None = None,
) -> int:
    """Insert one ``script`` episode version and return its ID."""

    statement = text(
        """
        INSERT INTO creative_episodes (
            project_id, stage, episode_number, batch_id, version, status,
            content_json, created_at, updated_at
        ) VALUES (
            :project_id, :stage, :episode_number, :batch_id, :version, 'draft',
            :content_json, now(), now()
        ) RETURNING id
        """
    ).bindparams(bindparam("content_json", type_=JSONB))
    with database.transaction() as connection:
        return int(
            connection.execute(
                statement,
                {
                    "project_id": int(project_id),
                    "stage": stage,
                    "episode_number": int(episode_number),
                    "batch_id": f"batch-{episode_number}-{version}",
                    "version": int(version),
                    "content_json": content
                    if content is not None
                    else {"episode_title": f"原稿 {episode_number}", "scenes": []},
                },
            ).scalar_one()
        )


def _create_review(
    repository,
    *,
    project_id: int | None,
    fingerprint: str = "fp-1",
    source_payload: dict | None = None,
    episode_numbers: list[int] | None = None,
) -> dict:
    return repository.create_script_review(
        source_type="project" if project_id is not None else "text",
        mode="single",
        project_id=project_id,
        episode_numbers=[1] if episode_numbers is None else episode_numbers,
        source_fingerprint=fingerprint,
        source_payload={"script": "第一集"} if source_payload is None else source_payload,
        created_by="writer-1",
    )


def _long_review_payload(request_key: str) -> dict:
    return {"audit_scope": "outline", "request_key": request_key, "episodes": [1, 2]}


def _create_long_review(repository, project_id: int, request_key: str, fingerprint: str):
    return repository.create_project_review_once(
        project_id=project_id,
        episode_numbers=[1, 2],
        source_fingerprint=fingerprint,
        source_payload=_long_review_payload(request_key),
        created_by="writer-1",
        request_key=request_key,
    )


def _completed_optimization(
    repository, project_id: int, review_id: int, *, mode: str, drafts: list[dict]
):
    task = repository.create_script_optimization(
        review_id=review_id,
        project_id=project_id,
        mode=mode,
        source_fingerprint="fp-1",
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1],
        created_by="writer-1",
    )
    return task, repository.complete_script_optimization(task["id"], drafts)


# ---------------------------------------------------------------------------
# script review lifecycle
# ---------------------------------------------------------------------------


def test_script_review_lifecycle_reaches_confirmed(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    review = _create_review(
        postgres_creative,
        project_id=project_id,
        fingerprint="fp-1",
        episode_numbers=[1, 2],
    )

    # created: epoch timestamps, native JSON, no leaked *_json keys
    assert review["status"] == "queued"
    assert review["project_id"] == project_id
    assert review["source_type"] == "project"
    assert review["mode"] == "single"
    assert review["episode_numbers"] == [1, 2]
    assert review["source_payload"] == {"script": "第一集"}
    assert review["report"] == {}
    assert review["ai_verdict"] is None
    assert review["final_verdict"] is None
    assert review["confirmed_by"] is None
    assert review["confirmed_at"] is None
    assert review["confirmation_note"] == ""
    assert review["job_id"] == ""
    assert review["error"] == ""
    assert isinstance(review["created_at"], float)
    assert isinstance(review["updated_at"], float)
    assert postgres_creative.get_script_review(review["id"]) == review

    running = postgres_creative.set_script_review_job(review["id"], "job-9")
    assert running["job_id"] == "job-9"
    running = postgres_creative.start_script_review(review["id"])
    assert (running["status"], running["job_id"], running["error"]) == (
        "running",
        "job-9",
        "",
    )

    checkpoint = {"ai_verdict": "建议修改", "summary": "第一批完成"}
    checked = postgres_creative.checkpoint_script_review(review["id"], checkpoint)
    assert checked["status"] == "running"
    assert checked["report"] == checkpoint

    report = {"ai_verdict": "建议修改", "summary": "完成", "core_findings": []}
    completed = postgres_creative.complete_script_review(review["id"], report)
    assert completed["status"] == "pending_confirmation"
    assert completed["ai_verdict"] == "建议修改"
    assert completed["report"] == report
    assert completed["error"] == ""

    confirmed = postgres_creative.confirm_script_review(
        review["id"], "建议修改", "  已复核  ", "owner-1", "fp-1"
    )
    assert confirmed["status"] == "confirmed"
    assert confirmed["final_verdict"] == "建议修改"
    assert confirmed["confirmation_note"] == "已复核"
    assert confirmed["confirmed_by"] == "owner-1"
    assert isinstance(confirmed["confirmed_at"], float)

    with pytest.raises(ValueError, match="该审核记录当前不能确认"):
        postgres_creative.confirm_script_review(
            review["id"], "通过", "", "owner-1", "fp-1"
        )
    with pytest.raises(ValueError, match="最终结论必须是通过或建议修改"):
        postgres_creative.confirm_script_review(
            review["id"], "拒绝", "", "owner-1", "fp-1"
        )
    with pytest.raises(ValueError, match="审核记录不存在"):
        postgres_creative.confirm_script_review(999999, "通过", "", "owner-1", "fp-1")
    assert postgres_creative.get_script_review(999999) is None


def test_script_review_marks_stale_when_the_script_changed(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    review = _create_review(postgres_creative, project_id=project_id, fingerprint="fp-1")
    postgres_creative.complete_script_review(review["id"], {"ai_verdict": "通过"})

    # The same fingerprint is a no-op, so a live review stays confirmable.
    unchanged = postgres_creative.mark_script_review_stale_if_changed(
        review["id"], "fp-1"
    )
    assert unchanged["status"] == "pending_confirmation"

    with pytest.raises(ValueError, match="剧本已更新，请重新审核后再确认"):
        postgres_creative.confirm_script_review(
            review["id"], "通过", "", "owner-1", "fp-2"
        )
    stale = postgres_creative.get_script_review(review["id"])
    assert stale["status"] == "stale"
    assert stale["error"] == "剧本已更新，请重新审核后再确认"
    assert stale["report"] == {"ai_verdict": "通过"}
    # A stale review is no longer confirmable even with the current fingerprint.
    with pytest.raises(ValueError, match="该审核记录当前不能确认"):
        postgres_creative.confirm_script_review(
            review["id"], "通过", "", "owner-1", "fp-1"
        )

    # Only project reviews that await confirmation are ever marked stale.
    text_review = _create_review(postgres_creative, project_id=None, fingerprint="fp-3")
    postgres_creative.complete_script_review(text_review["id"], {"ai_verdict": "通过"})
    kept = postgres_creative.mark_script_review_stale_if_changed(
        text_review["id"], "fp-other"
    )
    assert kept["status"] == "pending_confirmation"
    with pytest.raises(ValueError, match="审核记录不存在"):
        postgres_creative.mark_script_review_stale_if_changed(999999, "fp")


def test_script_review_listing_filters_and_ordering(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    first = _create_review(postgres_creative, project_id=project_id, fingerprint="fp-1")
    second = _create_review(postgres_creative, project_id=project_id, fingerprint="fp-2")
    orphan = _create_review(postgres_creative, project_id=None, fingerprint="fp-3")

    failed = postgres_creative.fail_script_review(first["id"], "x" * 3000)
    assert failed["status"] == "failed"
    assert len(failed["error"]) == 2000
    cancelled = postgres_creative.cancel_script_review(second["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "审核任务已取消"
    cancelled = postgres_creative.cancel_script_review(second["id"], "人工取消")
    assert cancelled["error"] == "人工取消"

    listed = postgres_creative.list_script_reviews(
        created_by="writer-1", project_ids={project_id}
    )
    assert [item["id"] for item in listed] == [second["id"], first["id"]]
    keys = [(item["created_at"], item["id"]) for item in listed]
    assert keys == sorted(keys, reverse=True)

    assert [
        item["id"]
        for item in postgres_creative.list_script_reviews(
            created_by="writer-1", project_ids=set()
        )
    ] == [orphan["id"]]
    assert postgres_creative.list_script_reviews(created_by="nobody") == []
    assert len(postgres_creative.list_script_reviews()) == 3

    postgres_creative.delete_script_review(first["id"])
    assert postgres_creative.get_script_review(first["id"]) is None
    with pytest.raises(ValueError, match="审核记录不存在"):
        postgres_creative.delete_script_review(first["id"])


# ---------------------------------------------------------------------------
# project long review: idempotency, completion and resume
# ---------------------------------------------------------------------------


def test_project_long_review_is_idempotent_completes_and_resumes(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)

    first, created = _create_long_review(
        postgres_creative, project_id, "confirm-1", "snap-a"
    )
    assert created is True
    assert first["source_type"] == "project"
    assert first["mode"] == "collection"
    assert first["status"] == "queued"
    assert first["episode_numbers"] == [1, 2]
    assert first["source_payload"]["request_key"] == "confirm-1"
    assert first["source_payload"]["audit_scope"] == "outline"

    second, created_again = _create_long_review(
        postgres_creative, project_id, "confirm-1", "snap-a"
    )
    assert second["id"] == first["id"]
    assert created_again is False
    assert (
        len(
            postgres_creative.list_script_reviews(
                created_by="writer-1", project_ids={project_id}
            )
        )
        == 1
    )

    # Different request key: a second long review for the same project is fine.
    third, third_created = _create_long_review(
        postgres_creative, project_id, "confirm-2", "snap-a"
    )
    assert third["id"] != first["id"]
    assert third_created is True

    with pytest.raises(ValueError, match="确认请求缺少幂等键"):
        postgres_creative.create_project_review_once(
            project_id=project_id,
            episode_numbers=[1],
            source_fingerprint="snap-a",
            source_payload=_long_review_payload("   "),
            created_by="writer-1",
            request_key="   ",
        )
    with pytest.raises(ValueError, match="完整项目审核缺少审核范围"):
        postgres_creative.create_project_review_once(
            project_id=project_id,
            episode_numbers=[1],
            source_fingerprint="snap-a",
            source_payload={"request_key": "confirm-3"},
            created_by="writer-1",
            request_key="confirm-3",
        )
    with pytest.raises(ValueError, match="确认请求已用于不同项目快照"):
        _create_long_review(postgres_creative, project_id, "confirm-1", "snap-b")

    completed = postgres_creative.complete_project_review(
        first["id"], {"ai_verdict": "通过", "summary": "整体通过"}
    )
    assert completed["status"] == "completed"
    assert completed["ai_verdict"] == "通过"
    assert completed["report"] == {"ai_verdict": "通过", "summary": "整体通过"}
    assert completed["error"] == ""

    # Only an audit_scope payload identifies a long review.
    formal = _create_review(
        postgres_creative,
        project_id=project_id,
        fingerprint="formal",
        source_payload={"formal": True},
    )
    with pytest.raises(ValueError, match="不是完整项目审核记录"):
        postgres_creative.complete_project_review(formal["id"], {"ai_verdict": "通过"})
    with pytest.raises(ValueError, match="不是完整项目审核记录"):
        postgres_creative.prepare_project_review_resume(formal["id"])
    with pytest.raises(ValueError, match="审核记录不存在"):
        postgres_creative.prepare_project_review_resume(999999)

    resumable, _ = _create_long_review(
        postgres_creative, project_id, "confirm-resume", "snap-r"
    )
    checkpoint = {"ai_verdict": "建议修改", "summary": "已完成首批"}
    postgres_creative.checkpoint_script_review(resumable["id"], checkpoint)
    postgres_creative.fail_script_review(resumable["id"], "provider unavailable")
    resumed = postgres_creative.prepare_project_review_resume(resumable["id"])
    assert resumed["status"] == "queued"
    assert resumed["error"] == ""
    assert resumed["report"] == checkpoint
    assert resumed["source_payload"]["audit_scope"] == "outline"
    assert (
        postgres_creative.prepare_project_review_resume(resumable["id"])["status"]
        == "queued"
    )
    postgres_creative.start_script_review(resumable["id"])
    assert (
        postgres_creative.prepare_project_review_resume(resumable["id"])["status"]
        == "running"
    )
    postgres_creative.complete_project_review(resumable["id"], {"ai_verdict": "通过"})
    with pytest.raises(ValueError, match="完整项目审核已完成，不能恢复"):
        postgres_creative.prepare_project_review_resume(resumable["id"])

    abandoned, _ = _create_long_review(
        postgres_creative, project_id, "confirm-cancel", "snap-c"
    )
    postgres_creative.cancel_script_review(abandoned["id"])
    with pytest.raises(ValueError, match="完整项目审核当前不能恢复"):
        postgres_creative.prepare_project_review_resume(abandoned["id"])


def test_concurrent_project_review_creation_stores_one_request_key(
    postgres_creative, isolated_system_database
) -> None:
    """Removing the per-project advisory lock must allow duplicate rows here."""

    project_id = _create_project(isolated_system_database)
    repository_class = type(postgres_creative)
    repositories = [repository_class(isolated_system_database) for _ in range(2)]
    barrier = Barrier(2)

    def create(repository):
        barrier.wait(timeout=30)
        return _create_long_review(repository, project_id, "confirm-race", "snap-race")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, repositories, timeout=60))

    assert len({review["id"] for review, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]
    stored = postgres_creative.list_script_reviews(
        created_by="writer-1", project_ids={project_id}
    )
    assert len(stored) == 1
    assert stored[0]["source_payload"]["request_key"] == "confirm-race"


# ---------------------------------------------------------------------------
# script optimizations and drafts
# ---------------------------------------------------------------------------


def test_script_optimization_lifecycle_pairs_structure_drafts(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    episode_id = _create_episode(isolated_system_database, project_id)
    review = _create_review(postgres_creative, project_id=project_id, fingerprint="fp-1")

    task = postgres_creative.create_script_optimization(
        review_id=review["id"],
        project_id=project_id,
        mode="structure",
        source_fingerprint="fp-1",
        selected_findings=[{"ref": "core:0", "episode_numbers": [1]}],
        target_episode_numbers=[1],
        created_by="writer-1",
    )
    assert task["status"] == "queued"
    assert task["drafts"] == []
    assert task["selected_findings"] == [{"ref": "core:0", "episode_numbers": [1]}]
    assert task["target_episode_numbers"] == [1]
    assert task["job_id"] == ""
    assert task["error"] == ""
    assert isinstance(task["created_at"], float)

    assert postgres_creative.set_script_optimization_job(task["id"], "job-opt")[
        "job_id"
    ] == "job-opt"
    running = postgres_creative.start_script_optimization(task["id"])
    assert (running["status"], running["error"]) == ("running", "")

    completed = postgres_creative.complete_script_optimization(
        task["id"],
        [
            {
                "episode_number": 1,
                "kind": "beats",
                "source_episode_id": episode_id,
                "source_fingerprint": "beats-fp",
                "content": {"beats": [{"order": 1}]},
            },
            {
                "episode_number": 1,
                "kind": "script",
                "source_episode_id": episode_id,
                "source_fingerprint": "script-fp",
                "content": {
                    "episode_title": "优化稿",
                    "scenes": [
                        {
                            "sequence_no": 1,
                            "scene_no": 1,
                            "blocks": [
                                {"type": "subtitle", "text": "字幕"},
                                {"type": "dialogue", "speaker": "主角", "text": "台词"},
                            ],
                        }
                    ],
                },
            },
        ],
    )
    assert completed["status"] == "completed"
    assert completed["error"] == ""
    drafts = completed["drafts"]
    assert [item["kind"] for item in drafts] == ["beats", "script"]
    beats_draft, script_draft = drafts
    # same-episode structure drafts point at each other
    assert beats_draft["peer_draft_id"] == script_draft["id"]
    assert script_draft["peer_draft_id"] == beats_draft["id"]
    assert all(item["project_id"] == project_id for item in drafts)
    assert all(item["status"] == "ready" for item in drafts)
    assert all(item["optimization_id"] == task["id"] for item in drafts)
    assert beats_draft["source_episode_id"] == episode_id
    assert beats_draft["source_fingerprint"] == "beats-fp"
    assert beats_draft["content"] == {"beats": [{"order": 1}]}
    # scene scripts are normalized on read, Beats are not
    assert [
        block["type"] for block in script_draft["content"]["scenes"][0]["blocks"]
    ] == ["dialogue"]

    assert (
        postgres_creative.get_script_optimization_draft(script_draft["id"])["content"]
        == script_draft["content"]
    )
    assert [
        item["id"]
        for item in postgres_creative.list_episode_script_optimization_drafts(
            project_id, 1, "script"
        )
    ] == [script_draft["id"]]
    assert postgres_creative.list_episode_script_optimization_drafts(
        project_id, 1, "beats"
    )[0]["id"] == beats_draft["id"]

    other = postgres_creative.create_script_optimization(
        review_id=review["id"],
        project_id=project_id,
        mode="direct",
        source_fingerprint="fp-1",
        selected_findings=[],
        target_episode_numbers=[1],
        created_by="writer-1",
    )
    assert [
        item["id"] for item in postgres_creative.list_script_optimizations(review["id"])
    ] == [other["id"], task["id"]]

    failed = postgres_creative.fail_script_optimization(other["id"], "y" * 2500)
    assert failed["status"] == "failed"
    assert len(failed["error"]) == 2000
    assert postgres_creative.get_script_optimization(999999) is None
    with pytest.raises(ValueError, match="优化任务不存在"):
        postgres_creative.complete_script_optimization(999999, [])


def test_draft_accept_binds_the_new_episode_version_once(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    source_episode = _create_episode(isolated_system_database, project_id, version=1)
    accepted_episode = _create_episode(
        isolated_system_database,
        project_id,
        version=2,
        content={"episode_title": "优化稿", "scenes": []},
    )
    review = _create_review(postgres_creative, project_id=project_id, fingerprint="fp-1")
    task, completed = _completed_optimization(
        postgres_creative,
        project_id,
        review["id"],
        mode="direct",
        drafts=[
            {
                "episode_number": 1,
                "kind": "script",
                "source_episode_id": source_episode,
                "source_fingerprint": "script-fp",
                "content": {"episode_title": "优化稿", "scenes": []},
            },
            {
                "episode_number": 1,
                "kind": "beats",
                "source_episode_id": source_episode,
                "source_fingerprint": "beats-fp",
                "content": {"beats": []},
            },
        ],
    )
    assert completed["status"] == "completed"
    script_draft = next(item for item in completed["drafts"] if item["kind"] == "script")
    beats_draft = next(item for item in completed["drafts"] if item["kind"] == "beats")
    assert script_draft["accepted_episode_id"] is None
    assert beats_draft["peer_draft_id"] is None

    accepted = postgres_creative.accept_script_optimization_draft(
        script_draft["id"], accepted_episode, "owner-1"
    )
    assert accepted["status"] == "accepted"
    assert accepted["accepted_episode_id"] == accepted_episode
    assert accepted["accepted_by"] == "owner-1"
    assert isinstance(accepted["accepted_at"], float)
    assert accepted["rejection_note"] == ""
    # the version advance is visible through the task that produced the draft
    stored_draft = next(
        item
        for item in postgres_creative.get_script_optimization(task["id"])["drafts"]
        if item["id"] == script_draft["id"]
    )
    assert stored_draft["accepted_episode_id"] == accepted_episode
    assert stored_draft["status"] == "accepted"
    with isolated_system_database.transaction() as connection:
        versions = [
            int(value)
            for value in connection.execute(
                text(
                    "SELECT version FROM creative_episodes "
                    "WHERE project_id = :project_id AND stage = 'script' "
                    "AND episode_number = 1 ORDER BY version ASC"
                ),
                {"project_id": project_id},
            ).scalars()
        ]
    assert versions == [1, 2]
    assert accepted["accepted_episode_id"] == accepted_episode

    with pytest.raises(ValueError, match="该优化草稿当前不能采纳"):
        postgres_creative.accept_script_optimization_draft(
            script_draft["id"], accepted_episode, "owner-2"
        )
    with pytest.raises(ValueError, match="该优化草稿当前不能拒绝"):
        postgres_creative.reject_script_optimization_draft(script_draft["id"], "不要")

    rejected = postgres_creative.reject_script_optimization_draft(
        beats_draft["id"], "  节奏可以  "
    )
    assert rejected["status"] == "rejected"
    assert rejected["rejection_note"] == "节奏可以"
    assert rejected["accepted_episode_id"] is None
    with pytest.raises(ValueError, match="该优化草稿当前不能采纳"):
        postgres_creative.accept_script_optimization_draft(
            beats_draft["id"], accepted_episode, "owner-1"
        )
    with pytest.raises(ValueError, match="优化草稿不存在"):
        postgres_creative.accept_script_optimization_draft(
            999999, accepted_episode, "owner-1"
        )
    with pytest.raises(ValueError, match="优化草稿不存在"):
        postgres_creative.reject_script_optimization_draft(999999, "x")
    assert postgres_creative.get_script_optimization_draft(999999) is None


def test_structure_drafts_become_accepted_as_each_locked_version_is_bound(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    source_episode = _create_episode(isolated_system_database, project_id)
    locked_beats_episode = _create_episode(
        isolated_system_database, project_id, stage="beats", version=1,
        content={"beats": [{"order": 1}]},
    )
    locked_script_episode = _create_episode(
        isolated_system_database, project_id, stage="script", version=2,
        content={"episode_title": "优化稿", "scenes": []},
    )
    review = _create_review(postgres_creative, project_id=project_id)
    _, completed = _completed_optimization(
        postgres_creative,
        project_id,
        review["id"],
        mode="structure",
        drafts=[
            {
                "episode_number": 1,
                "kind": "beats",
                "source_episode_id": source_episode,
                "source_fingerprint": "beats-fp",
                "content": {"beats": [{"order": 1}]},
            },
            {
                "episode_number": 1,
                "kind": "script",
                "source_episode_id": None,
                "source_fingerprint": "script-fp",
                "content": {"episode_title": "优化稿", "scenes": []},
            },
        ],
    )
    beats_draft = next(item for item in completed["drafts"] if item["kind"] == "beats")
    script_draft = next(item for item in completed["drafts"] if item["kind"] == "script")

    with pytest.raises(ValueError, match="请先锁定并采纳对应 Beats 草稿"):
        postgres_creative.accept_script_optimization_draft(
            script_draft["id"], locked_script_episode, "owner-1"
        )
    staged_beats = postgres_creative.accept_script_optimization_draft(
        beats_draft["id"], locked_beats_episode, "owner-1"
    )
    assert staged_beats["status"] == "accepted"

    accepted_script = postgres_creative.accept_script_optimization_draft(
        script_draft["id"], locked_script_episode, "owner-1"
    )
    assert accepted_script["status"] == "accepted"
    assert postgres_creative.get_script_optimization_draft(beats_draft["id"])["status"] == "accepted"


def test_delete_script_review_removes_its_optimizations_and_drafts(
    postgres_creative, isolated_system_database
) -> None:
    project_id = _create_project(isolated_system_database)
    episode_id = _create_episode(isolated_system_database, project_id)
    review = _create_review(postgres_creative, project_id=project_id, fingerprint="fp-1")
    task, completed = _completed_optimization(
        postgres_creative,
        project_id,
        review["id"],
        mode="structure",
        drafts=[
            {
                "episode_number": 1,
                "kind": "beats",
                "source_episode_id": episode_id,
                "source_fingerprint": "beats-fp",
                "content": {"beats": []},
            },
            {
                "episode_number": 1,
                "kind": "script",
                "source_episode_id": episode_id,
                "source_fingerprint": "script-fp",
                "content": {"episode_title": "优化稿", "scenes": []},
            },
        ],
    )
    draft_ids = [item["id"] for item in completed["drafts"]]
    assert len(draft_ids) == 2

    postgres_creative.delete_script_review(review["id"])

    assert postgres_creative.get_script_review(review["id"]) is None
    assert postgres_creative.get_script_optimization(task["id"]) is None
    assert postgres_creative.list_script_optimizations(review["id"]) == []
    for draft_id in draft_ids:
        assert postgres_creative.get_script_optimization_draft(draft_id) is None
    assert postgres_creative.list_script_reviews(created_by="writer-1") == []
    with pytest.raises(ValueError, match="审核记录不存在"):
        postgres_creative.delete_script_review(review["id"])
