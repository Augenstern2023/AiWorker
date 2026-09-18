# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Domain tests for the PostgreSQL topics, breakdowns and snapshot repository.

Every scenario is driven through the shared ``postgres_creative`` fixture so the
suite exercises the composed repository exactly as production composes it.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from creative.legacy_sqlite_repository import (
    DownloadTopicDecisionConflict,
    DuplicateTopicError,
    TopicDecisionConflict,
)
from system_database import SystemDatabase


def topic_payload(
    *,
    score=80,
    title="测试选题",
    platform="hg",
    job_id="job-1",
    source_path="rank.json",
    captured_at="2026-09-15T00:00:00Z",
    **overrides,
):
    payload = {
        "platform": platform, "job_id": job_id, "source_path": source_path,
        "captured_at": captured_at, "title": title,
        "description": "", "category": "都市", "rank_type": "热播榜",
        "score": score, "confidence": "high", "score_details": {},
        "recommendation": "adopt", "evidence": [], "source_platforms": [platform],
    }
    payload.update(overrides)
    return payload


def row_count(database: SystemDatabase, table: str = "creative_topics") -> int:
    with database.connection() as connection:
        return connection.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar_one()


# ---------------------------------------------------------------------------
# upsert_topics
# ---------------------------------------------------------------------------


def test_topic_upsert_preserves_manual_decision(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]
    postgres_creative.decide_topic(topic["id"], "adopted", "人工采纳", "user-1")
    updated = postgres_creative.upsert_topics([topic_payload(score=99)])[0]
    assert updated["status"] == "adopted"
    assert updated["decision_note"] == "人工采纳"


def test_upsert_topics_refreshes_a_pending_row(postgres_creative):
    first = postgres_creative.upsert_topics([topic_payload()])[0]
    second = postgres_creative.upsert_topics(
        [
            topic_payload(
                score=95,
                evidence=[{"source_type": "snapshot", "excerpt": "榜单 1"}],
                score_details={"heat": 90},
                captured_at="2026-09-16T00:00:00Z",
            )
        ]
    )[0]

    assert second["id"] == first["id"]
    assert second["score"] == 95
    assert second["captured_at"] == "2026-09-16T00:00:00Z"
    assert second["evidence"] == [{"source_type": "snapshot", "excerpt": "榜单 1"}]
    assert second["score_details"] == {"heat": 90}
    assert second["status"] == "pending_review"
    # The legacy boundary returns epoch seconds and native JSON, never
    # ``datetime`` objects and never the raw ``*_json`` columns.
    assert isinstance(second["created_at"], float)
    assert isinstance(second["updated_at"], float)
    assert "score_details_json" not in second
    assert "source_platforms_json" not in second


def test_upsert_topics_merges_same_title_across_platforms(postgres_creative):
    postgres_creative.upsert_topics(
        [
            topic_payload(
                platform="dy", job_id="job-dy", source_path="dy-rank.json",
                score=80,
                evidence=[{"source_type": "snapshot", "platform": "dy"}],
            )
        ]
    )
    postgres_creative.upsert_topics(
        [
            topic_payload(
                platform="hg", job_id="job-hg", source_path="hg-rank.json",
                score=85,
                evidence=[{"source_type": "snapshot", "platform": "hg"}],
            )
        ]
    )

    items = postgres_creative.list_topics()

    assert len(items) == 1
    assert items[0]["source_platforms"] == ["dy", "hg"]
    assert {item["platform"] for item in items[0]["evidence"]} == {"dy", "hg"}
    assert items[0]["source_paths"] == ["dy-rank.json", "hg-rank.json"]


