# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import time

import pytest

from creative.prompt_ab_service import PromptABService, prompt_ab_audit_markdown
from creative.prompt_registry import builtin_template
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository


def _setup(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    now = time.time()
    with repository._connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO creative_projects (
                name, target_episode_count, episode_duration_seconds,
                created_by, creation_mode, workflow_mode, created_at, updated_at
            ) VALUES ('A/B 测试项目', 12, 120, 'owner-1', 'original',
                      'sequential', ?, ?)
            """,
            (now, now),
        )
    project_id = int(cursor.lastrowid)
    store = PromptStore(repository.db_path)
    store.enable_project(project_id, actor_user_id="owner-1")
    snapshots = {
        "A": store.resolve_snapshot(
            project_id=project_id,
            stage="outline",
            mode="series",
            run_template_key="generic-vertical-short",
            actor_user_id="owner-1",
        ),
        "B": store.resolve_snapshot(
            project_id=project_id,
            stage="outline",
            mode="series",
            run_template_key="urban-identity-romcom",
            actor_user_id="owner-1",
        ),
    }
    return repository, project_id, snapshots


def _preflight(service, project_id, snapshots, **overrides):
    options = {
        "project_id": project_id,
        "stage": "outline",
        "episode_number": 0,
        "prompt_snapshots": snapshots,
        "model": {
            "id": "model-1",
            "input_price_per_million": 2,
            "output_price_per_million": 8,
            "price_currency": "CNY",
        },
        "budget_amount": 10,
        "run_context": {"generation_instruction": "突出人物代价"},
    }
    options.update(overrides)
    return service.preflight(**options)


def _scorecard(score: int, *, strengths: str = "", risks: str = ""):
    return {
        "premise_fit": score,
        "conflict_engine": score,
        "character_drive": score,
        "emotional_payoff": score,
        "pacing_hooks": score,
        "expression_quality": score,
        "production_feasibility": score,
        "average_score": float(score),
        "strengths": strengths,
        "risks": risks,
    }


def test_prompt_store_upgrades_existing_ab_table_with_evaluation_columns(tmp_path):
    repository = CreativeRepository(tmp_path / "creative.db")
    with repository._connect() as conn:
        conn.execute(
            """
            CREATE TABLE creative_prompt_ab_experiments (
                id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
                project_id INTEGER NOT NULL, stage TEXT NOT NULL,
                episode_number INTEGER NOT NULL DEFAULT 0,
                model_record_id TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
                preflight_hash TEXT NOT NULL, estimate_json TEXT NOT NULL,
                budget_amount REAL NOT NULL, price_currency TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    store = PromptStore(repository.db_path)

    with store._connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(creative_prompt_ab_experiments)"
            ).fetchall()
        }
    assert {
        "source_snapshot_json",
        "evaluation_json",
        "winner_arm",
        "decision_note",
        "evaluation_revision",
        "evaluated_by",
        "evaluated_at",
    } <= columns


def test_preflight_is_stable_and_freezes_calls_tokens_and_cost(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)

    first = _preflight(service, project_id, snapshots)
    second = _preflight(service, project_id, snapshots)

    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert first["preflight_hash"] == second["preflight_hash"]
    assert first["estimate"]["calls"] == 8
    assert first["estimate"]["estimated_total_tokens"] > 0
    assert first["estimate"]["estimated_cost"] > 0
    assert first["can_start"] is True


def test_preflight_hash_changes_when_template_order_or_content_changes(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)

    original = _preflight(service, project_id, snapshots)
    reversed_result = _preflight(
        service,
        project_id,
        {"A": snapshots["B"], "B": snapshots["A"]},
    )
    changed = {"A": dict(snapshots["A"]), "B": snapshots["B"]}
    changed["A"]["content_hash"] = "f" * 64
    changed_result = _preflight(service, project_id, changed)

    assert reversed_result["preflight_hash"] != original["preflight_hash"]
    assert changed_result["preflight_hash"] != original["preflight_hash"]


def test_preflight_blocks_missing_prices_low_budget_and_identical_templates(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)

    missing_price = _preflight(
        service,
        project_id,
        snapshots,
        model={"id": "model-1", "price_currency": "CNY"},
    )
    low_budget = _preflight(service, project_id, snapshots, budget_amount=0.000001)
    identical = _preflight(
        service,
        project_id,
        {"A": snapshots["A"], "B": snapshots["A"]},
    )

    assert missing_price["can_start"] is False
    assert any("单价" in item for item in missing_price["blockers"])
    assert low_budget["can_start"] is False
    assert any("预算不足" in item for item in low_budget["blockers"])
    assert identical["can_start"] is False
    assert any("内容相同" in item for item in identical["blockers"])


