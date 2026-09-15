# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Evidence-based prompt-template usage, adoption, and regression metrics."""

from __future__ import annotations

import json
from statistics import median
from typing import Any

from creative.prompt_registry import PROMPT_LIBRARY_STAGES
from creative.prompt_store import PromptStore


_JOB_TYPES = {
    "phase4_generate",
    "screenplay_review",
    "project_review",
    "screenplay_optimization",
}
_SUCCESS = {"succeeded", "success", "completed"}
_FAILED = {"failed", "error"}
_CANCELLED = {"cancelled", "stopped"}
_ACTION_STAGES = {
    "blueprints": "brief",
    "blueprint_variant": "brief",
    "characters": "characters",
    "outlines": "outline",
    "outline_overview": "outline",
    "beats": "beats",
    "script": "script",
}
_MIN_REGRESSION_SAMPLE = 3


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator < 1:
        return None
    return round(numerator / denominator, 4)


def _normalized_status(value: Any) -> str | None:
    status = str(value or "").strip().lower()
    if status in _SUCCESS:
        return "succeeded"
    if status in _FAILED:
        return "failed"
    if status in _CANCELLED:
        return "cancelled"
    return None


def _job_data(item: dict[str, Any]) -> dict[str, Any]:
    job = item.get("job")
    return dict(job) if isinstance(job, dict) else dict(item)


def _job_extra(item: dict[str, Any]) -> dict[str, Any]:
    job = _job_data(item)
    return dict(job.get("extra") or {})


def _job_stage(item: dict[str, Any]) -> str:
    job = _job_data(item)
    extra = _job_extra(item)
    direct = str(extra.get("normalized_stage") or "").strip()
    if direct in PROMPT_LIBRARY_STAGES:
        return direct
    crawler_type = str(job.get("crawler_type") or item.get("crawler_type") or "")
    if crawler_type in {"screenplay_review", "project_review"}:
        return "review"
    if crawler_type == "screenplay_optimization":
        return "optimization"
    action_stage = _ACTION_STAGES.get(str(extra.get("action") or "").strip())
    if action_stage:
        return action_stage
    snapshot = _prompt_snapshot(extra)
    return str(snapshot.get("stage") or "")


def _prompt_snapshot(extra: dict[str, Any]) -> dict[str, Any]:
    direct = extra.get("prompt_snapshot")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    source = extra.get("source_snapshot")
    if isinstance(source, dict) and isinstance(source.get("prompt_snapshot"), dict):
        return dict(source["prompt_snapshot"])
    return {}


def _identity_from_values(values: dict[str, Any]) -> dict[str, Any]:
    snapshot = values.get("prompt_snapshot")
    snapshot = dict(snapshot) if isinstance(snapshot, dict) else {}
    key = str(
        values.get("prompt_template_key")
        or snapshot.get("template_key")
        or ""
    ).strip()
    revision = _safe_int(
        values.get("prompt_template_revision")
        if values.get("prompt_template_revision") is not None
        else snapshot.get("revision")
    )
    if not key or not revision:
        return {}
    return {
        "template_id": str(
            values.get("prompt_template_id")
            or snapshot.get("template_id")
            or ""
        ),
        "template_key": key,
        "template_revision": revision,
        "template_title": str(snapshot.get("title") or ""),
        "content_hash": str(
            values.get("prompt_content_hash")
            or snapshot.get("content_hash")
            or ""
        ),
    }


def _terminal_payload(item: dict[str, Any]) -> dict[str, Any]:
    status = _normalized_status(item.get("status"))
    expected = "job_finished" if status == "succeeded" else "job_failed"
    events = item.get("events")
    if not isinstance(events, list):
        return {}
    expected_payload: dict[str, Any] = {}
    for event in reversed(events):
        if not isinstance(event, dict) or event.get("event") not in {
            "job_finished",
            "job_failed",
        }:
            continue
        payload = event.get("payload")
        normalized = dict(payload) if isinstance(payload, dict) else {}
        if event.get("event") == expected and not expected_payload:
            expected_payload = normalized
        if isinstance(normalized.get("llm_usage"), dict):
            return normalized
    return expected_payload