def test_list_topics_merges_a_decision_over_pending_copies(
    postgres_creative, isolated_system_database
):
    """``upsert_topics`` returns merged rows, so raw ids come from the table."""

    postgres_creative.upsert_topics(
        [topic_payload(title="同名剧", job_id="job-a", source_path="a.json", score=90)]
    )
    postgres_creative.upsert_topics(
        [topic_payload(title="同名剧", job_id="job-b", source_path="b.json", score=80)]
    )
    with isolated_system_database.connection() as connection:
        rows = connection.execute(
            text("SELECT id, job_id FROM creative_topics ORDER BY id")
        ).all()
    pending_id = next(row[0] for row in rows if row[1] == "job-a")
    decided_id = next(row[0] for row in rows if row[1] == "job-b")
    assert pending_id != decided_id
    postgres_creative.decide_topic(decided_id, "adopted", "人工采纳", "actor-1")

    items = postgres_creative.list_topics(limit=None)

    assert len(items) == 1
    assert items[0]["id"] == decided_id
    assert items[0]["status"] == "adopted"
    assert items[0]["decision_note"] == "人工采纳"
    assert items[0]["source_paths"] == ["a.json", "b.json"]
    assert items[0]["source_platforms"] == ["hg"]
    assert postgres_creative.get_topic(pending_id)["status"] == "pending_review"


def test_upsert_topics_concurrent_same_source_writes_exactly_one_row(
    postgres_creative, isolated_system_database
):
    """``uq_creative_topics_source`` + ``on_conflict_do_update`` settle the race."""

    barrier = threading.Barrier(2)

    def write() -> None:
        barrier.wait()
        postgres_creative.upsert_topics([topic_payload()])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: write(), range(2)))

    assert row_count(isolated_system_database) == 1
    assert len(postgres_creative.list_topics(limit=None)) == 1


# ---------------------------------------------------------------------------
# adopt_rank_topic
# ---------------------------------------------------------------------------


def test_adopt_rank_topic_adopts_the_pending_row(postgres_creative):
    pending = postgres_creative.upsert_topics([topic_payload()])[0]

    adopted = postgres_creative.adopt_rank_topic(topic_payload(), "actor-1")

    assert adopted["id"] == pending["id"]
    assert adopted["status"] == "adopted"
    assert adopted["decision_note"] == ""
    snapshot = postgres_creative.get_latest_selection_stage_snapshot(
        adopted["id"], "topic_decision"
    )
    assert snapshot is not None
    assert snapshot["version"] == 1
    assert snapshot["outcome"] == "adopted"
    assert snapshot["actor_user_id"] == "actor-1"
    assert snapshot["resource_type"] == "topic"
    assert snapshot["resource_id"] == str(adopted["id"])
    assert snapshot["source_job_id"] == "job-1"
    assert snapshot["snapshot"] == {
        "topic_id": adopted["id"],
        "title": "测试选题",
        "outcome": "adopted",
        "decision_note": "",
        "source": "today_rank",
    }
    assert isinstance(snapshot["created_at"], float)


def test_adopt_rank_topic_creates_a_new_adopted_row(postgres_creative):
    adopted = postgres_creative.adopt_rank_topic(topic_payload(title="新剧"), "actor-1")

    assert adopted["status"] == "adopted"
    assert adopted["title"] == "新剧"
    assert postgres_creative.decided_title_keys() == {"新剧"}
    assert (
        postgres_creative.get_latest_selection_stage_snapshot(
            adopted["id"], "topic_decision"
        )["version"]
        == 1
    )


def test_adopt_rank_topic_rejects_a_missing_title(postgres_creative):
    with pytest.raises(ValueError, match="榜单剧目缺少标题"):
        postgres_creative.adopt_rank_topic(topic_payload(title="  "), "actor-1")


def test_adopt_rank_topic_reports_an_existing_collection(postgres_creative):
    postgres_creative.adopt_rank_topic(topic_payload(), "actor-1")

    with pytest.raises(DuplicateTopicError, match="剧目已收录"):
        postgres_creative.adopt_rank_topic(topic_payload(), "actor-1")


@pytest.mark.parametrize(
    ("existing_status", "detail"),
    [
        ("rejected", "该剧目已被拒绝，请先在选题库恢复后再收录"),
        ("archived", "该剧目已归档，请先在选题库恢复后再收录"),
    ],
)
def test_adopt_rank_topic_reports_manual_decision_conflicts(
    postgres_creative, existing_status, detail
):
    existing = postgres_creative.upsert_topics([topic_payload()])[0]
    postgres_creative.update_topic(existing["id"], existing_status)

    with pytest.raises(TopicDecisionConflict, match=detail):
        postgres_creative.adopt_rank_topic(
            topic_payload(job_id="job-2", source_path="later-rank.json"), "actor-1"
        )

    assert postgres_creative.get_topic(existing["id"])["status"] == existing_status
    assert postgres_creative.list_topics(limit=None)[0]["id"] == existing["id"]


