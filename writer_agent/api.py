# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/writer_agent\api.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from __future__ import annotations

import json
import re
import sqlite3
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import unquote
from uuid import uuid4

import config
from analysis.llm import create_llm_provider_from_config
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from creative.application_service import CreativeApplicationService
from creative.market_reference_service import MarketReferenceService
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal
from pydantic import BaseModel, ConfigDict, Field

from .action_service import (
    ActionService,
    build_project_authority_context,
    parse_creative_cards,
    parse_proposed_actions,
    persist_proposed_actions,
    requires_direction_card,
    validate_direction_requirement,
    validate_project_authority,
    match_natural_language_decision,
)
from .schema import ActionCommand, ProposedAction, WriterActionError
from .service import build_writer_report_markdown, is_review_request, normalize_writer_report, writer_messages
from .store import WriterAgentStore


_TEXT_UPLOAD_EXTENSIONS = frozenset({
    ".txt", ".text", ".md", ".markdown", ".mdown", ".fountain", ".fd", ".rst",
})
_DOCUMENT_UPLOAD_EXTENSIONS = frozenset({".docx", ".pdf"})
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_SUPPLEMENT_MARKER = re.compile(r"\n\n\[补充材料：(?P<name>[^\]]+)\]\n")


class SessionCreateRequest(BaseModel):
    content: str
    title: str = ""


class ReferenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: Literal["topic", "breakdown"]
    resource_id: int = Field(ge=1)


class ChatRequest(BaseModel):
    message: str
    display_user_message: bool = True
    reference_refs: list[ReferenceSelection] = Field(default_factory=list, max_length=10)
    message_id: str | None = Field(default=None, min_length=1, max_length=200)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=200)
    link_project_id: int | None = Field(default=None, ge=1)


class ActionDecisionRequest(BaseModel):
    decision: Literal["confirm", "cancel"]
    expected_revision: int = Field(ge=1)
    client_request_id: str = Field(min_length=1, max_length=200)


class ActionRetryRequest(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)


def create_provider() -> Any:
    return create_llm_provider_from_config(config)


def _decode_upload(filename: str, content: bytes) -> tuple[str, str]:
    name = Path(unquote(filename or "")).name
    suffix = Path(name).suffix.lower()
    if suffix not in _TEXT_UPLOAD_EXTENSIONS | _DOCUMENT_UPLOAD_EXTENSIONS:
        raise ValueError("仅支持 TXT、Markdown、Fountain、RST、Word DOCX 或 PDF 文件")
    if not content or len(content) > _MAX_UPLOAD_BYTES:
        raise ValueError("上传文件不能为空且不能超过 20 MB")
    if suffix == ".docx":
        return name, _decode_docx(content)
    if suffix == ".pdf":
        return name, _decode_pdf(content)
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding).strip()
            if text:
                return name, text
        except UnicodeDecodeError:
            pass
    raise ValueError("无法识别文件编码，请使用 UTF-8 或 GB18030")


def _decode_docx(content: bytes) -> str:
    try:
        from docx import Document

        document = Document(BytesIO(content))
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        table_cells = [
            cell.text.strip()
            for table in document.tables
            for row in table.rows
            for cell in row.cells
        ]
    except Exception as exc:
        raise ValueError("无法读取 Word DOCX 文件，请确认文件未损坏") from exc
    text = "\n".join(item for item in [*paragraphs, *table_cells] if item)
    if not text:
        raise ValueError("Word DOCX 文件中未提取到可评审文本")
    return text


