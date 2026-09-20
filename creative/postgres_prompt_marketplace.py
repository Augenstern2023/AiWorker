# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Moderated publishing and project-local installation for prompt templates."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from creative.prompt_registry import (
    PROMPT_LIBRARY_MODES,
    PROMPT_LIBRARY_SCHEMA_VERSION,
    PROMPT_LIBRARY_STAGES,
    content_hash,
    normalize_template_content,
)
from creative.postgres_prompt_store import PromptStore
from creative.repository import CreativeRepository


INTERNAL_LICENSE_CODE = "INTERNAL"
MARKETPLACE_SOURCE_TYPES = {
    "original": "原创",
    "authorized": "已获授权",
    "open_license": "开放许可来源",
    "public_domain": "公有领域",
}


class PromptMarketplaceService:
    """Own the public snapshot, moderation, install, and rating boundaries."""

    def __init__(
        self,
        repository: CreativeRepository,
        prompt_store: PromptStore | None = None,
    ) -> None:
        if not hasattr(repository, "database"):
            raise TypeError(
                "PromptMarketplaceService requires a PostgreSQL creative repository"
            )
        self.repository = repository
        self.prompt_store = prompt_store or PromptStore(repository)

    def _connect(self) -> Any:
        return self.prompt_store._connect()

    @staticmethod
    def _compatibility(content: dict[str, Any]) -> dict[str, Any]:
        stage_rules = content.get("stage_rules") or {}
        stages = [
            stage
            for stage in sorted(PROMPT_LIBRARY_STAGES)
            if isinstance(stage_rules.get(stage), dict)
            and all(
                str(stage_rules[stage].get(mode) or "").strip()
                for mode in PROMPT_LIBRARY_MODES
            )
        ]
        return {
            "schema_version": PROMPT_LIBRARY_SCHEMA_VERSION,
            "runtime_contract": "model-independent-prompt-snapshot-v1",
            "supported_stages": stages,
            "supported_modes": sorted(PROMPT_LIBRARY_MODES),
        }

    @staticmethod
    def _clean_tags(tags: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in tags:
            value = str(raw or "").strip()[:30]
            if value and value.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(value)
        if len(cleaned) > 10:
            raise ValueError("市场标签不能超过 10 个")
        return cleaned

    @staticmethod
    def _submission_row(
        row: Any,
        *,
        actor_user_id: str = "",
        include_private: bool = False,
    ) -> dict[str, Any]:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        item["content"] = json.loads(item.pop("content_json") or "{}")
        item["compatibility"] = json.loads(
            item.pop("compatibility_json") or "{}"
        )
        item["is_owner"] = bool(
            actor_user_id
            and str(item.get("publisher_user_id") or "") == actor_user_id
        )
        if not include_private:
            item.pop("publisher_user_id", None)
            item.pop("request_hash", None)
            item.pop("operation_id", None)
            item.pop("rights_statement", None)
            item.pop("reviewed_by", None)
        return item

    @staticmethod
    def _installation_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item.pop("request_hash", None)
        return item

    def _project(self, project_id: int) -> dict[str, Any]:
        project = self.repository.get_project(int(project_id))
        if project is None:
            raise ValueError("创作项目不存在")
        return project

    def submit(
        self,
        *,
        project_id: int,
        template_id: str,
        expected_template_revision: int,
        operation_id: str,
        title: str,
        description: str,
        tags: list[str],
        attribution_name: str,
        source_type: str,
        source_url: str,
        rights_statement: str,
        publication_confirmed: bool,
        rights_confirmed: bool,
        actor_user_id: str,
    ) -> dict[str, Any]:
        self._project(project_id)
        template = self.prompt_store.get_template(template_id)
        if template is None:
            raise ValueError("模板不存在")
        if template.get("owner_scope") != "user":
            raise ValueError("模板市场只接受个人模板；请先另存到我的模板")
        if str(template.get("owner_user_id") or "") != actor_user_id:
            raise PermissionError("只能发布自己的个人模板")
        if str(template.get("status") or "") != "active":
            raise ValueError("已归档模板不能发布")
        if not publication_confirmed:
            raise ValueError("必须确认模板内容会在公司内部展示并可被安装")
        if not rights_confirmed:
            raise ValueError("必须确认拥有发布与内部使用所需权利")
        if source_type not in MARKETPLACE_SOURCE_TYPES:
            raise ValueError("不支持的模板来源类型")
        normalized_source_url = str(source_url or "").strip()
        if source_type != "original" and not normalized_source_url:
            raise ValueError("非原创模板必须提供可核验的来源链接")
        if normalized_source_url:
            parsed_source = urlsplit(normalized_source_url)
            if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
                raise ValueError("来源链接必须是有效的 HTTP 或 HTTPS 地址")
        normalized_rights = str(rights_statement or "").strip()
        if len(normalized_rights) < 10:
            raise ValueError("来源与权利说明至少需要 10 个字符")
        normalized_attribution = str(attribution_name or "").strip()
        if not normalized_attribution:
            raise ValueError("模板署名不能为空")
        revision = self.prompt_store.get_revision(
            template_id, int(expected_template_revision)
        )
        normalized_content = normalize_template_content(revision["content"])
        frozen_hash = content_hash(normalized_content)
        if frozen_hash != str(revision["content_hash"]):
            raise ValueError("模板内容哈希校验失败")
        cleaned_tags = self._clean_tags(tags)
        request_payload = {
            "template_id": str(template_id),
            "template_revision": int(expected_template_revision),
            "template_content_hash": frozen_hash,
            "title": str(title).strip(),
            "description": str(description).strip(),
            "tags": cleaned_tags,
            "attribution_name": normalized_attribution,
            "license_code": INTERNAL_LICENSE_CODE,
            "source_type": source_type,
            "source_url": normalized_source_url,
            "rights_statement": normalized_rights,
        }
        request_hash = content_hash(request_payload)
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_operation = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions "
                "WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
            if existing_operation is not None:
                if str(existing_operation["request_hash"]) != request_hash:
                    raise ValueError("市场发布操作标识已用于其他请求")
                return self._submission_row(
                    existing_operation,
                    actor_user_id=actor_user_id,
                    include_private=True,
                )
            existing_revision = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions "
                "WHERE template_id = ? AND template_revision = ?",
                (str(template_id), int(expected_template_revision)),
            ).fetchone()
            if existing_revision is not None:
                if str(existing_revision["request_hash"]) != request_hash:
                    raise ValueError("该模板版本已经以其他发布信息提交")
                return self._submission_row(
                    existing_revision,
                    actor_user_id=actor_user_id,
                    include_private=True,
                )
            latest = self.prompt_store.get_revision(template_id)
            if int(latest["revision"]) != int(expected_template_revision):
                raise ValueError("只能提交个人模板的最新版本")
            submission_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO creative_prompt_market_submissions (
                    id, operation_id, request_hash, template_id, template_key,
                    template_revision, template_content_hash, title,
                    description, tags_json, publisher_user_id,
                    attribution_name, license_code, source_type, source_url,
                    rights_statement, content_json, compatibility_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission_id,
                    str(operation_id),
                    request_hash,
                    str(template_id),
                    str(template["template_key"]),
                    int(expected_template_revision),
                    frozen_hash,
                    str(title).strip(),
                    str(description).strip(),
                    json.dumps(cleaned_tags, ensure_ascii=False),
                    actor_user_id,
                    normalized_attribution,
                    INTERNAL_LICENSE_CODE,
                    source_type,
                    normalized_source_url,
                    normalized_rights,
                    json.dumps(normalized_content, ensure_ascii=False),
                    json.dumps(self._compatibility(normalized_content), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions WHERE id = ?",
                (submission_id,),
            ).fetchone()
        return self._submission_row(
            row, actor_user_id=actor_user_id, include_private=True
        )

    def _rating_summary(
        self, conn: Any, submission_id: str
    ) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT score, COUNT(*) AS total FROM creative_prompt_market_ratings "
            "WHERE submission_id = ? GROUP BY score ORDER BY score",
            (str(submission_id),),
        ).fetchall()
        distribution = {str(score): 0 for score in range(1, 6)}
        weighted = 0
        total = 0
        for row in rows:
            score = int(row["score"])
            count = int(row["total"])
            distribution[str(score)] = count
            weighted += score * count
            total += count
        return {
            "count": total,
            "average": round(weighted / total, 2) if total else None,
            "distribution": distribution,
            "contract": "每个登录用户对同一模板只能保留一条已安装评分",
        }

    def _official_items(
        self,
        *,
        project_id: int,
        actor_user_id: str,
        query: str,
        stage: str | None,
    ) -> list[dict[str, Any]]:
        """Project the canonical built-ins into the marketplace discovery UI.

        Official templates stay system-owned and immediately usable. They are
        deliberately not marketplace submissions, so they never acquire an
        installation, rating, or moderation lifecycle.
        """

        normalized_query = str(query or "").strip().casefold()
        items: list[dict[str, Any]] = []
        for template in self.prompt_store.list_catalog(
            project_id=project_id, actor_user_id=actor_user_id
        ):
            template_key = str(template.get("template_key") or "")
            if template.get("owner_scope") != "system":
                continue
            # Legacy imports may contain system-scoped copies. Only the
            # canonical built-in identity belongs in the official shelf.
            if str(template.get("id") or "") != f"system:{template_key}":
                continue
            supported_stages = list(template.get("supported_stages") or [])
            if stage and stage not in supported_stages:
                continue
            dimensions = template.get("content", {}).get("dimensions") or {}
            searchable = " ".join(
                [
                    str(template.get("title") or ""),
                    str(template.get("description") or ""),
                    str(template.get("content", {}).get("shared_dna") or ""),
                    *[str(value or "") for value in dimensions.values()],
                ]
            ).casefold()
            if normalized_query and normalized_query not in searchable:
                continue
            items.append(
                {
                    **template,
                    "marketplace_kind": "official",
                    "attribution_name": "AiWorker 官方",
                    "tags": list(
                        dict.fromkeys(
                            str(value) for value in dimensions.values() if value
                        )
                    ),
                    "compatibility": {
                        "schema_version": PROMPT_LIBRARY_SCHEMA_VERSION,
                        "runtime_contract": "model-independent-prompt-snapshot-v1",
                        "supported_stages": supported_stages,
                        "supported_modes": sorted(PROMPT_LIBRARY_MODES),
                    },
                }
            )
        return sorted(items, key=lambda item: str(item.get("title") or ""))

    def marketplace(
        self,
        *,
        project_id: int,
        actor_user_id: str,
        query: str = "",
        stage: str | None = None,
        is_admin: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._project(project_id)
        if stage and stage not in PROMPT_LIBRARY_STAGES:
            raise ValueError("模板市场阶段筛选无效")
        normalized_query = str(query or "").strip().casefold()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions "
                "WHERE status = 'approved' ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
            installed_rows = conn.execute(
                "SELECT * FROM creative_prompt_market_installations "
                "WHERE project_id = ? ORDER BY created_at DESC",
                (int(project_id),),
            ).fetchall()
            installed_by_submission = {
                str(row["submission_id"]): self._installation_row(row)
                for row in installed_rows
            }
            own_ratings = {
                str(row["submission_id"]): dict(row)
                for row in conn.execute(
                    "SELECT * FROM creative_prompt_market_ratings "
                    "WHERE actor_user_id = ?",
                    (actor_user_id,),
                ).fetchall()
            }
            listings: list[dict[str, Any]] = []
            for row in rows:
                item = self._submission_row(row, actor_user_id=actor_user_id)
                searchable = " ".join(
                    [
                        str(item.get("title") or ""),
                        str(item.get("description") or ""),
                        str(item.get("attribution_name") or ""),
                        " ".join(item.get("tags") or []),
                    ]
                ).casefold()
                if normalized_query and normalized_query not in searchable:
                    continue
                if stage and stage not in (
                    item.get("compatibility", {}).get("supported_stages") or []
                ):
                    continue
                submission_id = str(item["id"])
                item["rating"] = self._rating_summary(conn, submission_id)
                item["install_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) AS total "
                        "FROM creative_prompt_market_installations "
                        "WHERE submission_id = ?",
                        (submission_id,),
                    ).fetchone()["total"]
                )
                item["installation"] = installed_by_submission.get(submission_id)
                item["my_rating"] = own_ratings.get(submission_id)
                listings.append(item)
            own_rows = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions "
                "WHERE publisher_user_id = ? ORDER BY created_at DESC LIMIT 100",
                (actor_user_id,),
            ).fetchall()
            moderation_rows = (
                conn.execute(
                    "SELECT * FROM creative_prompt_market_submissions "
                    "WHERE status = 'pending' ORDER BY created_at ASC LIMIT 100"
                ).fetchall()
                if is_admin
                else []
            )
        return {
            "official_items": self._official_items(
                project_id=project_id,
                actor_user_id=actor_user_id,
                query=query,
                stage=stage,
            ),
            "items": listings,
            "own_submissions": [
                self._submission_row(
                    row, actor_user_id=actor_user_id, include_private=True
                )
                for row in own_rows
            ],
            "moderation_queue": [
                self._submission_row(
                    row, actor_user_id=actor_user_id, include_private=True
                )
                for row in moderation_rows
            ],
            "installations": list(installed_by_submission.values()),
            "source_types": [
                {"code": code, "label": label}
                for code, label in MARKETPLACE_SOURCE_TYPES.items()
            ],
            "can_review": bool(is_admin),
            "policy": [
                "官方模板为系统内置内容，可直接选择用于下一次生成，无需安装",
                "社区发布只接受个人模板，提交即同意在公司内部展示冻结版本的完整内容",
                "审核通过后才能被其他项目发现和安装",
                "安装生成项目内独立副本，不自动绑定，也不跟随来源更新",
                "发布者不能给自己的模板评分；每位用户每个模板只能保留一条评分",
                "撤回或下架只停止新安装，不破坏项目中已有副本",
            ],
        }

    def moderate(
        self,
        *,
        submission_id: str,
        action: str,
        note: str,
        expected_review_revision: int,
        operation_id: str,
        actor_user_id: str,
        is_admin: bool,
    ) -> dict[str, Any]:
        transitions = {
            "approve": ({"pending"}, "approved"),
            "reject": ({"pending"}, "rejected"),
            "remove": ({"approved"}, "removed"),
            "withdraw": ({"pending", "approved"}, "withdrawn"),
        }
        if action not in transitions:
            raise ValueError("模板市场审核操作无效")
        normalized_note = str(note or "").strip()
        if not normalized_note:
            raise ValueError("模板市场状态变更必须填写原因")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions WHERE id = ?",
                (str(submission_id),),
            ).fetchone()
            if current is None:
                raise ValueError("模板市场发布申请不存在")
            if action == "withdraw":
                if str(current["publisher_user_id"]) != actor_user_id:
                    raise PermissionError("只有发布者可以撤回模板")
            elif not is_admin:
                raise PermissionError("只有系统管理员可以审核或下架市场模板")
            allowed_from, to_status = transitions[action]
            request_payload = {
                "submission_id": str(submission_id),
                "action": action,
                "note": normalized_note,
                "expected_review_revision": int(expected_review_revision),
            }
            request_hash = content_hash(request_payload)
            existing_event = conn.execute(
                "SELECT * FROM creative_prompt_market_events WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
            if existing_event is not None:
                if str(existing_event["request_hash"]) != request_hash:
                    raise ValueError("市场治理操作标识已用于其他请求")
                row = conn.execute(
                    "SELECT * FROM creative_prompt_market_submissions WHERE id = ?",
                    (str(submission_id),),
                ).fetchone()
                return self._submission_row(
                    row, actor_user_id=actor_user_id, include_private=True
                )
            from_status = str(current["status"])
            if from_status not in allowed_from:
                raise ValueError("当前市场状态不允许执行该操作")
            if int(current["review_revision"]) != int(expected_review_revision):
                raise ValueError("模板市场状态已被其他人员更新，请刷新后重试")
            if action == "approve":
                content = normalize_template_content(
                    json.loads(current["content_json"] or "{}")
                )
                if content_hash(content) != str(current["template_content_hash"]):
                    raise ValueError("冻结模板内容哈希校验失败，不能批准")
                compatibility = json.loads(current["compatibility_json"] or "{}")
                if compatibility.get("schema_version") != PROMPT_LIBRARY_SCHEMA_VERSION:
                    raise ValueError("模板与当前提示词运行契约不兼容")
            now = time.time()
            next_revision = int(expected_review_revision) + 1
            updated = conn.execute(
                """
                UPDATE creative_prompt_market_submissions
                SET status = ?, review_revision = ?, review_note = ?,
                    reviewed_by = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ? AND review_revision = ?
                """,
                (
                    to_status,
                    next_revision,
                    normalized_note,
                    actor_user_id,
                    now,
                    now,
                    str(submission_id),
                    int(expected_review_revision),
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("模板市场状态已被其他人员更新，请刷新后重试")
            conn.execute(
                """
                INSERT INTO creative_prompt_market_events (
                    id, operation_id, request_hash, submission_id, action,
                    from_status, to_status, note, actor_user_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    str(operation_id),
                    request_hash,
                    str(submission_id),
                    action,
                    from_status,
                    to_status,
                    normalized_note,
                    actor_user_id,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions WHERE id = ?",
                (str(submission_id),),
            ).fetchone()
        return self._submission_row(
            row, actor_user_id=actor_user_id, include_private=True
        )

    def install(
        self,
        *,
        project_id: int,
        submission_id: str,
        operation_id: str,
        actor_user_id: str,
    ) -> dict[str, Any]:
        self._project(project_id)
        request_payload = {
            "project_id": int(project_id),
            "submission_id": str(submission_id),
        }
        request_hash = content_hash(request_payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing_operation = conn.execute(
                "SELECT * FROM creative_prompt_market_installations "
                "WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
            if existing_operation is not None:
                if str(existing_operation["request_hash"]) != request_hash:
                    raise ValueError("模板安装操作标识已用于其他请求")
                return self._installed_result(conn, existing_operation)
            existing_installation = conn.execute(
                "SELECT * FROM creative_prompt_market_installations "
                "WHERE project_id = ? AND submission_id = ?",
                (int(project_id), str(submission_id)),
            ).fetchone()
            if existing_installation is not None:
                return self._installed_result(conn, existing_installation)
            listing = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions WHERE id = ?",
                (str(submission_id),),
            ).fetchone()
            if listing is None or str(listing["status"]) != "approved":
                raise ValueError("模板未上架或已停止安装")
            compatibility = json.loads(listing["compatibility_json"] or "{}")
            if compatibility.get("schema_version") != PROMPT_LIBRARY_SCHEMA_VERSION:
                raise ValueError("模板与当前提示词运行契约不兼容")
            normalized_content = normalize_template_content(
                json.loads(listing["content_json"] or "{}")
            )
            source_hash = content_hash(normalized_content)
            if source_hash != str(listing["template_content_hash"]):
                raise ValueError("市场模板冻结内容哈希校验失败")
            now = time.time()
            template_id = uuid.uuid4().hex
            template_key = f"project-{template_id}"
            description = str(listing["description"] or "").strip()
            provenance = (
                f"模板中心安装 · {listing['attribution_name']} · "
                f"来源版本 v{listing['template_revision']}"
            )
            installed_description = (
                f"{description}\n{provenance}" if description else provenance
            )
            conn.execute(
                """
                INSERT INTO creative_prompt_templates (
                    id, template_key, title, description, owner_scope,
                    owner_user_id, project_id, visibility, status,
                    governance_status, approved_revision, catalog_visible,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'project', ?, ?, 'project', 'active',
                          'approved', 1, TRUE, ?, ?)
                """,
                (
                    template_id,
                    template_key,
                    str(listing["title"]),
                    installed_description,
                    actor_user_id,
                    int(project_id),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO creative_prompt_template_revisions (
                    template_id, revision, content_json, content_hash,
                    created_by, created_at
                ) VALUES (?, 1, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    json.dumps(normalized_content, ensure_ascii=False),
                    source_hash,
                    actor_user_id,
                    now,
                ),
            )
            installation_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO creative_prompt_market_installations (
                    id, operation_id, request_hash, submission_id, project_id,
                    installed_template_id, installed_template_revision,
                    source_content_hash, license_code, attribution_name,
                    installed_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    installation_id,
                    str(operation_id),
                    request_hash,
                    str(submission_id),
                    int(project_id),
                    template_id,
                    source_hash,
                    str(listing["license_code"]),
                    str(listing["attribution_name"]),
                    actor_user_id,
                    now,
                ),
            )
            installation = conn.execute(
                "SELECT * FROM creative_prompt_market_installations WHERE id = ?",
                (installation_id,),
            ).fetchone()
            return self._installed_result(conn, installation)

    def _installed_result(
        self, conn: Any, installation: Any
    ) -> dict[str, Any]:
        template = conn.execute(
            "SELECT * FROM creative_prompt_templates WHERE id = ?",
            (str(installation["installed_template_id"]),),
        ).fetchone()
        revision = conn.execute(
            "SELECT * FROM creative_prompt_template_revisions "
            "WHERE template_id = ? AND revision = ?",
            (
                str(installation["installed_template_id"]),
                int(installation["installed_template_revision"]),
            ),
        ).fetchone()
        template_result = dict(template) if template is not None else {}
        if revision is not None:
            template_result["revision"] = int(revision["revision"])
            template_result["content"] = json.loads(revision["content_json"] or "{}")
            template_result["content_hash"] = str(revision["content_hash"])
        return {
            "installation": self._installation_row(installation),
            "template": template_result,
            "bound": False,
        }

    def rate(
        self,
        *,
        project_id: int,
        submission_id: str,
        score: int,
        review_text: str,
        expected_revision: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        self._project(project_id)
        if int(score) < 1 or int(score) > 5:
            raise ValueError("市场评分必须是 1 到 5 分")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            listing = conn.execute(
                "SELECT * FROM creative_prompt_market_submissions WHERE id = ?",
                (str(submission_id),),
            ).fetchone()
            if listing is None or str(listing["status"]) != "approved":
                raise ValueError("只能评价当前已上架的模板")
            if str(listing["publisher_user_id"]) == actor_user_id:
                raise PermissionError("发布者不能评价自己的模板")
            installation = conn.execute(
                "SELECT id FROM creative_prompt_market_installations "
                "WHERE project_id = ? AND submission_id = ?",
                (int(project_id), str(submission_id)),
            ).fetchone()
            if installation is None:
                raise PermissionError("只有已安装该模板的项目成员才能评分")
            existing = conn.execute(
                "SELECT * FROM creative_prompt_market_ratings "
                "WHERE submission_id = ? AND actor_user_id = ?",
                (str(submission_id), actor_user_id),
            ).fetchone()
            now = time.time()
            if existing is None:
                if int(expected_revision) != 0:
                    raise ValueError("评分版本已变化，请刷新后重试")
                rating_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO creative_prompt_market_ratings (
                        id, submission_id, project_id, actor_user_id, score,
                        review_text, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        rating_id,
                        str(submission_id),
                        int(project_id),
                        actor_user_id,
                        int(score),
                        str(review_text or "").strip(),
                        now,
                        now,
                    ),
                )
            else:
                if int(existing["revision"]) != int(expected_revision):
                    raise ValueError("评分版本已变化，请刷新后重试")
                rating_id = str(existing["id"])
                conn.execute(
                    """
                    UPDATE creative_prompt_market_ratings
                    SET project_id = ?, score = ?, review_text = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        int(project_id),
                        int(score),
                        str(review_text or "").strip(),
                        now,
                        rating_id,
                        int(expected_revision),
                    ),
                )
            rating = conn.execute(
                "SELECT * FROM creative_prompt_market_ratings WHERE id = ?",
                (rating_id,),
            ).fetchone()
            summary = self._rating_summary(conn, str(submission_id))
        return {"rating": dict(rating), "summary": summary}