# ---------------------------------------------------------------------------
# ensure_download_topic
# ---------------------------------------------------------------------------


def test_download_intent_creates_then_reuses_one_adopted_topic(postgres_creative):
    created, created_before = postgres_creative.ensure_download_topic(
        title="下载短剧", platform="hg", provider_id="hongguo_capture",
        source_ref="series-1", actor_user_id="selector-1",
    )
    reused, reused_before = postgres_creative.ensure_download_topic(
        title=" 下载短剧 ", platform="hg", provider_id="hongguo_capture",
        source_ref="series-1", actor_user_id="selector-1",
    )

    assert created["status"] == "adopted"
    assert created["decision_note"] == "下载时自动采纳"
    assert created["platform"] == "hg"
    assert created["job_id"] == "download:series-1"
    assert created["source_path"] == "download-search:hongguo_capture:series-1"
    assert created["rank_type"] == "download_search"
    assert created["rank_source"] == "download_search"
    assert created["score"] == 0
    assert created["confidence"] == "medium"
    assert created["source_platforms"] == ["hg"]
    assert created["category"] == ""
    assert created_before is None

    assert reused["id"] == created["id"]
    assert reused_before is not None
    assert reused_before["status"] == "adopted"
    assert len(postgres_creative.list_topics(limit=None)) == 1


def test_download_intent_adopts_a_pending_topic_and_records_the_actor(
    postgres_creative,
):
    pending = postgres_creative.upsert_topics([topic_payload(title="待采纳短剧")])[0]

    adopted, before = postgres_creative.ensure_download_topic(
        title=pending["title"], platform="hg", provider_id="provider-a",
        source_ref="series-2", actor_user_id="selector-1",
    )

    assert before is not None
    assert before["status"] == "pending_review"
    assert adopted["id"] == pending["id"]
    assert adopted["status"] == "adopted"
    assert adopted["decision_note"] == "下载时自动采纳"
    snapshot = postgres_creative.get_latest_selection_stage_snapshot(
        adopted["id"], "topic_decision"
    )
    assert snapshot["outcome"] == "adopted"
    assert snapshot["actor_user_id"] == "selector-1"
    assert snapshot["source_job_id"] == "job-1"
    assert snapshot["snapshot"]["decision_note"] == "下载时自动采纳"
    assert snapshot["snapshot"]["source"] == "download_search"
    assert snapshot["snapshot"]["provider_id"] == "provider-a"
    assert snapshot["snapshot"]["source_ref"] == "series-2"


@pytest.mark.parametrize("status", ["rejected", "archived"])
def test_download_intent_preserves_a_manual_negative_decision(
    postgres_creative, status
):
    topic = postgres_creative.upsert_topics([topic_payload(title="人工决策短剧")])[0]
    postgres_creative.decide_topic(topic["id"], status, "人工决定", "selector-1")

    with pytest.raises(
        DownloadTopicDecisionConflict,
        match="该剧目已被拒绝或归档，请先在选题库恢复后再下载",
    ):
        postgres_creative.ensure_download_topic(
            title=topic["title"], platform="hg", provider_id="provider-a",
            source_ref="series-3", actor_user_id="selector-1",
        )

    assert postgres_creative.get_topic(topic["id"])["status"] == status


def test_download_intent_validates_its_arguments(postgres_creative):
    with pytest.raises(ValueError, match="剧名不能为空"):
        postgres_creative.ensure_download_topic(
            title=" ", platform="hg", provider_id="p", source_ref="s",
            actor_user_id="u",
        )
    with pytest.raises(ValueError, match="下载选题平台必须是 hg 或 dy"):
        postgres_creative.ensure_download_topic(
            title="下载短剧", platform="xhs", provider_id="p", source_ref="s",
            actor_user_id="u",
        )
    with pytest.raises(ValueError, match="下载选题缺少来源标识"):
        postgres_creative.ensure_download_topic(
            title="下载短剧", platform="hg", provider_id="p", source_ref=" ",
            actor_user_id="u",
        )