def _decode_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先移除密码后上传")
        text = "\n".join((page.extract_text() or "").strip() for page in reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法读取 PDF 文件，请确认文件未损坏") from exc
    if not text.strip():
        raise ValueError("PDF 中未提取到文本；扫描件请先进行 OCR 后上传")
    return text.strip()


def _material_message(name: str, content: str) -> dict[str, Any]:
    return {
        "role": "user",
        "kind": "material",
        "content": f"已提交创作材料：{name}",
        "material_name": name,
        "material_preview": content[:1200],
        "material_length": len(content),
    }


def _session_materials(session: dict[str, Any]) -> list[dict[str, Any]]:
    content = str(session.get("content") or "")
    first_name = str(session.get("filename") or session.get("title") or "已粘贴创作材料")
    matches = list(_SUPPLEMENT_MARKER.finditer(content))
    materials: list[dict[str, Any]] = []
    initial_content = content[:matches[0].start()] if matches else content
    if initial_content.strip():
        materials.append({"name": first_name, "content": initial_content.strip()})
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        material_content = content[match.end():end].strip()
        if material_content:
            materials.append({"name": match.group("name").strip(), "content": material_content})
    if len(materials) > 1 and str(materials[0].get("content") or "").strip() == "继续创作":
        return materials[1:]
    return materials or [{"name": first_name, "content": content}]


def build_writer_agent_router(
    runtime_dir: str | Path = ".runtime",
    *,
    creative_repository: CreativeRepository | None = None,
    creative_application_service: CreativeApplicationService | None = None,
    store: WriterAgentStore | None = None,
    action_service: ActionService | None = None,
    runner: Any | None = None,
    audit_service: Any | None = None,
    permission_service: Any | None = None,
    workflow_repository: Any | None = None,
) -> APIRouter:
    store = store or WriterAgentStore(
        Path(runtime_dir) / "writer_agent" / "writer_agent.db"
    )
    resolved_action_service = action_service
    if resolved_action_service is None and runner is not None:
        resolved_action_service = ActionService(
            store=store,
            runner=runner,
            audit_service=audit_service,
            permission_service=permission_service,
            workflow_repository=workflow_repository,
        )
    authority_repository = creative_repository or getattr(
        creative_application_service, "repository", None
    )
    router = APIRouter(prefix="/api/writer-agent", tags=["writer-agent"])
    router.creative_dependencies = SimpleNamespace(
        repository=creative_repository,
        application_service=creative_application_service,
    )

    def assert_access(principal: Principal) -> None:
        if not principal.is_system_admin and "script.review.generate" not in principal.permissions:
            raise HTTPException(403, "没有使用编剧大师的权限")

    def session_user_id(principal: Principal) -> str | None:
        return None if principal.is_system_admin else principal.user_id

    def owned_user_id(principal: Principal) -> str:
        # Action mutation is private even when an administrator may read sessions.
        return principal.user_id

    def action_error(exc: WriterActionError) -> HTTPException:
        status = {
            "PROJECT_ACCESS_DENIED": 403,
            "PROJECT_CREATE_DENIED": 403,
            "PROJECT_NOT_AVAILABLE": 404,
            "ACTION_NOT_PENDING": 409,
            "ACTION_REVISION_MISMATCH": 409,
            "ACTION_STATE_INVALID": 409,
            "ACTIVE_ACTION_CONFLICT": 409,
            "IDEMPOTENCY_KEY_REUSED": 409,
            "RESULT_VALIDATION_FAILED": 422,
        }.get(exc.code, 409)
        return HTTPException(
            status,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "current_revision": exc.current_revision,
                "conflicting_action_id": exc.conflicting_action_id,
            },
        )

    def require_action_service() -> ActionService:
        if resolved_action_service is None:
            raise HTTPException(503, "创作动作执行服务不可用")
        return resolved_action_service

    def can_read_reference(
        principal: Principal, resource_type: str, resource_id: int
    ) -> bool:
        del resource_id
        if principal.is_system_admin:
            return True
        if resource_type == "topic":
            return bool(
                {"topic.read", "breakdown.read.approved", "brief.read.approved"}
                & principal.permissions
            )
        return bool(
            {"breakdown.read", "breakdown.read.approved"}
            & principal.permissions
        )

    def resolve_references(
        selected: list[ReferenceSelection], principal: Principal
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not selected:
            return [], []
        if authority_repository is None:
            raise HTTPException(409, "可选参考服务不可用")
        raw_refs = [item.model_dump(mode="json") for item in selected]
        try:
            bundle = MarketReferenceService(authority_repository).resolve(
                raw_refs,
                can_read=lambda resource_type, resource_id: can_read_reference(
                    principal, resource_type, resource_id
                ),
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        audited_refs: list[dict[str, Any]] = []
        for item in bundle.source_refs:
            resource_type = str(item["resource_type"])
            resource_id = int(item["resource_id"])
            resource = (
                authority_repository.get_topic(resource_id)
                if resource_type == "topic"
                else authority_repository.get_breakdown(resource_id)
            )
            assert resource is not None
            if resource_type == "topic":
                name = str(resource.get("title") or f"选题 #{resource_id}")
                version = int(resource.get("version") or 1)
            else:
                content = resource.get("content") or {}
                name = str(content.get("title") or f"拆解 #{resource_id}")
                version = int(resource.get("version") or 1)
            audited_refs.append({
                "resource_type": resource_type,
                "resource_id": resource_id,
                "name": name,
                "version": version,
                "readable": True,
            })
        return bundle.context, audited_refs

    @router.get("/sessions")
    async def list_sessions(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        assert_access(principal)
        return {"items": store.list(session_user_id(principal))}

    @router.post("/sessions")
    async def create_session(payload: SessionCreateRequest, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        assert_access(principal)
        content = payload.content.strip()
        if not content:
            raise HTTPException(400, "请输入创作材料")
        return store.create(principal.user_id, content, payload.title.strip())

    @router.post("/sessions/file")
    async def create_file_session(request: Request, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        assert_access(principal)
        try:
            name, content = _decode_upload(request.headers.get("X-Writer-Filename", ""), await request.body())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return store.create(principal.user_id, content, filename=name)

    @router.post("/sessions/{session_id}/materials/file")
    async def append_file_material(
        session_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            name, content = _decode_upload(
                request.headers.get("X-Writer-Filename", ""), await request.body()
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        session = store.append_material(
            session_id, session_user_id(principal), content, name
        )
        if not session:
            raise HTTPException(404, "创作会话不存在")
        return session

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: int, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        assert_access(principal)
        session = store.get(session_id, session_user_id(principal))
        if not session:
            raise HTTPException(404, "创作会话不存在")
        return session

    @router.post("/sessions/{session_id}/messages")
    async def chat(session_id: int, payload: ChatRequest, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        assert_access(principal)
        session = store.get(session_id, session_user_id(principal))
        if not session:
            raise HTTPException(404, "创作会话不存在")
        message = payload.message.strip()
        if not message:
            raise HTTPException(400, "请输入要讨论的问题")
        history = list(session.get("messages") or [])
        message_id = (
            payload.client_message_id
            or payload.message_id
            or f"message-{uuid4().hex}"
        )
        prior_message = next(
            (
                item
                for item in history
                if item.get("role") == "user" and item.get("message_id") == message_id
            ),
            None,
        )
        if prior_message is not None:
            if prior_message.get("content") != message:
                raise HTTPException(409, "消息 ID 已用于不同内容")
            return session
        if payload.link_project_id is not None:
            project_id = int(payload.link_project_id)
            try:
                project = authority_repository.get_project(project_id)
            except Exception as exc:
                raise HTTPException(404, "可关联项目不存在") from exc
            if permission_service is not None and not permission_service.can_read_project(
                principal, str(project_id)
            ):
                raise HTTPException(403, "无权关联该项目")
            materials = _session_materials(session)
            expected_context = {
                "session_revision": int(session.get("context_revision") or 0)
            }
            actions = persist_proposed_actions(
                store,
                session_id=session_id,
                actor_user_id=principal.user_id,
                proposed_actions=[
                    ProposedAction(
                        action_type="project.link",
                        title=f"关联项目：{project.get('name') or project_id}",
                        summary="确认后将该项目设为当前创作项目",
                        project_id=project_id,
                    )
                ],
                materials=materials,
                selected_messages=[
                    *(item for item in history if item.get("kind") != "material"),
                    {"role": "user", "content": message},
                ],
                project_context=expected_context,
            )
            history.extend(
                [
                    {
                        "role": "user",
                        "content": message,
                        "message_id": message_id,
                        "display_user_message": payload.display_user_message,
                    },
                    {
                        "role": "assistant",
                        "content": "已生成关联项目确认卡，确认后再切换当前项目。",
                        "action_ids": [action["id"] for action in actions],
                    },
                ]
            )
            return store.save_turn(
                session_id, owned_user_id(principal), history, None
            )
        try:
            natural_decision = match_natural_language_decision(
                message, store.list_session_actions(session_id)
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc
        if natural_decision.action_id and not natural_decision.revision_required:
            action = store.require_action(
                natural_decision.action_id, owned_user_id(principal)
            )
            request_id = f"message:{session_id}:{action['id']}:{message_id}"
            try:
                updated_action = require_action_service().decide(
                    ActionCommand(
                        session_id=session_id,
                        action_id=action["id"],
                        decision=natural_decision.decision,
                        actor=principal,
                        expected_revision=int(action["revision"]),
                        client_request_id=request_id,
                    )
                )
            except WriterActionError as exc:
                raise action_error(exc) from exc
            history.extend(
                [
                    {
                        "role": "user",
                        "content": message,
                        "message_id": message_id,
                        "display_user_message": payload.display_user_message,
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "已提交执行。"
                            if natural_decision.decision == "confirm"
                            else "已取消该动作。"
                        ),
                        "action_ids": [updated_action["id"]],
                    },
                ]
            )
            return store.save_turn(
                session_id, owned_user_id(principal), history, None
            )
        materials = _session_materials(session)
        current_material = materials[-1]
        review_requested = is_review_request(message)
        material_catalog = [{"name": item["name"], "length": len(item["content"])} for item in materials]
        reference_context, reference_refs = resolve_references(
            payload.reference_refs, principal
        )
        try:
            project_context = build_project_authority_context(
                authority_repository, session.get("project_id")
            )
        except WriterActionError as exc:
            raise HTTPException(409, str(exc)) from exc
        project_context = {
            **project_context,
            "session_revision": int(session.get("context_revision") or 0),
        }
        direction_required = requires_direction_card(
            materials, message, project_context
        )
        try:
            raw = await create_provider().complete_json(writer_messages(
                current_material["content"], history, message,
                review_requested=review_requested,
                material_name=current_material["name"],
                material_catalog=material_catalog,
                materials=materials,
                project_context=project_context,
                reference_context=reference_context,
                direction_required=direction_required,
            ))
        except Exception as exc:
            raise HTTPException(503, f"编剧大师暂时无法响应：{exc}") from exc
        payload_data = raw if isinstance(raw, dict) else {}
        report = normalize_writer_report(payload_data) if review_requested else None
        reply = report["reply"] if report is not None else str(payload_data.get("reply") or "我已结合当前材料与上下文整理回答。").strip()
        cards: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        if not review_requested:
            try:
                card_models = parse_creative_cards(payload_data)
                proposed_actions = parse_proposed_actions(payload_data)
                validate_direction_requirement(card_models, direction_required)
                validate_project_authority(
                    card_models, proposed_actions, project_context
                )
                cards = [card.model_dump(mode="json") for card in card_models]
            except WriterActionError:
                cards = []
                proposed_actions = []
            else:
                card_ids = {card["card_id"] for card in cards}
                proposed_actions = [
                    action for action in proposed_actions
                    if action.source_card_id is None or action.source_card_id in card_ids
                ]
                card_by_id = {card["card_id"]: card for card in cards}
                enriched_actions = []
                for action in proposed_actions:
                    source_card = card_by_id.get(action.source_card_id or "")
                    if source_card and source_card.get("card_type") == "brief":
                        confirmed_brief = source_card.get("content")
                        if isinstance(confirmed_brief, dict):
                            action_payload = dict(action.payload)
                            action_payload["confirmed_brief_payload"] = confirmed_brief
                            step_payloads = dict(
                                action_payload.get("step_payloads") or {}
                            )
                            step_payloads["brief.persist"] = confirmed_brief
                            action_payload["step_payloads"] = step_payloads
                            action = action.model_copy(
                                update={"payload": action_payload}
                            )
                    enriched_actions.append(action)
                proposed_actions = enriched_actions
            actions = persist_proposed_actions(
                store,
                session_id=session_id,
                actor_user_id=principal.user_id,
                proposed_actions=proposed_actions,
                materials=materials,
                selected_messages=[
                    *(item for item in history if item.get("kind") != "material"),
                    {"role": "user", "content": message},
                ],
                project_context=project_context,
                reference_refs=reference_refs,
            )
        history.extend([
            {
                "role": "user",
                "content": message,
                "message_id": message_id,
                "display_user_message": payload.display_user_message,
            },
            {
                "role": "assistant", "content": reply,
                **({"cards": cards} if cards else {}),
                **({"action_ids": [action["id"] for action in actions]} if actions else {}),
            },
        ])
        return store.save_turn(session_id, session_user_id(principal), history, report)

    @router.get("/sessions/{session_id}/actions/{action_id}")
    async def get_action(
        session_id: int,
        action_id: str,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_access(principal)
        if store.get(session_id, session_user_id(principal)) is None:
            raise HTTPException(404, "创作动作不存在")
        try:
            action = store.require_action(action_id, session_user_id(principal))
        except WriterActionError as exc:
            raise action_error(exc) from exc
        if int(action["session_id"]) != session_id:
            raise HTTPException(404, "创作动作不存在")
        return action

    @router.post(
        "/sessions/{session_id}/actions/{action_id}/decision", status_code=202
    )
    async def decide_action(
        session_id: int,
        action_id: str,
        payload: ActionDecisionRequest,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return require_action_service().decide(
                ActionCommand(
                    session_id=session_id,
                    action_id=action_id,
                    decision=payload.decision,
                    actor=principal,
                    expected_revision=payload.expected_revision,
                    client_request_id=payload.client_request_id,
                )
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc

    @router.post(
        "/sessions/{session_id}/actions/{action_id}/retry", status_code=202
    )
    async def retry_action(
        session_id: int,
        action_id: str,
        payload: ActionRetryRequest,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return require_action_service().retry(
                session_id=session_id,
                action_id=action_id,
                actor=principal,
                client_request_id=payload.client_request_id,
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc

    @router.get("/sessions/{session_id}/export")
    async def export_session(session_id: int, principal: Principal = Depends(current_principal)) -> Response:
        assert_access(principal)
        session = store.get(session_id, session_user_id(principal))
        if not session:
            raise HTTPException(404, "创作会话不存在")
        if not session.get("report"):
            raise HTTPException(409, "请先发起一次对话，生成评审报告后再导出")
        return Response(
            content=build_writer_report_markdown(session),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="writer-agent-report-{session_id}.md"'},
        )

    @router.get("/sessions/{session_id}/reports/{report_id}/export")
    async def export_report_version(
        session_id: int,
        report_id: str,
        principal: Principal = Depends(current_principal),
    ) -> Response:
        assert_access(principal)
        session = store.get(session_id, session_user_id(principal))
        if not session:
            raise HTTPException(404, "创作会话不存在")
        report_entry = next(
            (
                item
                for item in session.get("reports") or []
                if isinstance(item, dict) and item.get("id") == report_id
            ),
            None,
        )
        if not report_entry:
            raise HTTPException(404, "评审报告不存在")
        report_session = {**session, "report": report_entry.get("report") or {}}
        return Response(
            content=build_writer_report_markdown(report_session),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="writer-agent-{session_id}-{report_id}.md"'
                )
            },
        )

    return router


def register_writer_agent_routes(
    app: Any,
    *,
    runtime_dir: str | Path = ".runtime",
    creative_repository: CreativeRepository | None = None,
    creative_application_service: CreativeApplicationService | None = None,
    store: WriterAgentStore | None = None,
    action_service: ActionService | None = None,
    runner: Any | None = None,
    audit_service: Any | None = None,
    permission_service: Any | None = None,
    workflow_repository: Any | None = None,
) -> None:
    app.include_router(
        build_writer_agent_router(
            runtime_dir,
            creative_repository=creative_repository,
            creative_application_service=creative_application_service,
            store=store,
            action_service=action_service,
            runner=runner,
            audit_service=audit_service,
            permission_service=permission_service,
            workflow_repository=workflow_repository,
        )
    )