def test_experiment_keeps_immutable_prompt_and_price_snapshots(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    preflight = _preflight(service, project_id, snapshots)

    experiment = service.create_experiment(
        operation_id="ab-operation-1",
        actor_user_id="owner-1",
        preflight=preflight,
    )
    original_hash = experiment["arms"][0]["prompt_snapshot"]["content_hash"]
    snapshots["A"]["content_hash"] = "0" * 64
    loaded = service.store.get_ab_experiment(experiment["id"])

    assert loaded["arms"][0]["prompt_snapshot"]["content_hash"] == original_hash
    assert loaded["estimate"]["price_snapshot"] == {
        "input_price_per_million": 2,
        "output_price_per_million": 8,
    }
    assert loaded["source_snapshot"] == preflight["source_snapshot"]


def test_audit_report_traces_frozen_inputs_without_candidate_bodies(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    experiment = service.create_experiment(
        operation_id="ab-audit-report",
        actor_user_id="owner-1",
        preflight=_preflight(service, project_id, snapshots),
    )
    service.store.set_ab_arm_job(experiment["id"], "A", "audit-job-a")
    service.store.set_ab_arm_job(experiment["id"], "B", "audit-job-b")
    repository.create_blueprints(
        project_id,
        [{"content": {"title": "不得导出的候选正文", "story": "绝密剧情"}}],
        job_id="audit-job-a",
        generation_meta={
            "prompt_ab_experiment_id": experiment["id"],
            "prompt_ab_arm": "A",
        },
    )
    service.store.save_ab_evaluation(
        experiment["id"],
        evaluation={"A": _scorecard(5), "B": _scorecard(3)},
        winner_arm="A",
        decision_note="A 的命题更聚焦",
        expected_revision=0,
        actor_user_id="owner-1",
    )

    report = service.audit_report(
        project_id=project_id,
        experiment_id=experiment["id"],
        actor_user_id="owner-1",
    )
    serialized = repr(report)

    assert report["experiment"]["source_fingerprint"]
    assert report["frozen_source"]["run_context"]["generation_instruction"] == "突出人物代价"
    assert report["estimate_snapshot"]["price_snapshot"] == {
        "input_price_per_million": 2,
        "output_price_per_million": 8,
    }
    assert {arm["template"]["revision"] for arm in report["arms"]} == {1}
    assert all(arm["template"]["content_hash"] for arm in report["arms"])
    assert report["candidate_resources"]["A"][0]["resource_type"] == "brief"
    assert "不得导出的候选正文" not in serialized
    assert "绝密剧情" not in serialized
    assert "不能保证逐字复现" in prompt_ab_audit_markdown(report)


def test_audit_report_redacts_personal_templates_and_other_users_evidence(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    content = builtin_template("generic-vertical-short")
    content["shared_dna"] += " 私人正文标记 audit-private-body-marker。"
    private = service.store.create_template(
        title="绝密私人模板标题",
        description="不得向其他成员暴露",
        content=content,
        owner_scope="user",
        owner_user_id="owner-1",
    )
    snapshots["A"] = service.store.resolve_snapshot(
        project_id=project_id,
        stage="outline",
        mode="series",
        run_template_key=private["template_key"],
        actor_user_id="owner-1",
    )
    experiment = service.create_experiment(
        operation_id="ab-private-audit",
        actor_user_id="owner-1",
        preflight=_preflight(service, project_id, snapshots),
    )
    weak = _scorecard(4, strengths="私人成效证据", risks="私人风险证据")
    weak["production_feasibility"] = 2
    weak["average_score"] = 3.71
    service.store.save_ab_evaluation(
        experiment["id"],
        evaluation={"A": weak, "B": _scorecard(3)},
        winner_arm="A",
        decision_note="私人判断依据",
        expected_revision=0,
        actor_user_id="owner-1",
    )
    draft = service.create_iteration_draft(
        operation_id="private-audit-iteration",
        project_id=project_id,
        actor_user_id="owner-1",
        template_key=private["template_key"],
        expected_template_revision=1,
        stage="outline",
        dimension_key="production_feasibility",
    )

    owner_report = service.audit_report(
        project_id=project_id,
        experiment_id=experiment["id"],
        actor_user_id="owner-1",
    )
    owner_arm = next(arm for arm in owner_report["arms"] if arm["arm"] == "A")
    owner_draft = next(
        item for item in owner_report["iteration_chain"] if item["id"] == draft["id"]
    )
    member_report = service.audit_report(
        project_id=project_id,
        experiment_id=experiment["id"],
        actor_user_id="member-2",
    )
    member_serialized = repr(member_report)

    assert owner_arm["template"]["body_redacted"] is True
    assert "effective_instruction" not in owner_arm["template"]
    assert "audit-private-body-marker" not in repr(owner_report)
    assert owner_draft["body_redacted"] is True
    assert "changes" not in owner_draft
    assert private["template_key"] not in member_serialized
    assert "绝密私人模板标题" not in member_serialized
    assert snapshots["A"]["content_hash"] not in member_serialized
    assert "私人成效证据" not in member_serialized
    assert "私人风险证据" not in member_serialized
    assert "私人判断依据" not in member_serialized
    assert member_report["evaluation"]["scorecards"] == {}


def test_project_deletion_cascades_to_ab_experiment_and_arms(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    experiment = service.create_experiment(
        operation_id="ab-operation-delete",
        actor_user_id="owner-1",
        preflight=_preflight(service, project_id, snapshots),
    )

    repository.delete_project(project_id)

    assert service.store.get_ab_experiment(experiment["id"]) is None
    with service.store._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM creative_prompt_ab_arms WHERE experiment_id = ?",
            (experiment["id"],),
        ).fetchone()[0]
    assert count == 0


def test_manual_evaluation_uses_optimistic_revision_and_does_not_change_snapshots(
    tmp_path,
):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    experiment = service.create_experiment(
        operation_id="ab-operation-evaluation",
        actor_user_id="owner-1",
        preflight=_preflight(service, project_id, snapshots),
    )
    original_arms = experiment["arms"]
    evaluation = {
        "A": {"premise_fit": 4, "average_score": 4.0},
        "B": {"premise_fit": 3, "average_score": 3.0},
    }

    saved = service.store.save_ab_evaluation(
        experiment["id"],
        evaluation=evaluation,
        winner_arm="A",
        decision_note="A 的冲突推进更清晰",
        expected_revision=0,
        actor_user_id="owner-1",
    )

    assert saved["winner_arm"] == "A"
    assert saved["evaluation_revision"] == 1
    assert saved["evaluation"] == evaluation
    assert saved["arms"] == original_arms
    with pytest.raises(ValueError, match="已被其他成员更新"):
        service.store.save_ab_evaluation(
            experiment["id"],
            evaluation=evaluation,
            winner_arm="B",
            decision_note="过期覆盖",
            expected_revision=0,
            actor_user_id="owner-2",
        )


def test_performance_report_aggregates_scores_and_gives_sample_guarded_advice(
    tmp_path,
):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    for index, (a_score, b_score, winner) in enumerate(
        ((5, 3, "A"), (4, 4, "tie")), start=1
    ):
        experiment = service.create_experiment(
            operation_id=f"ab-report-{index}",
            actor_user_id="owner-1",
            preflight=_preflight(service, project_id, snapshots),
        )
        service.store.save_ab_evaluation(
            experiment["id"],
            evaluation={
                "A": _scorecard(a_score, strengths="冲突集中"),
                "B": _scorecard(b_score, risks="中段偏慢"),
            },
            winner_arm=winner,
            decision_note="人工结论",
            expected_revision=0,
            actor_user_id="owner-1",
        )

    report = service.performance_report(
        project_id=project_id,
        actor_user_id="owner-1",
        stage="outline",
    )
    generic = next(
        item for item in report["items"]
        if item["template_key"] == "generic-vertical-short"
    )

    assert report["evaluated_experiment_count"] == 2
    assert report["template_count"] == 2
    assert generic["evaluation_count"] == 2
    assert generic["template_revision"] == 1
    assert generic["average_score"] == 4.5
    assert generic["wins"] == 1
    assert generic["ties"] == 1
    assert generic["sample_level"] == "low"
    assert "样本少于 3 次" in generic["iteration_suggestions"][0]


def test_performance_report_excludes_another_users_private_template(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    private = service.store.create_template(
        title="所有者私有模板",
        description="不得进入其他成员汇总",
        content=builtin_template("generic-vertical-short"),
        owner_scope="user",
        owner_user_id="owner-1",
    )
    snapshots["A"] = service.store.resolve_snapshot(
        project_id=project_id,
        stage="outline",
        mode="series",
        run_template_key=private["template_key"],
        actor_user_id="owner-1",
    )
    experiment = service.create_experiment(
        operation_id="ab-private-report",
        actor_user_id="owner-1",
        preflight=_preflight(service, project_id, snapshots),
    )
    service.store.save_ab_evaluation(
        experiment["id"],
        evaluation={"A": _scorecard(5), "B": _scorecard(3)},
        winner_arm="A",
        decision_note="私人模板胜出",
        expected_revision=0,
        actor_user_id="owner-1",
    )

    owner_report = service.performance_report(
        project_id=project_id, actor_user_id="owner-1", stage="outline"
    )
    member_report = service.performance_report(
        project_id=project_id, actor_user_id="member-2", stage="outline"
    )

    assert private["template_key"] in {
        item["template_key"] for item in owner_report["items"]
    }
    assert private["template_key"] not in {
        item["template_key"] for item in member_report["items"]
    }
    assert "所有者私有模板" not in repr(member_report)


def _evaluated_custom_template(
    service, project_id, snapshots, *, owner_scope="user"
):
    custom = service.store.create_template(
        title="可迭代模板",
        description="人工维护",
        content=builtin_template("generic-vertical-short"),
        owner_scope=owner_scope,
        owner_user_id="owner-1",
        project_id=project_id if owner_scope == "project" else None,
    )
    snapshots["A"] = service.store.resolve_snapshot(
        project_id=project_id,
        stage="outline",
        mode="series",
        run_template_key=custom["template_key"],
        actor_user_id="owner-1",
    )
    experiment = service.create_experiment(
        operation_id="ab-iteration-evidence",
        actor_user_id="owner-1",
        preflight=_preflight(service, project_id, snapshots),
    )
    weak = _scorecard(4, strengths="人物动机清楚", risks="制作成本偏高")
    weak["production_feasibility"] = 2
    weak["average_score"] = 3.71
    service.store.save_ab_evaluation(
        experiment["id"],
        evaluation={"A": weak, "B": _scorecard(3)},
        winner_arm="A",
        decision_note="保留结构并降低制作成本",
        expected_revision=0,
        actor_user_id="owner-1",
    )
    return custom


def test_iteration_draft_previews_diff_and_only_publishes_after_confirmation(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    custom = _evaluated_custom_template(
        service, project_id, snapshots, owner_scope="project"
    )
    binding_before = service.store.binding(project_id, "outline", 0)

    draft = service.create_iteration_draft(
        operation_id="iteration-draft-1",
        project_id=project_id,
        actor_user_id="owner-1",
        template_key=custom["template_key"],
        expected_template_revision=1,
        stage="outline",
        dimension_key="production_feasibility",
    )

    assert draft["status"] == "draft"
    assert draft["base_revision"] == 1
    assert draft["changes"][0]["before"] != draft["changes"][0]["after"]
    assert "制作可行性" in draft["changes"][0]["after"]
    assert service.store.get_revision(custom["id"])["revision"] == 1
    assert service.store.binding(project_id, "outline", 0) == binding_before

    published = service.publish_iteration_draft(
        draft_id=draft["id"],
        project_id=project_id,
        actor_user_id="owner-1",
        expected_template_revision=1,
    )
    retried = service.publish_iteration_draft(
        draft_id=draft["id"],
        project_id=project_id,
        actor_user_id="owner-1",
        expected_template_revision=1,
    )

    assert published["draft"]["status"] == "published"
    assert published["revision"]["revision"] == 2
    assert retried["revision"]["revision"] == 2
    assert service.store.binding(project_id, "outline", 0) == binding_before


def test_iteration_draft_rejects_system_template_and_stale_base_version(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    with pytest.raises(ValueError, match="系统模板只读"):
        service.create_iteration_draft(
            operation_id="system-iteration",
            project_id=project_id,
            actor_user_id="owner-1",
            template_key="generic-vertical-short",
            expected_template_revision=1,
            stage="outline",
            dimension_key="premise_fit",
        )

    custom = _evaluated_custom_template(service, project_id, snapshots)
    draft = service.create_iteration_draft(
        operation_id="stale-iteration",
        project_id=project_id,
        actor_user_id="owner-1",
        template_key=custom["template_key"],
        expected_template_revision=1,
        stage="outline",
        dimension_key="production_feasibility",
    )
    changed = service.store.get_revision(custom["id"], 1)["content"]
    changed["shared_dna"] += " 外部并发修订。"
    service.store.save_revision(
        custom["id"], changed,
        expected_revision=1, actor_user_id="owner-1",
    )

    with pytest.raises(ValueError, match="模板版本已发生变化"):
        service.publish_iteration_draft(
            draft_id=draft["id"],
            project_id=project_id,
            actor_user_id="owner-1",
            expected_template_revision=1,
        )


def test_project_deletion_removes_iteration_drafts(tmp_path):
    repository, project_id, snapshots = _setup(tmp_path)
    service = PromptABService(repository)
    custom = _evaluated_custom_template(
        service, project_id, snapshots, owner_scope="project"
    )
    before_draft = repository.writer_project_deletion_fingerprint(project_id)
    draft = service.create_iteration_draft(
        operation_id="delete-iteration-draft",
        project_id=project_id,
        actor_user_id="owner-1",
        template_key=custom["template_key"],
        expected_template_revision=1,
        stage="outline",
        dimension_key="production_feasibility",
    )
    after_draft = repository.writer_project_deletion_fingerprint(project_id)

    repository.delete_project(project_id)

    assert after_draft != before_draft
    assert service.store.get_iteration_draft(draft["id"]) is None