def test_concurrent_download_intent_creates_one_topic(
    postgres_creative, isolated_system_database
):
    barrier = threading.Barrier(2)

    def ensure() -> int:
        barrier.wait()
        topic, _ = postgres_creative.ensure_download_topic(
            title="并发下载短剧", platform="hg", provider_id="provider-a",
            source_ref="series-4", actor_user_id="selector-1",
        )
        return int(topic["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: ensure(), range(2)))

    assert len(set(ids)) == 1
    assert row_count(isolated_system_database) == 1


# ---------------------------------------------------------------------------
# list filtering and soft delete
# ---------------------------------------------------------------------------


def test_list_topics_filters_and_orders(postgres_creative):
    postgres_creative.upsert_topics(
        [
            topic_payload(
                title="热播剧甲", rank_source="真人剧热播榜|真人剧推荐榜",
                score=90, captured_at="2026-09-15T00:00:00Z",
            ),
            topic_payload(
                title="漫剧候选", category="漫剧", rank_type="热播榜",
                rank_source="漫剧热播榜", score=70,
                captured_at="2026-09-14T00:00:00Z",
            ),
            topic_payload(
                platform="dy", job_id="job-dy", source_path="dy-rank.json",
                title="抖音热榜剧", category="都市", rank_type="热榜",
                rank_source="热榜", score=50,
                captured_at="2026-09-13T00:00:00Z",
            ),
        ]
    )

    assert [item["title"] for item in postgres_creative.list_topics()] == [
        "热播剧甲",
        "漫剧候选",
        "抖音热榜剧",
    ]
    assert [
        item["title"] for item in postgres_creative.list_topics(platform="hg")
    ] == ["热播剧甲", "漫剧候选"]
    assert [
        item["title"] for item in postgres_creative.list_topics(platform="dy")
    ] == ["抖音热榜剧"]
    assert [
        item["title"] for item in postgres_creative.list_topics(category="都市")
    ] == ["热播剧甲", "抖音热榜剧"]
    assert [
        item["title"] for item in postgres_creative.list_topics(min_score=70)
    ] == ["热播剧甲", "漫剧候选"]
    assert [
        item["title"] for item in postgres_creative.list_topics(max_score=70)
    ] == ["漫剧候选", "抖音热榜剧"]
    assert [
        item["title"]
        for item in postgres_creative.list_topics(captured_date="2026-09-15")
    ] == ["热播剧甲"]
    assert [
        item["title"] for item in postgres_creative.list_topics(limit=1)
    ] == ["热播剧甲"]
    assert len(postgres_creative.list_topics(limit=None)) == 3
    assert [
        item["title"]
        for item in postgres_creative.list_topics(status="pending_review")
    ] == ["热播剧甲", "漫剧候选", "抖音热榜剧"]
    assert postgres_creative.list_topics(status="adopted") == []

    # A board label is never shown as a genre, exactly like the legacy store.
    assert postgres_creative.list_topics(category="漫剧")[0]["category"] == "待补充"


def test_list_topics_filters_rank_source_tokens(postgres_creative):
    postgres_creative.upsert_topics(
        [
            topic_payload(title="真人多榜剧", rank_source="真人剧热播榜|真人剧推荐榜"),
            topic_payload(
                title="漫剧候选", job_id="job-2", source_path="rank-2.json",
                rank_source="漫剧热播榜",
            ),
            topic_payload(
                platform="dy", job_id="job-dy", source_path="dy-rank.json",
                title="抖音热榜剧", rank_type="热榜", rank_source="热榜",
            ),
        ]
    )

    assert [
        item["title"] for item in postgres_creative.list_topics(rank_source="真人剧")
    ] == ["真人多榜剧"]
    assert [
        item["title"] for item in postgres_creative.list_topics(rank_source="漫剧")
    ] == ["漫剧候选"]
    assert [
        item["title"] for item in postgres_creative.list_topics(rank_source="热榜")
    ] == ["抖音热榜剧"]
    # ``rank_source`` skips the SQL LIMIT exactly like the legacy query.
    assert len(postgres_creative.list_topics(rank_source="真人剧", limit=1)) == 1


def test_list_topics_exposes_locked_and_latest_breakdown(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]
    locked = postgres_creative.create_breakdown(topic["id"], {"title": "A"}, {}, [])
    postgres_creative.lock_breakdown(locked["id"])
    draft = postgres_creative.create_breakdown(topic["id"], {"title": "B"}, {}, [])

    listed = postgres_creative.list_topics(status="pending_review")[0]

    assert listed["locked_breakdown_id"] == locked["id"]
    assert listed["latest_breakdown_id"] == draft["id"]
    assert listed["latest_breakdown_version"] == 2
    assert listed["latest_breakdown_status"] == "draft"
    assert [
        item["id"]
        for item in postgres_creative.list_topics(has_locked_breakdown=True)
    ] == [topic["id"]]
    assert postgres_creative.list_topics(has_locked_breakdown=False) == []


def test_get_topic_returns_none_for_unknown_ids(postgres_creative):
    assert postgres_creative.get_topic(4242) is None


def test_update_topic_rejects_unknown_status(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]

    with pytest.raises(ValueError, match="invalid topic status"):
        postgres_creative.update_topic(topic["id"], "finished")


def test_decided_title_keys_only_lists_decided_topics(postgres_creative):
    adopted = postgres_creative.upsert_topics([topic_payload(title="已采纳剧")])[0]
    postgres_creative.upsert_topics(
        [topic_payload(title="待审剧", job_id="job-2", source_path="rank-2.json")]
    )
    postgres_creative.decide_topic(adopted["id"], "adopted", "", "actor-1")

    assert postgres_creative.decided_title_keys() == {"已采纳剧"}


def test_soft_delete_only_removes_archived_topics(
    postgres_creative, isolated_system_database
):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]

    with pytest.raises(ValueError, match="只能删除已归档选题"):
        postgres_creative.delete_topic(topic["id"])

    postgres_creative.update_topic(topic["id"], "archived")
    deleted = postgres_creative.delete_topic(topic["id"])

    assert deleted["id"] == topic["id"]
    assert deleted["status"] == "archived"
    assert postgres_creative.get_topic(topic["id"]) is None
    assert postgres_creative.list_topics(limit=None) == []
    assert postgres_creative.delete_topic(4242) is None
    # Soft, not hard: the row stays for audit and workflow history.
    assert row_count(isolated_system_database) == 1


# ---------------------------------------------------------------------------
# breakdowns
# ---------------------------------------------------------------------------


def test_breakdown_versions_lock_and_immutability(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]

    first = postgres_creative.create_breakdown(
        topic["id"], {"title": "第一版"}, {"title": "人工"}, ["风险"]
    )
    second = postgres_creative.create_breakdown(
        topic["id"], {"title": "第二版"}, {}, [], parent_version_id=first["id"]
    )

    assert first["version"] == 1
    assert first["status"] == "draft"
    assert first["parent_version_id"] is None
    assert first["content"] == {"title": "第一版"}
    assert first["field_sources"] == {"title": "人工"}
    assert first["risks"] == ["风险"]
    assert isinstance(first["created_at"], float)
    assert "content_json" not in first
    assert second["version"] == 2
    assert second["parent_version_id"] == first["id"]
    assert postgres_creative.get_latest_breakdown(topic["id"])["id"] == second["id"]

    edited = postgres_creative.update_breakdown(first["id"], {"title": "改过"})
    assert edited["content"] == {"title": "改过"}

    locked = postgres_creative.lock_breakdown(first["id"])
    assert locked["status"] == "locked"
    assert isinstance(locked["locked_at"], float)

    with pytest.raises(ValueError, match="immutable breakdown cannot be edited"):
        postgres_creative.update_breakdown(first["id"], {"title": "越权"})

    assert postgres_creative.get_latest_locked_breakdown(topic["id"])["id"] == first["id"]
    assert [
        item["id"] for item in postgres_creative.list_locked_breakdowns(topic["id"])
    ] == [first["id"]]
    # Relocking a locked version is a no-op that still returns the row.
    assert postgres_creative.lock_breakdown(first["id"])["status"] == "locked"
    assert postgres_creative.lock_breakdown(4242) is None
    assert postgres_creative.update_breakdown(4242, {}) is None
    assert postgres_creative.get_breakdown(4242) is None


def test_breakdown_echoes_the_source_snapshot_argument(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]

    breakdown = postgres_creative.create_breakdown(
        topic["id"], {"title": "A"}, {}, [], source_snapshot_id="snapshot-abc"
    )

    assert breakdown["source_snapshot_id"] == "snapshot-abc"


def test_complete_breakdown_locks_and_records_one_deduplicated_snapshot(
    postgres_creative,
):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]
    breakdown = postgres_creative.create_breakdown(topic["id"], {"title": "A"}, {}, [])

    completed = postgres_creative.complete_breakdown(breakdown["id"], "actor-1")
    repeated = postgres_creative.complete_breakdown(breakdown["id"], "actor-1")

    assert completed["status"] == "locked"
    assert repeated["status"] == "locked"
    snapshot = postgres_creative.get_latest_selection_stage_snapshot(
        topic["id"], "breakdown"
    )
    assert snapshot["outcome"] == "completed"
    assert snapshot["resource_type"] == "breakdown"
    assert snapshot["resource_id"] == str(breakdown["id"])
    assert snapshot["snapshot"]["breakdown_id"] == breakdown["id"]
    assert snapshot["snapshot"]["content"] == {"title": "A"}
    assert snapshot["version"] == 1

    with pytest.raises(ValueError, match="拆解不存在"):
        postgres_creative.complete_breakdown(4242, "actor-1")


