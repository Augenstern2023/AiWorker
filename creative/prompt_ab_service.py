# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Preflight calculations and durable metadata for prompt-template A/B runs."""

from __future__ import annotations

import json
import math
from typing import Any

from creative.prompt_registry import content_hash
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository


AB_STAGES = {"brief", "characters", "outline", "beats", "script"}
AB_SCORE_DIMENSIONS = {
    "premise_fit": "命题契合",
    "conflict_engine": "冲突引擎",
    "character_drive": "人物驱动力",
    "emotional_payoff": "情绪回报",
    "pacing_hooks": "节奏与钩子",
    "expression_quality": "表达完成度",
    "production_feasibility": "制作可行性",
}
AB_ITERATION_GUIDANCE = {
    "premise_fit": "收紧模板与项目命题、平台和目标受众的对应关系",
    "conflict_engine": "强化角色目标、阻力、代价与反转之间的因果链",
    "character_drive": "让关键行动由人物欲望和选择推动，减少情节代办",
    "emotional_payoff": "提前铺设情绪承诺，并在关键节点提供可见回报",
    "pacing_hooks": "压缩过渡段，在开场、转折和结尾建立更明确的钩子",
    "expression_quality": "统一对白、动作和叙事颗粒度，减少泛化表达",
    "production_feasibility": "降低高成本场景与调度依赖，保留核心戏剧效果",
}


def _estimated_calls(stage: str, episode_count: int) -> int:
    if stage == "outline":
        return 2 + math.ceil(max(1, episode_count) / 10)
    return 1


def _estimated_output_tokens(
    stage: str, *, episode_count: int, episode_duration_seconds: int
) -> int:
    if stage == "brief":
        return 1_400
    if stage == "characters":
        return 3_000
    if stage == "outline":
        return 2_000 + max(1, episode_count) * 500
    if stage == "beats":
        return 1_600
    return max(2_000, int(episode_duration_seconds) * 7)


def _source_descriptor(
    project: dict[str, Any], *, stage: str, episode_number: int
) -> dict[str, Any]:
    seed = project.get("seed") if isinstance(project.get("seed"), dict) else {}
    return {
        "project_id": int(project["id"]),
        "project_revision": int(project.get("revision") or 0),
        "stage": stage,
        "episode_number": int(episode_number),
        "workflow_mode": str(project.get("workflow_mode") or "sequential"),
        "target_episode_count": int(project.get("target_episode_count") or 1),
        "episode_duration_seconds": int(
            project.get("episode_duration_seconds") or 120
        ),
        "locked_brief_id": project.get("locked_brief_id"),
        "character_batch_id": project.get("character_batch_id"),
        "outline_batch_id": project.get("outline_batch_id"),
        "seed_id": seed.get("id"),
        "seed_version": seed.get("version"),
        "resource_heads": [
            {
                "stage": item.get("stage"),
                "episode_number": int(item.get("episode_number") or 0),
                "resource_ref": str(item.get("resource_ref") or ""),
                "resource_version": int(item.get("resource_version") or 0),
                "revision": int(item.get("revision") or 0),
            }
            for item in project.get("resource_heads") or []
        ],
    }


