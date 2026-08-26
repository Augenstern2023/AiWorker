# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Complete-project review coordination for Writer Agent sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from project_review.source import build_project_review_source


def long_project_review_scope(message: str) -> str | None:
    normalized = "".join(str(message or "").lower().split())
    if any(term in normalized for term in ("完整项目", "全项目")):
        return "project"
    if any(term in normalized for term in ("全部剧本", "所有剧本", "完整剧本")):
        return "script"
    if any(term in normalized for term in ("全部大纲", "所有大纲", "完整大纲", "更新后的项目大纲")):
        return "outline"
    return None


class ProjectReviewCoordinator:
    def __init__(
        self, *, repository: Any, store: Any, permission_service: Any,
        job_manager: Any, runtime_dir: str | Path,
    ) -> None:
        self.repository = repository
        self.store = store
        self.permission_service = permission_service
        self.job_manager = job_manager
        self.runtime_dir = Path(runtime_dir)

    def preview(self, session_id: int, principal: Any, audit_scope: str) -> dict[str, Any]:
        session, project = self._session_project(session_id, principal)
        del session
        try:
            source = build_project_review_source(project, audit_scope)
        except ValueError as exc:
            raise ProjectReviewError(409, str(exc)) from exc
        return self._preview_payload(source)

    def create(
        self, session_id: int, principal: Any, audit_scope: str,
        expected_fingerprint: str, client_request_id: str,
    ) -> dict[str, Any]:
        session, project = self._session_project(session_id, principal)
        if not str(client_request_id).strip():
            raise ProjectReviewError(400, "确认请求缺少幂等键")
        try:
            source = build_project_review_source(project, audit_scope)
        except ValueError as exc:
            raise ProjectReviewError(409, str(exc)) from exc
        if str(source["fingerprint"]) != str(expected_fingerprint):
            raise ProjectReviewError(
                409, "项目内容已更新，请刷新审核预检后再确认",
                extra=self._preview_payload(source),
            )
        source["writer_session_id"] = int(session_id)
        episode_numbers = sorted({
            int(number)
            for stage in dict(source.get("stages") or {}).values()
            for number in dict(stage).get("episode_numbers") or []
        })
        try:
            review, _created = self.repository.create_project_review_once(
                project_id=int(project["id"]), episode_numbers=episode_numbers,
                source_fingerprint=str(source["fingerprint"]), source_payload=source,
                created_by=principal.user_id,
                request_key=(
                    f"writer-session:{int(session_id)}:{client_request_id}"
                ),
            )
        except ValueError as exc:
            raise ProjectReviewError(409, str(exc)) from exc
        try:
            state = self._queue(review, principal, retry_failed_operation=False)
        except ProjectReviewError as exc:
            self.repository.fail_script_review(review["id"], str(exc))
            raise
        review = self.repository.set_script_review_job(review["id"], str(state.job_id))
        self.store.append_project_review_message_once(
            session_id, principal.user_id, review_id=int(review["id"]),
            audit_scope=audit_scope,
        )
        return {"review": review, "job_id": str(state.job_id), "created": _created}

    def get(self, session_id: int, principal: Any, review_id: int) -> dict[str, Any]:
        self._session_project(session_id, principal)
        review = self.repository.get_script_review(int(review_id))
        if not review or not self._belongs_to_session(review, session_id):
            raise ProjectReviewError(404, "完整项目审核不存在")
        self._assert_review_readable(review, principal)
        return review

    def resume(self, session_id: int, principal: Any, review_id: int) -> dict[str, Any]:
        self._session_project(session_id, principal)
        review = self.get(session_id, principal, review_id)
        if review.get("status") != "failed":
            raise ProjectReviewError(409, "只有失败的完整项目审核可以恢复")
        try:
            review = self.repository.prepare_project_review_resume(int(review_id))
        except ValueError as exc:
            raise ProjectReviewError(409, str(exc)) from exc
        try:
            state = self._queue(review, principal, retry_failed_operation=True)
        except ProjectReviewError as exc:
            self.repository.fail_script_review(review["id"], str(exc))
            raise
        review = self.repository.set_script_review_job(review["id"], str(state.job_id))
        return {"review": review, "job_id": str(state.job_id)}

    def _session_project(self, session_id: int, principal: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        session = self.store.get(
            int(session_id), None if principal.is_system_admin else principal.user_id
        )
        if not session:
            raise ProjectReviewError(404, "创作会话不存在")
        project_id = session.get("project_id")
        if project_id is None:
            raise ProjectReviewError(409, "请先关联创作项目后再发起完整项目审核")
        if self.permission_service is None or not self.permission_service.can_read_project(
            principal, str(project_id)
        ):
            raise ProjectReviewError(403, "当前账号已无权读取关联项目")
        project = self.repository.get_project(int(project_id))
        if not project:
            raise ProjectReviewError(409, "关联创作项目已不存在")
        return session, project

    @staticmethod
    def _preview_payload(source: dict[str, Any]) -> dict[str, Any]:
        manifest = dict(source.get("manifest") or {})
        return {
            "audit_scope": source.get("audit_scope"), "manifest": manifest,
            "fingerprint": source.get("fingerprint"),
            "batches": {
                name: list(dict(stage).get("batches") or [])
                for name, stage in dict(source.get("stages") or {}).items()
            },
        }

    @staticmethod
    def _belongs_to_session(review: dict[str, Any], session_id: int) -> bool:
        return int(dict(review.get("source_payload") or {}).get("writer_session_id") or 0) == int(session_id)

    def _assert_review_readable(self, review: dict[str, Any], principal: Any) -> None:
        if not principal.is_system_admin and review.get("created_by") != principal.user_id:
            raise ProjectReviewError(403, "没有访问此完整项目审核的权限")
        project_id = review.get("project_id")
        if project_id is None or self.permission_service is None or not self.permission_service.can_read_project(principal, str(project_id)):
            raise ProjectReviewError(403, "当前账号已无权读取关联项目")

    def _queue(self, review: dict[str, Any], principal: Any, *, retry_failed_operation: bool) -> Any:
        if self.job_manager is None:
            raise ProjectReviewError(503, "完整项目审核任务服务不可用")
        created_by = str(review.get("created_by") or principal.user_id)
        operation_id = f"project-review:{review['id']}"
        payload = {
            "platform": "creative", "crawler_type": "project_review",
            "keywords": [], "save_data_option": "json", "get_comments": False,
            "get_sub_comments": False,
            "extra": {
                "review_id": review["id"], "project_id": review["project_id"],
                "created_by": created_by,
                "created_by_user_id": created_by,
                "db_path": str(Path(self.repository.db_path).resolve()),
                "runtime_dir": str(self.runtime_dir.resolve()),
                "retry_failed_operation": retry_failed_operation,
            },
        }
        try:
            return self.job_manager.get_or_create_job(
                operation_id, payload, created_by_user_id=created_by,
                workflow_task_id="", project_id=review["project_id"],
            )
        except Exception as exc:
            raise ProjectReviewError(503, "无法创建完整项目审核任务") from exc


class ProjectReviewError(Exception):
    def __init__(self, status_code: int, message: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.extra = extra