# ---------------------------------------------------------------------------
# selection stage snapshots
# ---------------------------------------------------------------------------


def test_append_selection_stage_snapshot_allocates_versions(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload()])[0]

    first = postgres_creative.append_selection_stage_snapshot(
        "source_package", topic["id"], "ready", {"valid_transcript_episodes": [1]},
        resource_type="topic", resource_id=topic["id"], actor_user_id="actor-1",
    )
    second = postgres_creative.append_selection_stage_snapshot(
        "source_package", topic["id"], "ready", {"valid_transcript_episodes": [1, 2]},
    )

    assert first["version"] == 1
    assert second["version"] == 2
    assert first["snapshot"] == {"valid_transcript_episodes": [1]}
    assert isinstance(first["snapshot"], dict)
    assert isinstance(first["created_at"], float)
    assert "snapshot_json" not in first
    assert first["id"] != second["id"]
    assert first["content_fingerprint"] != second["content_fingerprint"]
    assert len(first["content_fingerprint"]) == 64

    deduplicated = postgres_creative.append_selection_stage_snapshot(
        "source_package", topic["id"], "ready", {"valid_transcript_episodes": [1]},
        deduplicate=True,
    )
    assert deduplicated["id"] == first["id"]
    assert deduplicated["version"] == 1

    latest = postgres_creative.get_latest_selection_stage_snapshot(
        topic["id"], "source_package"
    )
    assert latest["id"] == second["id"]
    assert (
        postgres_creative.get_latest_selection_stage_snapshot(topic["id"], "missing")
        is None
    )


def test_ensure_download_topic_snapshot_versions_increment(postgres_creative):
    topic = postgres_creative.upsert_topics([topic_payload(title="多版本短剧")])[0]
    postgres_creative.ensure_download_topic(
        title=topic["title"], platform="hg", provider_id="p", source_ref="s-1",
        actor_user_id="actor-1",
    )
    postgres_creative.update_topic(topic["id"], "pending_review")
    postgres_creative.ensure_download_topic(
        title=topic["title"], platform="hg", provider_id="p", source_ref="s-2",
        actor_user_id="actor-2",
    )

    latest = postgres_creative.get_latest_selection_stage_snapshot(
        topic["id"], "topic_decision"
    )
    assert latest["version"] == 2
    assert latest["actor_user_id"] == "actor-2"
    assert latest["snapshot"]["source_ref"] == "s-2"