class PromptABService:
    def __init__(self, repository: CreativeRepository) -> None:
        self.repository = repository
        self.store = PromptStore(repository.db_path)

    def preflight(
        self,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
        prompt_snapshots: dict[str, dict[str, Any]],
        model: dict[str, Any],
        budget_amount: float,
        run_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(int(project_id))
        if project is None:
            raise ValueError("创作项目不存在")
        if stage not in AB_STAGES:
            raise ValueError("A/B 测试只支持创作生成阶段")
        if stage in {"beats", "script"}:
            total = int(project.get("target_episode_count") or 0)
            if not 1 <= int(episode_number) <= total:
                raise ValueError("A/B 测试集数超出项目范围")
        else:
            episode_number = 0
        if set(prompt_snapshots) != {"A", "B"}:
            raise ValueError("A/B 测试必须包含两个模板快照")

        descriptor = _source_descriptor(
            project, stage=stage, episode_number=episode_number
        )
        descriptor["run_context"] = dict(run_context or {})
        source_json = json.dumps(
            descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        source_fingerprint = content_hash(descriptor)
        calls_per_arm = _estimated_calls(
            stage, int(project.get("target_episode_count") or 1)
        )
        output_tokens_per_arm = _estimated_output_tokens(
            stage,
            episode_count=int(project.get("target_episode_count") or 1),
            episode_duration_seconds=int(
                project.get("episode_duration_seconds") or 120
            ),
        )
        arms: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        for arm in ("A", "B"):
            snapshot = prompt_snapshots[arm]
            input_chars = len(source_json) + len(
                str(snapshot.get("effective_instruction") or "")
            )
            input_tokens = max(1, math.ceil(input_chars / 2)) * calls_per_arm
            output_tokens = output_tokens_per_arm
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            arms.append(
                {
                    "arm": arm,
                    "template_key": snapshot.get("template_key"),
                    "template_title": snapshot.get("title"),
                    "template_revision": int(snapshot.get("revision") or 0),
                    "calls": calls_per_arm,
                    "estimated_input_tokens": input_tokens,
                    "estimated_output_tokens": output_tokens,
                }
            )

        input_price = model.get("input_price_per_million")
        output_price = model.get("output_price_per_million")
        currency = str(model.get("price_currency") or "CNY").upper()
        blockers: list[str] = []
        if input_price is None or output_price is None:
            blockers.append("所选模型尚未配置输入/输出 Token 单价")
            estimated_cost = None
        else:
            estimated_cost = round(
                total_input_tokens * float(input_price) / 1_000_000
                + total_output_tokens * float(output_price) / 1_000_000,
                6,
            )
            if float(budget_amount) < estimated_cost:
                blockers.append(
                    f"预算不足：预计 {estimated_cost:g} {currency}，当前预算 {float(budget_amount):g} {currency}"
                )
        if (
            prompt_snapshots["A"].get("content_hash")
            == prompt_snapshots["B"].get("content_hash")
        ):
            blockers.append("A、B 两组模板内容相同，无法形成有效对照")
        estimate = {
            "calls": calls_per_arm * 2,
            "estimated_input_tokens": total_input_tokens,
            "estimated_output_tokens": total_output_tokens,
            "estimated_total_tokens": total_input_tokens + total_output_tokens,
            "estimated_cost": estimated_cost,
            "price_currency": currency,
            "price_snapshot": {
                "input_price_per_million": input_price,
                "output_price_per_million": output_price,
            },
            "arms": arms,
        }
        preflight_payload = {
            "project_id": int(project_id),
            "stage": stage,
            "episode_number": int(episode_number),
            "model_record_id": str(model.get("model_record_id") or model.get("id") or ""),
            "source_fingerprint": source_fingerprint,
            "prompt_hashes": {
                arm: str(prompt_snapshots[arm].get("content_hash") or "")
                for arm in ("A", "B")
            },
            "estimate": estimate,
            "budget_amount": float(budget_amount),
        }
        return {
            **preflight_payload,
            "preflight_hash": content_hash(preflight_payload),
            "can_start": not blockers,
            "blockers": blockers,
            "prompt_snapshots": prompt_snapshots,
            "source_snapshot": descriptor,
        }

    def create_experiment(
        self,
        *,
        operation_id: str,
        actor_user_id: str,
        preflight: dict[str, Any],
    ) -> dict[str, Any]:
        return self.store.create_ab_experiment(
            operation_id=operation_id,
            project_id=int(preflight["project_id"]),
            stage=str(preflight["stage"]),
            episode_number=int(preflight["episode_number"]),
            model_record_id=str(preflight["model_record_id"]),
            source_fingerprint=str(preflight["source_fingerprint"]),
            source_snapshot=dict(preflight.get("source_snapshot") or {}),
            preflight_hash=str(preflight["preflight_hash"]),
            estimate=dict(preflight["estimate"]),
            budget_amount=float(preflight["budget_amount"]),
            price_currency=str(preflight["estimate"]["price_currency"]),
            prompt_snapshots=dict(preflight["prompt_snapshots"]),
            actor_user_id=actor_user_id,
        )

    def performance_report(
        self,
        *,
        project_id: int,
        actor_user_id: str,
        stage: str | None = None,
    ) -> dict[str, Any]:
        if stage is not None and stage not in AB_STAGES:
            raise ValueError("A/B 汇总阶段无效")
        experiments = self.store.list_evaluated_ab_experiments(
            project_id, stage=stage
        )
        grouped: dict[str, dict[str, Any]] = {}
        visible_experiment_ids: set[str] = set()
        for experiment in experiments:
            winner = str(experiment.get("winner_arm") or "")
            evaluation = dict(experiment.get("evaluation") or {})
            for arm in experiment.get("arms") or []:
                arm_name = str(arm.get("arm") or "")
                snapshot = dict(arm.get("prompt_snapshot") or {})
                template_key = str(
                    snapshot.get("template_key") or arm.get("template_key") or ""
                )
                template_revision = int(
                    snapshot.get("revision") or arm.get("template_revision") or 0
                )
                template = self.store.get_template_by_key(template_key)
                if (
                    template
                    and template.get("owner_scope") == "user"
                    and str(template.get("owner_user_id") or "")
                    != str(actor_user_id or "")
                ):
                    continue
                if template is None and template_key.startswith("user-"):
                    continue
                scores = evaluation.get(arm_name)
                if not isinstance(scores, dict):
                    continue
                dimension_scores: dict[str, int] = {}
                for key in AB_SCORE_DIMENSIONS:
                    try:
                        score = int(scores.get(key))
                    except (TypeError, ValueError):
                        break
                    if not 1 <= score <= 5:
                        break
                    dimension_scores[key] = score
                if len(dimension_scores) != len(AB_SCORE_DIMENSIONS):
                    continue
                visible_experiment_ids.add(str(experiment["id"]))
                group_key = f"{template_key}@{template_revision}"
                item = grouped.setdefault(
                    group_key,
                    {
                        "template_key": template_key,
                        "template_revision": template_revision,
                        "template_title": str(
                            snapshot.get("title")
                            or (template or {}).get("title")
                            or "未命名模板"
                        ),
                        "evaluation_count": 0,
                        "wins": 0,
                        "losses": 0,
                        "ties": 0,
                        "dimension_totals": {
                            key: 0 for key in AB_SCORE_DIMENSIONS
                        },
                        "strengths": [],
                        "risks": [],
                    },
                )
                item["evaluation_count"] += 1
                if winner == arm_name:
                    item["wins"] += 1
                elif winner == "tie":
                    item["ties"] += 1
                elif winner in {"A", "B"}:
                    item["losses"] += 1
                for key, score in dimension_scores.items():
                    item["dimension_totals"][key] += score
                for note_key in ("strengths", "risks"):
                    note = str(scores.get(note_key) or "").strip()
                    if note and note not in item[note_key]:
                        item[note_key].append(note)

        items: list[dict[str, Any]] = []
        for item in grouped.values():
            count = int(item["evaluation_count"])
            dimensions = {
                key: round(total / count, 2)
                for key, total in item.pop("dimension_totals").items()
            }
            average_score = round(sum(dimensions.values()) / len(dimensions), 2)
            ranked = sorted(dimensions, key=lambda key: (-dimensions[key], key))
            weakest = sorted(dimensions, key=lambda key: (dimensions[key], key))[0]
            decided = int(item["wins"]) + int(item["losses"])
            sample_level = "low" if count < 3 else "medium" if count < 6 else "high"
            suggestions: list[str] = []
            if count < 3:
                suggestions.append(
                    "当前样本少于 3 次，先继续对照验证，不建议据此改默认模板"
                )
            if dimensions[weakest] >= 4:
                suggestions.append(
                    "七项均分都达到 4 分，优先保留当前结构，仅针对具体风险做小幅修订"
                )
            else:
                suggestions.append(AB_ITERATION_GUIDANCE[weakest])
            recent_strengths = item.pop("strengths")[:3]
            recent_risks = item.pop("risks")[:3]
            items.append(
                {
                    **item,
                    "average_score": average_score,
                    "dimension_scores": dimensions,
                    "best_dimension": {
                        "key": ranked[0],
                        "label": AB_SCORE_DIMENSIONS[ranked[0]],
                        "score": dimensions[ranked[0]],
                    },
                    "weakest_dimension": {
                        "key": weakest,
                        "label": AB_SCORE_DIMENSIONS[weakest],
                        "score": dimensions[weakest],
                    },
                    "decided_win_rate": (
                        round(int(item["wins"]) / decided, 4) if decided else None
                    ),
                    "sample_level": sample_level,
                    "recent_strengths": recent_strengths,
                    "recent_risks": recent_risks,
                    "iteration_suggestions": suggestions,
                }
            )
        items.sort(
            key=lambda item: (
                -int(item["evaluation_count"]),
                -float(item["average_score"]),
                str(item["template_title"]),
            )
        )
        return {
            "project_id": int(project_id),
            "stage": stage,
            "evaluated_experiment_count": len(visible_experiment_ids),
            "template_count": len(items),
            "score_dimensions": AB_SCORE_DIMENSIONS,
            "items": items,
            "methodology": [
                "只汇总当前用户有权查看的模板及已人工确认的评价",
                "胜率只计算明确选择 A 或 B 的实验，平局单独统计",
                "样本少于 3 次仅提供继续验证建议，不建议修改默认绑定",
                "报告为确定性汇总，不自动创建模板版本或改变项目设置",
            ],
        }

    def create_iteration_draft(
        self,
        *,
        operation_id: str,
        project_id: int,
        actor_user_id: str,
        template_key: str,
        expected_template_revision: int,
        stage: str,
        dimension_key: str,
    ) -> dict[str, Any]:
        if stage not in AB_STAGES:
            raise ValueError("模板迭代阶段无效")
        if dimension_key not in AB_SCORE_DIMENSIONS:
            raise ValueError("模板迭代维度无效")
        template = self.store.get_template_by_key(template_key)
        if template is None:
            raise ValueError("模板不存在")
        if template.get("owner_scope") == "system":
            raise ValueError("系统模板只读，请先另存为个人或项目模板")
        if (
            template.get("owner_scope") == "user"
            and str(template.get("owner_user_id") or "") != str(actor_user_id)
        ):
            raise PermissionError("只能迭代自己的个人模板")
        if (
            template.get("owner_scope") == "project"
            and int(template.get("project_id") or 0) != int(project_id)
        ):
            raise PermissionError("项目模板不属于当前项目")
        base = self.store.get_revision(
            str(template["id"]), int(expected_template_revision)
        )
        latest = self.store.get_revision(str(template["id"]))
        if int(latest["revision"]) != int(expected_template_revision):
            raise ValueError("模板版本已发生变化，请刷新汇总后重试")

        report = self.performance_report(
            project_id=project_id,
            actor_user_id=actor_user_id,
            stage=stage,
        )
        report_item = next(
            (
                item
                for item in report["items"]
                if item["template_key"] == str(template_key)
                and int(item["template_revision"])
                == int(expected_template_revision)
            ),
            None,
        )
        if report_item is None:
            raise ValueError("该模板尚无可用于迭代的人工评价")
        if str(report_item["weakest_dimension"]["key"]) != dimension_key:
            raise ValueError("迭代建议已变化，请刷新汇总后重新选择")

        proposed = json.loads(json.dumps(base["content"], ensure_ascii=False))
        mode = "series" if stage == "outline" else "single"
        before = str(proposed["stage_rules"][stage][mode])
        guidance = AB_ITERATION_GUIDANCE[dimension_key]
        label = AB_SCORE_DIMENSIONS[dimension_key]
        addition = f"【人工评价迭代重点·{label}】{guidance}。"
        if addition in before:
            raise ValueError("当前模板版本已经包含这条迭代重点")
        after = before.rstrip() + "\n\n" + addition
        proposed["stage_rules"][stage][mode] = after
        report_snapshot = {
            "stage": stage,
            "template_key": template_key,
            "evaluation_count": report_item["evaluation_count"],
            "average_score": report_item["average_score"],
            "weakest_dimension": report_item["weakest_dimension"],
            "recent_strengths": report_item["recent_strengths"],
            "recent_risks": report_item["recent_risks"],
            "iteration_suggestion": guidance,
        }
        request_fingerprint = content_hash(
            {
                "project_id": int(project_id),
                "template_key": template_key,
                "base_revision": int(expected_template_revision),
                "base_content_hash": base["content_hash"],
                "stage": stage,
                "dimension_key": dimension_key,
                "report_snapshot": report_snapshot,
            }
        )
        warnings = []
        if int(report_item["evaluation_count"]) < 3:
            warnings.append("当前评价样本少于 3 次，发布前建议继续进行 A/B 验证")
        return self.store.create_iteration_draft(
            operation_id=operation_id,
            request_fingerprint=request_fingerprint,
            project_id=project_id,
            template_id=str(template["id"]),
            template_key=template_key,
            base_revision=int(expected_template_revision),
            base_content_hash=str(base["content_hash"]),
            proposed_content=proposed,
            stage=stage,
            dimension_key=dimension_key,
            report_snapshot=report_snapshot,
            changes=[
                {
                    "path": f"stage_rules.{stage}.{mode}",
                    "label": f"{stage} · {mode} 阶段规则",
                    "before": before,
                    "after": after,
                }
            ],
            warnings=warnings,
            actor_user_id=actor_user_id,
        )

    def publish_iteration_draft(
        self,
        *,
        draft_id: str,
        project_id: int,
        actor_user_id: str,
        expected_template_revision: int,
    ) -> dict[str, Any]:
        draft = self.store.get_iteration_draft(draft_id)
        if draft is None or int(draft["project_id"]) != int(project_id):
            raise ValueError("模板迭代草稿不存在")
        template = self.store.get_template(str(draft["template_id"]))
        if template is None:
            raise ValueError("模板不存在")
        if (
            template.get("owner_scope") == "user"
            and str(template.get("owner_user_id") or "") != str(actor_user_id)
        ):
            raise PermissionError("只能发布自己的个人模板草稿")
        if (
            template.get("owner_scope") == "project"
            and int(template.get("project_id") or 0) != int(project_id)
        ):
            raise PermissionError("项目模板不属于当前项目")
        if int(draft["base_revision"]) != int(expected_template_revision):
            raise ValueError("草稿基础版本与确认版本不一致")
        if draft["status"] == "published":
            revision = self.store.get_revision(
                str(template["id"]), int(draft["published_revision"])
            )
            return {"draft": draft, "revision": revision}
        if draft["status"] != "draft":
            raise ValueError("模板迭代草稿已失效")

        latest = self.store.get_revision(str(template["id"]))
        if (
            int(latest["revision"]) == int(draft["base_revision"])
            and str(latest["content_hash"]) == str(draft["base_content_hash"])
        ):
            revision = self.store.save_revision(
                str(template["id"]),
                dict(draft["proposed_content"]),
                expected_revision=int(draft["base_revision"]),
                actor_user_id=actor_user_id,
            )
        elif str(latest["content_hash"]) == str(draft["proposed_content_hash"]):
            revision = latest
        else:
            raise ValueError("模板版本已发生变化，请重新创建迭代草稿")
        published = self.store.mark_iteration_draft_published(
            draft_id,
            published_revision=int(revision["revision"]),
            actor_user_id=actor_user_id,
        )
        return {"draft": published, "revision": revision}

    def audit_report(
        self,
        *,
        project_id: int,
        experiment_id: str,
        actor_user_id: str,
    ) -> dict[str, Any]:
        experiment = self.store.get_ab_experiment(experiment_id)
        if experiment is None or int(experiment["project_id"]) != int(project_id):
            raise ValueError("A/B 实验不存在")
        project = self.repository.get_project(project_id)
        if project is None:
            raise ValueError("创作项目不存在")

        hidden_arms: set[str] = set()
        personal_template_keys: set[str] = set()
        visible_template_keys: set[str] = set()
        arms: list[dict[str, Any]] = []
        for stored_arm in experiment.get("arms") or []:
            arm_name = str(stored_arm.get("arm") or "")
            snapshot = dict(stored_arm.get("prompt_snapshot") or {})
            template_key = str(
                snapshot.get("template_key")
                or stored_arm.get("template_key")
                or ""
            )
            template = self.store.get_template_by_key(template_key)
            hidden = bool(
                (template is None and template_key.startswith("user-"))
                or (
                    template
                    and template.get("owner_scope") == "user"
                    and str(template.get("owner_user_id") or "")
                    != str(actor_user_id or "")
                )
            )
            if hidden:
                hidden_arms.add(arm_name)
                template_summary = {
                    "template_key": "",
                    "title": "私人模板（不可见）",
                    "revision": 0,
                    "content_hash": None,
                    "body_redacted": True,
                }
            else:
                visible_template_keys.add(template_key)
                is_personal = bool(
                    template and template.get("owner_scope") == "user"
                )
                if is_personal:
                    personal_template_keys.add(template_key)
                template_summary = {
                    key: snapshot.get(key)
                    for key in (
                        "template_key",
                        "title",
                        "revision",
                        "content_hash",
                        "stage",
                        "mode",
                        "dimensions",
                    )
                }
                template_summary["owner_scope"] = (
                    template.get("owner_scope") if template else "unknown"
                )
                template_summary["body_redacted"] = is_personal
                if not is_personal:
                    template_summary["effective_instruction"] = snapshot.get(
                        "effective_instruction"
                    )
            arms.append(
                {
                    "arm": arm_name,
                    "job_id": str(stored_arm.get("job_id") or ""),
                    "template": template_summary,
                }
            )

        candidate_resources: dict[str, list[dict[str, Any]]] = {
            "A": [],
            "B": [],
        }
        resource_groups = (
            ("brief", project.get("blueprints") or []),
            ("character", project.get("characters") or []),
            ("relationship", project.get("relationships") or []),
            ("episode", project.get("episodes") or []),
        )
        for resource_type, resources in resource_groups:
            for resource in resources:
                meta = dict(resource.get("generation_meta") or {})
                if str(meta.get("prompt_ab_experiment_id") or "") != str(
                    experiment_id
                ):
                    continue
                arm_name = str(meta.get("prompt_ab_arm") or "")
                if arm_name not in candidate_resources:
                    continue
                candidate_resources[arm_name].append(
                    {
                        "resource_type": resource_type,
                        "resource_id": resource.get("id"),
                        "batch_id": resource.get("batch_id"),
                        "episode_number": resource.get("episode_number"),
                        "version": resource.get("version"),
                        "status": resource.get("status"),
                        "job_id": meta.get("job_id"),
                    }
                )

        evaluation = dict(experiment.get("evaluation") or {})
        winner_arm = str(experiment.get("winner_arm") or "")
        decision_note = str(experiment.get("decision_note") or "")
        if hidden_arms:
            evaluation = {}
            winner_arm = ""
            decision_note = ""

        iteration_chain: list[dict[str, Any]] = []
        for draft in self.store.list_iteration_drafts(
            project_id, template_keys=visible_template_keys
        ):
            template_key = str(draft.get("template_key") or "")
            item = {
                key: draft.get(key)
                for key in (
                    "id",
                    "template_key",
                    "base_revision",
                    "base_content_hash",
                    "proposed_content_hash",
                    "stage",
                    "dimension_key",
                    "status",
                    "created_at",
                    "published_revision",
                    "published_at",
                )
            }
            item["report_snapshot"] = draft.get("report_snapshot")
            item["body_redacted"] = template_key in personal_template_keys
            if template_key not in personal_template_keys:
                item["changes"] = draft.get("changes")
            iteration_chain.append(item)

        estimate = dict(experiment.get("estimate") or {})
        estimate["arms"] = [
            (
                {
                    **{
                        key: value
                        for key, value in dict(stored_estimate_arm).items()
                        if key
                        not in {
                            "template_key",
                            "template_title",
                            "template_revision",
                        }
                    },
                    "template_key": "",
                    "template_title": "私人模板（不可见）",
                    "template_revision": 0,
                }
                if str(stored_estimate_arm.get("arm") or "") in hidden_arms
                else dict(stored_estimate_arm)
            )
            for stored_estimate_arm in estimate.get("arms") or []
        ]
        return {
            "schema_version": 1,
            "report_type": "prompt_template_ab_audit",
            "project": {
                "id": int(project_id),
                "name": str(project.get("name") or ""),
                "workflow_mode": str(project.get("workflow_mode") or ""),
            },
            "experiment": {
                "id": str(experiment["id"]),
                "stage": str(experiment["stage"]),
                "episode_number": int(experiment.get("episode_number") or 0),
                "model_record_id": str(experiment.get("model_record_id") or ""),
                "source_fingerprint": str(experiment["source_fingerprint"]),
                "preflight_hash": str(experiment["preflight_hash"]),
                "created_at": experiment.get("created_at"),
                "created_by": experiment.get("created_by"),
            },
            "frozen_source": dict(experiment.get("source_snapshot") or {}),
            "estimate_snapshot": estimate,
            "arms": arms,
            "candidate_resources": candidate_resources,
            "evaluation": {
                "revision": int(experiment.get("evaluation_revision") or 0),
                "winner_arm": winner_arm,
                "decision_note": decision_note,
                "scorecards": evaluation,
                "evaluated_by": (
                    "" if hidden_arms else str(experiment.get("evaluated_by") or "")
                ),
                "evaluated_at": (
                    None if hidden_arms else experiment.get("evaluated_at")
                ),
            },
            "iteration_chain": iteration_chain,
            "reproducibility": {
                "source_traceable": bool(experiment.get("source_snapshot")),
                "exact_output_replay_guaranteed": False,
                "note": "输入、模板与模型配置可追溯；模型输出具有非确定性，不能保证逐字复现。",
            },
            "privacy": {
                "personal_template_bodies_redacted": True,
                "hidden_private_arms": sorted(hidden_arms),
                "candidate_bodies_included": False,
            },
        }


def prompt_ab_audit_markdown(report: dict[str, Any]) -> str:
    project = dict(report.get("project") or {})
    experiment = dict(report.get("experiment") or {})
    estimate = dict(report.get("estimate_snapshot") or {})
    evaluation = dict(report.get("evaluation") or {})
    lines = [
        f"# 提示词 A/B 实验审计报告：{project.get('name') or project.get('id')}",
        "",
        f"- 实验 ID：{experiment.get('id')}",
        f"- 阶段：{experiment.get('stage')}",
        f"- 集数：{experiment.get('episode_number') or '全局'}",
        f"- 来源指纹：`{experiment.get('source_fingerprint')}`",
        f"- 预检哈希：`{experiment.get('preflight_hash')}`",
        f"- 模型记录：`{experiment.get('model_record_id')}`",
        "",
        "## 冻结来源",
        "",
    ]
    frozen_source = report.get("frozen_source") or {}
    if frozen_source:
        lines.extend(
            "    " + source_line
            for source_line in json.dumps(
                frozen_source,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).splitlines()
        )
    else:
        lines.append("- 历史实验未保存完整来源描述；请使用来源指纹追溯。")
    price_snapshot = dict(estimate.get("price_snapshot") or {})
    lines.extend(
        [
        "",
        "## 预算与估算快照",
        "",
        f"- 调用次数：{estimate.get('calls')}",
        f"- 输入 Token：{estimate.get('estimated_input_tokens')}",
        f"- 输出 Token：{estimate.get('estimated_output_tokens')}",
        f"- 预计费用：{estimate.get('estimated_cost')} {estimate.get('price_currency') or ''}",
        f"- 输入单价/百万 Token：{price_snapshot.get('input_price_per_million')}",
        f"- 输出单价/百万 Token：{price_snapshot.get('output_price_per_million')}",
        "",
        "## 模板分组",
        "",
        ]
    )
    for arm in report.get("arms") or []:
        template = dict(arm.get("template") or {})
        lines.extend(
            [
                f"### {arm.get('arm')} · {template.get('title')}",
                "",
                f"- 模板版本：v{template.get('revision')}",
                f"- 内容哈希：`{template.get('content_hash')}`",
                f"- Job ID：`{arm.get('job_id')}`",
                f"- Job 状态：{arm.get('job_status') or 'unknown'}",
                f"- Job 消息：{arm.get('job_message') or '—'}",
                f"- Job 创建时间：{arm.get('job_created_at') or '—'}",
                f"- Job 开始时间：{arm.get('job_started_at') or '—'}",
                f"- Job 完成时间：{arm.get('job_finished_at') or '—'}",
                f"- 正文脱敏：{'是' if template.get('body_redacted') else '否'}",
                "",
            ]
        )
    lines.extend(
        [
            "## 人工评价",
            "",
            f"- 评价版本：{evaluation.get('revision')}",
            f"- 人工结论：{evaluation.get('winner_arm') or '未评价或已脱敏'}",
            f"- 判断依据：{evaluation.get('decision_note') or '—'}",
            f"- 评价人：{evaluation.get('evaluated_by') or '—'}",
            f"- 评价时间：{evaluation.get('evaluated_at') or '—'}",
            "",
        ]
    )
    for arm_name in ("A", "B"):
        scorecard = dict((evaluation.get("scorecards") or {}).get(arm_name) or {})
        if not scorecard:
            continue
        lines.extend([f"### {arm_name} 组评分", ""])
        for key, label in AB_SCORE_DIMENSIONS.items():
            lines.append(f"- {label}：{scorecard.get(key)}")
        lines.append(f"- 主要优势：{scorecard.get('strengths') or '—'}")
        lines.append(f"- 风险与不足：{scorecard.get('risks') or '—'}")
        lines.append("")
    lines.extend(["## 候选资源引用", ""])
    for arm_name in ("A", "B"):
        resources = (report.get("candidate_resources") or {}).get(arm_name) or []
        lines.append(f"- {arm_name}：{len(resources)} 个资源")
        for resource in resources:
            lines.append(
                "  - "
                + " / ".join(
                    str(value)
                    for value in (
                        resource.get("resource_type"),
                        resource.get("resource_id") or resource.get("batch_id"),
                        f"v{resource.get('version')}",
                        resource.get("status"),
                    )
                    if value not in {None, ""}
                )
            )
    lines.extend(
        [
            "",
            "## 模板迭代链",
            "",
        ]
    )
    if not report.get("iteration_chain"):
        lines.append("- 暂无关联迭代草稿")
    for draft in report.get("iteration_chain") or []:
        lines.append(
            f"- {draft.get('id')}：v{draft.get('base_revision')} → "
            f"v{draft.get('published_revision') or '草稿'}（{draft.get('status')}）"
        )
    lines.extend(
        [
            "",
            "## 复现说明",
            "",
            f"- {report.get('reproducibility', {}).get('note')}",
            "- 候选正文未包含在报告中；个人模板正文始终脱敏。",
            "",
        ]
    )
    return "\n".join(lines)
