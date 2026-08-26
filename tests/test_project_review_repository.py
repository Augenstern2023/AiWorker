# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from creative.repository import CreativeRepository


def _create_project_review(
    repository: CreativeRepository,
    *,
    fingerprint: str = "snapshot-a",
    request_key: str = "confirm-1",
) -> tuple[dict, bool]:
    return repository.create_project_review_once(
        project_id=11,
        episode_numbers=[1, 2],
        source_fingerprint=fingerprint,
        source_payload={"audit_scope": "outline", "request_key": request_key},
        created_by="writer-1",
        request_key=request_key,
    )


def test_project_review_creation_is_idempotent_and_completes_without_confirmation(
    tmp_path,
) -> None:
    """Dropping request-key lookup or using formal completion must fail this contract."""
    repository = CreativeRepository(tmp_path / "creative.db")

    first, first_created = _create_project_review(repository)
    second, second_created = _create_project_review(repository)
    completed = repository.complete_project_review(first["id"], {"ai_verdict": "通过"})

    assert first["id"] == second["id"]
    assert (first_created, second_created) == (True, False)
    assert first["source_type"] == "project"
    assert first["mode"] == "collection"
    assert completed["status"] == "completed"
    assert completed["report"] == {"ai_verdict": "通过"}


def test_concurrent_project_review_creation_stores_one_request_key(tmp_path) -> None:
    """Removing the immediate write transaction must allow duplicate rows here."""
    repository = CreativeRepository(tmp_path / "creative.db")
    barrier = Barrier(2)

    def create() -> tuple[dict, bool]:
        barrier.wait()
        return _create_project_review(repository)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))

    rows = [
        review
        for review in repository.list_script_reviews(created_by="writer-1", project_ids={11})
        if review["source_payload"].get("request_key") == "confirm-1"
    ]
    assert len({review["id"] for review, _ in results}) == 1
    assert sorted(created for _, created in results) == [False, True]
    assert len(rows) == 1


def test_project_review_rejects_reused_request_key_for_different_snapshot(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    _create_project_review(repository)

    with pytest.raises(ValueError, match="确认请求已用于不同项目快照"):
        _create_project_review(repository, fingerprint="snapshot-b")


def test_project_review_resume_uses_compare_and_swap_and_preserves_checkpoint(tmp_path) -> None:
    """Replacing CAS with a broad update would overwrite terminal or foreign records."""
    repository = CreativeRepository(tmp_path / "creative.db")
    review, _ = _create_project_review(repository)
    checkpoint = {"ai_verdict": "建议修改", "summary": "已完成首批"}
    repository.checkpoint_script_review(review["id"], checkpoint)
    repository.fail_script_review(review["id"], "provider unavailable")

    resumed = repository.prepare_project_review_resume(review["id"])
    queued_replay = repository.prepare_project_review_resume(review["id"])
    repository.start_script_review(review["id"])
    running_replay = repository.prepare_project_review_resume(review["id"])
    completed = repository.complete_project_review(review["id"], {"ai_verdict": "通过"})

    assert resumed["status"] == "queued"
    assert resumed["error"] == ""
    assert resumed["report"] == checkpoint
    assert resumed["source_payload"]["audit_scope"] == "outline"
    assert queued_replay["status"] == "queued"
    assert running_replay["status"] == "running"
    assert completed["status"] == "completed"
    with pytest.raises(ValueError, match="已完成"):
        repository.prepare_project_review_resume(review["id"])


def test_project_review_resume_rejects_formal_script_review(tmp_path) -> None:
    repository = CreativeRepository(tmp_path / "creative.db")
    formal = repository.create_script_review(
        source_type="project",
        mode="single",
        project_id=11,
        episode_numbers=[1],
        source_fingerprint="formal-snapshot",
        source_payload={"formal": True},
        created_by="writer-1",
    )
    repository.fail_script_review(formal["id"], "provider unavailable")

    with pytest.raises(ValueError, match="完整项目审核"):
        repository.prepare_project_review_resume(formal["id"])
