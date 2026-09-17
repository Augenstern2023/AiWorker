# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Application boundary for project prompt-library operations."""

from __future__ import annotations

from typing import Any

from creative.prompt_library_intelligence import (
    preview_prompt_import,
    recommend_prompt_templates,
)
from creative.prompt_store import PromptStore
from creative.repository import CreativeRepository


class ProjectPromptService:
    def __init__(self, repository: CreativeRepository) -> None:
        self.repository = repository
        self.store = PromptStore(repository.db_path)

    def _project(self, project_id: int) -> dict[str, Any]:
        project = self.repository.get_project(int(project_id))
        if project is None:
            raise ValueError("创作项目不存在")
        return project

    def catalog(self, project_id: int, actor_user_id: str) -> dict[str, Any]:
        self._project(project_id)
        return {
            "settings": self.store.project_settings(project_id),
            "templates": self.store.list_catalog(
                project_id=project_id, actor_user_id=actor_user_id
            ),
        }

    def recommendations(
        self,
        project_id: int,
        actor_user_id: str,
        *,
        stage: str | None = None,
    ) -> dict[str, Any]:
        project = self._project(project_id)
        templates = self.store.list_catalog(
            project_id=project_id, actor_user_id=actor_user_id
        )
        return recommend_prompt_templates(project, templates, stage=stage)

    async def import_preview(
        self,
        project_id: int,
        *,
        provider: Any,
        source_text: str,
        target_stage: str | None = None,
    ) -> dict[str, Any]:
        self._project(project_id)
        return await preview_prompt_import(
            provider,
            source_text,
            target_stage=target_stage,
        )

    def enable_existing_project(
        self,
        project_id: int,
        actor_user_id: str,
        *,
        template_key: str = "classic-compatible",
    ) -> dict[str, Any]:
        self._project(project_id)
        return self.store.enable_project(
            project_id,
            actor_user_id=actor_user_id,
            template_key=template_key,
        )

    def enable_new_project(
        self, project_id: int, actor_user_id: str
    ) -> dict[str, Any]:
        self._project(project_id)
        settings = self.store.project_settings(project_id)
        if settings.get("enabled"):
            return settings
        return self.store.enable_project(
            project_id,
            actor_user_id=actor_user_id,
            template_key="generic-vertical-short",
        )

    def module_state(
        self,
        project_id: int,
        stage: str,
        *,
        episode_number: int = 0,
        actor_user_id: str = "",
    ) -> dict[str, Any]:
        self._project(project_id)
        if stage not in {
            "brief", "characters", "outline", "beats", "script", "review", "optimization",
        }:
            raise ValueError(
                "模板阶段只支持 brief、characters、outline、beats、script、review 或 optimization"
            )
        non_episode_stages = {
            "brief": "Brief 模板不支持单集覆盖",
            "characters": "人物模板不支持单集覆盖",
            "outline": "大纲模板不支持单集覆盖",
            "review": "审核模板不支持单集覆盖",
            "optimization": "优化模板不支持单集覆盖",
        }
        if stage in non_episode_stages and int(episode_number) != 0:
            raise ValueError(non_episode_stages[stage])
        settings = self.store.project_settings(project_id)
        effective = None
        if settings.get("enabled"):
            effective = self.store.resolve_snapshot(
                project_id=project_id,
                stage=stage,
                mode="single",
                episode_number=episode_number,
            )
            if effective is not None:
                latest = self.store.get_revision(effective["template_id"])
                effective["latest_revision"] = int(latest["revision"])
                effective["update_available"] = (
                    int(latest["revision"]) > int(effective["revision"])
                )
        return {
            "settings": settings,
            "stage": stage,
            "episode_number": int(episode_number),
            "stage_binding": self.store.binding(project_id, stage, 0),
            "episode_binding": (
                self.store.binding(project_id, stage, episode_number)
                if episode_number > 0
                else None
            ),
            "effective": effective,
            "templates": self.store.list_catalog(
                project_id=project_id, actor_user_id=actor_user_id
            ),
        }

    def bind(
        self,
        *,
        project_id: int,
        stage: str,
        episode_number: int,
        template_key: str,
        template_revision: int | None,
        override: dict[str, Any] | None,
        actor_user_id: str,
    ) -> dict[str, Any]:
        self._project(project_id)
        template = self.store.get_template_by_key(template_key)
        if template is None:
            raise ValueError("模板不存在")
        revision = self.store.get_revision(template["id"], template_revision)
        settings = self.store.project_settings(project_id)
        binding = self.store.set_binding(
            project_id=project_id,
            stage=stage,
            episode_number=episode_number,
            template_id=template["id"],
            template_revision=int(revision["revision"]),
            override=override,
            actor_user_id=actor_user_id,
        )
        if not settings.get("enabled"):
            # Keep legacy projects on their original prompts until the first
            # persistent choice. Saving a default is the explicit opt-in and the
            # compatibility default preserves all other stages.
            self.store.enable_project(
                project_id,
                actor_user_id=actor_user_id,
                template_key="classic-compatible",
            )
        return {
            "binding": binding,
            "settings": self.store.project_settings(project_id),
            "effective": self.store.resolve_snapshot(
                project_id=project_id,
                stage=stage,
                mode="single",
                episode_number=episode_number,
            ),
        }

    def create_template(
        self,
        *,
        project_id: int,
        actor_user_id: str,
        title: str,
        description: str,
        owner_scope: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        self._project(project_id)
        return self.store.create_template(
            title=title,
            description=description,
            content=content,
            owner_scope=owner_scope,
            owner_user_id=actor_user_id,
            project_id=project_id if owner_scope == "project" else None,
        )

    def save_template_revision(
        self,
        *,
        project_id: int,
        template_id: str,
        actor_user_id: str,
        expected_revision: int,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        self._project(project_id)
        template = self.store.get_template(template_id)
        if template is None:
            raise ValueError("模板不存在")
        if template["owner_scope"] == "user" and template["owner_user_id"] != actor_user_id:
            raise PermissionError("只能修改自己的个人模板")
        if template["owner_scope"] == "project" and int(template["project_id"] or 0) != int(project_id):
            raise PermissionError("项目模板不属于当前项目")
        return self.store.save_revision(
            template_id,
            content,
            expected_revision=expected_revision,
            actor_user_id=actor_user_id,
        )

    def governance(self, project_id: int) -> dict[str, Any]:
        self._project(project_id)
        items = self.store.list_project_template_governance(project_id)
        return {
            "project_id": int(project_id),
            "items": items,
            "status_labels": {
                "pending_review": "待审核",
                "approved": "已批准",
                "deprecated": "已弃用",
                "archived": "已归档",
            },
            "policy": [
                "项目模板的新版本进入待审核状态，批准前不能新绑定或单次使用",
                "已弃用模板不接受新绑定，但已有固定版本继续生成",
                "模板仍被默认、阶段或单集绑定使用时不得归档",
                "归档模板恢复后重新进入待审核，不会自动恢复任何绑定",
            ],
        }

    def govern_template(
        self,
        *,
        project_id: int,
        template_id: str,
        action: str,
        note: str,
        expected_governance_revision: int,
        operation_id: str,
        actor_user_id: str,
    ) -> dict[str, Any]:
        self._project(project_id)
        return self.store.govern_project_template(
            project_id=project_id,
            template_id=template_id,
            action=action,
            note=note,
            expected_governance_revision=expected_governance_revision,
            operation_id=operation_id,
            actor_user_id=actor_user_id,
        )

    def resolve_generation_snapshot(
        self,
        *,
        project_id: int,
        stage: str,
        series: bool,
        mode: str | None = None,
        episode_number: int | None,
        template_key: str | None,
        template_revision: int | None,
        run_override: dict[str, Any] | None,
        actor_user_id: str = "",
    ) -> dict[str, Any] | None:
        return self.store.resolve_snapshot(
            project_id=project_id,
            stage=stage,
            mode=mode or ("series" if series else "single"),
            episode_number=int(episode_number or 0),
            run_template_key=template_key,
            run_template_revision=template_revision,
            run_override=run_override,
            actor_user_id=actor_user_id,
        )