def _usage_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("llm_usage")
    if not isinstance(raw, dict) or "call_count" not in raw:
        return None
    keys = (
        "call_count",
        "reported_call_count",
        "input_reported_call_count",
        "output_reported_call_count",
        "total_reported_call_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
    )
    normalized: dict[str, Any] = {}
    for key in keys:
        value = _safe_int(raw.get(key))
        normalized[key] = value if value is not None else 0
    normalized["complete"] = bool(raw.get("complete")) and (
        normalized["reported_call_count"] == normalized["call_count"]
        and normalized["total_reported_call_count"] == normalized["call_count"]
    )
    return normalized


class PromptObservabilityService:
    def __init__(self, repository: Any, job_manager: Any) -> None:
        self.repository = repository
        self.job_manager = job_manager
        self.store = PromptStore(repository.db_path)

    def report(
        self,
        *,
        project_id: int,
        actor_user_id: str,
        stage: str | None = None,
        template_key: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        if stage is not None and stage not in PROMPT_LIBRARY_STAGES:
            raise ValueError("模板可观测性阶段无效")
        normalized_key = str(template_key or "").strip()
        signals = self._resource_signals(project_id)
        candidates = []
        for item in self.job_manager.list_jobs():
            if not isinstance(item, dict):
                continue
            job = _job_data(item)
            extra = _job_extra(item)
            if str(extra.get("project_id") or item.get("project_id") or "") != str(
                project_id
            ):
                continue
            crawler_type = str(job.get("crawler_type") or item.get("crawler_type") or "")
            if crawler_type not in _JOB_TYPES:
                continue
            normalized_status = _normalized_status(item.get("status"))
            if normalized_status is None:
                continue
            job_stage = _job_stage(item)
            if job_stage not in PROMPT_LIBRARY_STAGES:
                continue
            if stage is not None and job_stage != stage:
                continue
            candidates.append(item)
        candidates.sort(
            key=lambda item: float(item.get("created_at") or 0), reverse=True
        )
        requested_limit = max(1, min(1000, int(limit)))
        truncated = len(candidates) > requested_limit
        candidates = candidates[:requested_limit]

        records: list[dict[str, Any]] = []
        unattributed = 0
        attributed = 0
        privacy_hidden = 0
        template_cache: dict[str, dict[str, Any] | None] = {}
        for listed in candidates:
            job_id = str(listed.get("job_id") or _job_data(listed).get("job_id") or "")
            try:
                full = self.job_manager.get_job(job_id)
            except Exception:
                full = listed
            extra = _job_extra(full)
            signal = signals.get(job_id, {})
            identity = _identity_from_values(extra)
            if not identity:
                identity = dict(signal.get("identity") or {})
            if not identity:
                unattributed += 1
                continue
            attributed += 1
            if normalized_key and identity["template_key"] != normalized_key:
                continue
            identity_key = str(identity["template_key"])
            if identity_key not in template_cache:
                template_cache[identity_key] = self.store.get_template_by_key(
                    identity_key
                )
            template = template_cache[identity_key]
            if not self._template_visible(
                template,
                project_id=project_id,
                actor_user_id=actor_user_id,
                template_key=identity["template_key"],
            ):
                privacy_hidden += 1
                continue
            if template is not None:
                identity["template_id"] = str(template.get("id") or identity["template_id"])
                identity["template_title"] = str(
                    template.get("title") or identity["template_title"]
                )
            terminal_payload = _terminal_payload(full)
            usage = _usage_from_payload(terminal_payload)
            started_at = _safe_float(full.get("started_at"))
            finished_at = _safe_float(full.get("finished_at"))
            duration = (
                max(0.0, finished_at - started_at)
                if started_at is not None and finished_at is not None
                else None
            )
            records.append(
                {
                    **identity,
                    "job_id": job_id,
                    "stage": _job_stage(full),
                    "status": _normalized_status(full.get("status")),
                    "created_at": _safe_float(full.get("created_at")) or 0.0,
                    "duration_seconds": duration,
                    "usage": usage,
                    "adoption_applicable": bool(
                        signal.get("adoption_applicable")
                    ),
                    "adoption_known": bool(signal.get("adoption_known")),
                    "adopted": bool(signal.get("adopted")),
                }
            )

        grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(
                (record["template_key"], record["template_revision"]), []
            ).append(record)
        versions = [
            self._aggregate(group_records)
            for group_records in grouped.values()
        ]
        versions.sort(
            key=lambda item: (
                max((job["created_at"] for job in item["recent_jobs"]), default=0),
                item["template_key"],
                item["template_revision"],
            ),
            reverse=True,
        )
        alerts, watch_items = self._regressions(records)
        return {
            "project_id": int(project_id),
            "filters": {
                "stage": stage,
                "template_key": normalized_key or None,
                "limit": requested_limit,
            },
            "summary": {
                "terminal_jobs_scanned": len(candidates),
                "attributed_jobs": attributed,
                "unattributed_jobs": unattributed,
                "attribution_coverage": _ratio(attributed, len(candidates)),
                "privacy_hidden_jobs": privacy_hidden,
                "returned_jobs": len(records),
                "truncated": truncated,
            },
            "metric_contract": {
                "tokens": "仅汇总供应商响应 usage；不会用字符数或预估 Token 回填",
                "adoption": "创作资源以锁定/批准或当前 Head 为准；优化以人工接受草稿为准；审核报告不适用",
                "historical_data": "缺少 usage、模板身份或采纳信号的历史任务只计覆盖缺口",
            },
            "versions": versions,
            "alerts": alerts,
            "watch_items": watch_items,
        }

    @staticmethod
    def _template_visible(
        template: dict[str, Any] | None,
        *,
        project_id: int,
        actor_user_id: str,
        template_key: str,
    ) -> bool:
        if template is None:
            return not template_key.startswith(("user-", "project-"))
        if template.get("owner_scope") == "user":
            return str(template.get("owner_user_id") or "") == str(actor_user_id)
        if template.get("owner_scope") == "project":
            return int(template.get("project_id") or 0) == int(project_id)
        return True

    def _resource_signals(self, project_id: int) -> dict[str, dict[str, Any]]:
        signals: dict[str, dict[str, Any]] = {}
        with self.repository._connect() as connection:
            head_rows = connection.execute(
                "SELECT stage, episode_number, resource_ref FROM creative_resource_heads "
                "WHERE project_id = ? AND resource_scope = 'free'",
                (int(project_id),),
            ).fetchall()
            heads = {
                (
                    str(row["stage"]),
                    int(row["episode_number"] or 0),
                    str(row["resource_ref"]),
                )
                for row in head_rows
            }
            specs = (
                (
                    "creative_blueprints",
                    "brief",
                    "SELECT id, candidate_group_id, status, resource_scope, generation_meta_json "
                    "FROM creative_blueprints WHERE project_id = ?",
                ),
                (
                    "creative_characters",
                    "characters",
                    "SELECT id, batch_id, status, resource_scope, generation_meta_json "
                    "FROM creative_characters WHERE project_id = ?",
                ),
                (
                    "creative_relationships",
                    "characters",
                    "SELECT id, batch_id, status, resource_scope, generation_meta_json "
                    "FROM creative_relationships WHERE project_id = ?",
                ),
                (
                    "creative_episodes",
                    "",
                    "SELECT id, stage, episode_number, batch_id, status, resource_scope, generation_meta_json "
                    "FROM creative_episodes WHERE project_id = ?",
                ),
            )
            for table, fixed_stage, query in specs:
                if not self._table_exists(connection, table):
                    continue
                for row in connection.execute(query, (int(project_id),)).fetchall():
                    try:
                        meta = json.loads(row["generation_meta_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(meta, dict):
                        continue
                    job_id = str(meta.get("job_id") or "").strip()
                    if not job_id:
                        continue
                    row_stage = fixed_stage or str(row["stage"])
                    scope = str(row["resource_scope"] or "sequential")
                    if scope == "free":
                        if row_stage == "brief":
                            head_ref = str(row["id"])
                            head_episode = 0
                        elif row_stage in {"characters", "outline"}:
                            head_ref = str(row["batch_id"])
                            head_episode = 0
                        else:
                            head_ref = str(row["id"])
                            head_episode = int(row["episode_number"] or 0)
                        adopted = (row_stage, head_episode, head_ref) in heads
                    else:
                        adopted = str(row["status"] or "") in {
                            "brief_locked",
                            "locked",
                            "approved",
                        }
                    self._merge_signal(
                        signals,
                        job_id,
                        stage=row_stage,
                        identity=_identity_from_values(meta),
                        adopted=adopted,
                    )

            if self._table_exists(connection, "script_optimizations") and self._table_exists(
                connection, "script_optimization_drafts"
            ):
                rows = connection.execute(
                    "SELECT optimization.job_id, optimization.prompt_snapshot_json, "
                    "draft.status FROM script_optimizations optimization "
                    "JOIN script_optimization_drafts draft "
                    "ON draft.optimization_id = optimization.id "
                    "WHERE optimization.project_id = ?",
                    (int(project_id),),
                ).fetchall()
                for row in rows:
                    job_id = str(row["job_id"] or "").strip()
                    if not job_id:
                        continue
                    try:
                        snapshot = json.loads(row["prompt_snapshot_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        snapshot = {}
                    self._merge_signal(
                        signals,
                        job_id,
                        stage="optimization",
                        identity=_identity_from_values(
                            {"prompt_snapshot": snapshot}
                        ),
                        adopted=str(row["status"] or "") == "accepted",
                    )
        return signals

    @staticmethod
    def _table_exists(connection: Any, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is not None

    @staticmethod
    def _merge_signal(
        signals: dict[str, dict[str, Any]],
        job_id: str,
        *,
        stage: str,
        identity: dict[str, Any],
        adopted: bool,
    ) -> None:
        signal = signals.setdefault(
            job_id,
            {
                "stage": stage,
                "identity": identity,
                "adoption_applicable": True,
                "adoption_known": True,
                "adopted": False,
            },
        )
        if identity and not signal.get("identity"):
            signal["identity"] = identity
        signal["adopted"] = bool(signal.get("adopted") or adopted)

    @staticmethod
    def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
        first = records[0]
        succeeded = sum(record["status"] == "succeeded" for record in records)
        failed = sum(record["status"] == "failed" for record in records)
        cancelled = sum(record["status"] == "cancelled" for record in records)
        outcome_count = succeeded + failed
        durations = [
            float(record["duration_seconds"])
            for record in records
            if record["duration_seconds"] is not None
        ]
        usages = [record["usage"] for record in records if record["usage"] is not None]
        complete_usages = [usage for usage in usages if usage["complete"]]
        observed = {
            key: sum(int(usage[key]) for usage in usages)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cached_tokens",
                "reasoning_tokens",
            )
        }
        complete_total_tokens = sum(
            int(usage["total_tokens"]) for usage in complete_usages
        )
        calls = sum(int(usage["call_count"]) for usage in usages)
        reported_calls = sum(
            int(usage["reported_call_count"]) for usage in usages
        )
        adoption_candidates = [
            record
            for record in records
            if record["status"] == "succeeded"
            and record["adoption_applicable"]
        ]
        adoption_known = [
            record for record in adoption_candidates if record["adoption_known"]
        ]
        adopted = sum(record["adopted"] for record in adoption_known)
        recent = sorted(records, key=lambda record: record["created_at"], reverse=True)[:10]
        return {
            "template_id": first["template_id"],
            "template_key": first["template_key"],
            "template_title": first["template_title"] or first["template_key"],
            "template_revision": first["template_revision"],
            "stages": sorted({record["stage"] for record in records}),
            "jobs": {
                "terminal": len(records),
                "outcome": outcome_count,
                "succeeded": succeeded,
                "failed": failed,
                "cancelled": cancelled,
                "success_rate": _ratio(succeeded, outcome_count),
                "failure_rate": _ratio(failed, outcome_count),
            },
            "duration": {
                "sample_count": len(durations),
                "average_seconds": (
                    round(sum(durations) / len(durations), 3) if durations else None
                ),
                "p50_seconds": round(float(median(durations)), 3) if durations else None,
                "coverage": _ratio(len(durations), len(records)),
            },
            "tokens": {
                "observed": observed,
                "instrumented_job_count": len(usages),
                "complete_job_count": len(complete_usages),
                "complete_total_tokens": complete_total_tokens,
                "average_total_tokens": (
                    round(complete_total_tokens / len(complete_usages), 3)
                    if complete_usages
                    else None
                ),
                "job_coverage": _ratio(len(complete_usages), len(records)),
                "reported_call_coverage": _ratio(reported_calls, calls),
            },
            "adoption": {
                "applicable_job_count": len(adoption_candidates),
                "known_job_count": len(adoption_known),
                "adopted_job_count": adopted,
                "rate": _ratio(adopted, len(adoption_known)),
                "coverage": _ratio(len(adoption_known), len(adoption_candidates)),
            },
            "recent_jobs": [
                {
                    "job_id": record["job_id"],
                    "stage": record["stage"],
                    "status": record["status"],
                    "created_at": record["created_at"],
                    "duration_seconds": (
                        round(record["duration_seconds"], 3)
                        if record["duration_seconds"] is not None
                        else None
                    ),
                    "token_complete": bool(
                        record["usage"] and record["usage"]["complete"]
                    ),
                    "adoption": (
                        record["adopted"]
                        if record["adoption_applicable"]
                        and record["adoption_known"]
                        else None
                    ),
                }
                for record in recent
            ],
        }

    def _regressions(
        self, records: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(
                (
                    record["template_key"],
                    record["stage"],
                    record["template_revision"],
                ),
                [],
            ).append(record)
        alerts: list[dict[str, Any]] = []
        watch_items: list[dict[str, Any]] = []
        pairs: dict[tuple[str, str], list[int]] = {}
        for template_key, stage, revision in grouped:
            pairs.setdefault((template_key, stage), []).append(revision)
        for (template_key, stage), revisions in pairs.items():
            ordered = sorted(set(revisions))
            for baseline_revision, current_revision in zip(ordered, ordered[1:]):
                if current_revision != baseline_revision + 1:
                    continue
                baseline = self._aggregate(
                    grouped[(template_key, stage, baseline_revision)]
                )
                current = self._aggregate(grouped[(template_key, stage, current_revision)])
                baseline_sample = int(baseline["jobs"]["outcome"])
                current_sample = int(current["jobs"]["outcome"])
                if min(baseline_sample, current_sample) < _MIN_REGRESSION_SAMPLE:
                    watch_items.append(
                        {
                            "template_key": template_key,
                            "stage": stage,
                            "baseline_revision": baseline_revision,
                            "current_revision": current_revision,
                            "baseline_sample_count": baseline_sample,
                            "current_sample_count": current_sample,
                            "required_sample_count": _MIN_REGRESSION_SAMPLE,
                            "message": "相邻版本终态样本不足，继续观察，不判定异常回归",
                        }
                    )
                    continue
                self._append_delta_alert(
                    alerts,
                    template_key=template_key,
                    stage=stage,
                    metric="failure_rate",
                    label="失败率",
                    baseline_revision=baseline_revision,
                    current_revision=current_revision,
                    baseline_value=baseline["jobs"]["failure_rate"],
                    current_value=current["jobs"]["failure_rate"],
                    baseline_sample=baseline_sample,
                    current_sample=current_sample,
                    threshold=0.20,
                    direction="increase_absolute",
                    baseline_coverage=1.0,
                    current_coverage=1.0,
                )
                self._append_metric_alert(
                    alerts,
                    template_key=template_key,
                    stage=stage,
                    metric="adoption_rate",
                    label="人工采纳率",
                    baseline_revision=baseline_revision,
                    current_revision=current_revision,
                    baseline=baseline["adoption"],
                    current=current["adoption"],
                    value_key="rate",
                    sample_key="known_job_count",
                    threshold=0.20,
                    direction="decrease_absolute",
                )
                self._append_metric_alert(
                    alerts,
                    template_key=template_key,
                    stage=stage,
                    metric="average_total_tokens",
                    label="平均 Token",
                    baseline_revision=baseline_revision,
                    current_revision=current_revision,
                    baseline=baseline["tokens"],
                    current=current["tokens"],
                    value_key="average_total_tokens",
                    sample_key="complete_job_count",
                    threshold=0.30,
                    direction="increase_relative",
                )
                self._append_delta_alert(
                    alerts,
                    template_key=template_key,
                    stage=stage,
                    metric="p50_duration_seconds",
                    label="P50 耗时",
                    baseline_revision=baseline_revision,
                    current_revision=current_revision,
                    baseline_value=baseline["duration"]["p50_seconds"],
                    current_value=current["duration"]["p50_seconds"],
                    baseline_sample=baseline["duration"]["sample_count"],
                    current_sample=current["duration"]["sample_count"],
                    threshold=0.50,
                    direction="increase_relative",
                    baseline_coverage=baseline["duration"]["coverage"],
                    current_coverage=current["duration"]["coverage"],
                )
        return alerts, watch_items

    def _append_metric_alert(self, alerts: list[dict[str, Any]], **values: Any) -> None:
        baseline = values.pop("baseline")
        current = values.pop("current")
        value_key = str(values.pop("value_key"))
        sample_key = str(values.pop("sample_key"))
        self._append_delta_alert(
            alerts,
            baseline_value=baseline.get(value_key),
            current_value=current.get(value_key),
            baseline_sample=int(baseline.get(sample_key) or 0),
            current_sample=int(current.get(sample_key) or 0),
            baseline_coverage=baseline.get("job_coverage", baseline.get("coverage")),
            current_coverage=current.get("job_coverage", current.get("coverage")),
            **values,
        )

    @staticmethod
    def _append_delta_alert(
        alerts: list[dict[str, Any]],
        *,
        template_key: str,
        stage: str,
        metric: str,
        label: str,
        baseline_revision: int,
        current_revision: int,
        baseline_value: Any,
        current_value: Any,
        baseline_sample: int,
        current_sample: int,
        threshold: float,
        direction: str,
        baseline_coverage: Any,
        current_coverage: Any,
    ) -> None:
        if (
            baseline_value is None
            or current_value is None
            or min(int(baseline_sample), int(current_sample)) < _MIN_REGRESSION_SAMPLE
        ):
            return
        baseline_number = float(baseline_value)
        current_number = float(current_value)
        if direction == "increase_absolute":
            delta = current_number - baseline_number
            regressed = delta >= threshold
        elif direction == "decrease_absolute":
            delta = current_number - baseline_number
            regressed = delta <= -threshold
        else:
            if baseline_number <= 0:
                return
            delta = (current_number - baseline_number) / baseline_number
            regressed = delta >= threshold
        if not regressed:
            return
        alerts.append(
            {
                "severity": "warning",
                "template_key": template_key,
                "stage": stage,
                "metric": metric,
                "metric_label": label,
                "baseline_revision": int(baseline_revision),
                "current_revision": int(current_revision),
                "baseline_value": baseline_number,
                "current_value": current_number,
                "delta": round(delta, 4),
                "delta_kind": "relative" if direction.endswith("relative") else "absolute",
                "threshold": threshold,
                "sample_size": {
                    "baseline": int(baseline_sample),
                    "current": int(current_sample),
                },
                "coverage": {
                    "baseline": baseline_coverage,
                    "current": current_coverage,
                },
                "message": (
                    f"{label}从版本 {baseline_revision} 的 {baseline_number:g} "
                    f"变化到版本 {current_revision} 的 {current_number:g}，达到回归阈值"
                ),
            }
        )
