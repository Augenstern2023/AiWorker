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

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
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
    filter_project_authority_output,
    parse_creative_cards,
    parse_proposed_actions,
    persist_proposed_actions,
    requires_direction_card,
    validate_direction_requirement,
    match_natural_language_decision,
)
from .schema import ActionCommand, ProposedAction, WriterActionError
from .project_review import (
    ProjectReviewCoordinator,
    ProjectReviewError,
    long_project_review_scope,
)
from .rollback_service import RollbackService
from .service import build_writer_report_markdown, is_review_request, normalize_writer_report, writer_messages
from .store import WriterAgentStore


_TEXT_UPLOAD_EXTENSIONS = frozenset({
    ".txt", ".text", ".md", ".markdown", ".mdown", ".fountain", ".fd", ".rst",
})
_DOCUMENT_UPLOAD_EXTENSIONS = frozenset({".docx", ".pdf"})
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_SUPPLEMENT_MARKER = re.compile(r"\n\n\[补充材料：(?P<name>[^\]]+)\]\n")
_MESSAGE_CLAIM_LEASE_SECONDS = 30.0
_MESSAGE_CLAIM_HEARTBEAT_SECONDS = 10.0
_SESSION_MUTATION_LEASE_SECONDS = 120.0


def _writer_message_timeout_seconds() -> float:
    try:
        return max(
            0.01,
            float(
                getattr(
                    config, "WRITER_AGENT_MESSAGE_TIMEOUT_SECONDS", 210
                )
            ),
        )
    except (TypeError, ValueError):
        return 210.0


def _start_message_claim_heartbeat(
    store: WriterAgentStore,
    session_id: int,
    user_id: str,
    message_id: str,
    claim_token: str,
    mutation_token: str | None = None,
) -> threading.Event:
    """Renew a provider turn independently of the request event loop."""

    interval = max(float(_MESSAGE_CLAIM_HEARTBEAT_SECONDS), 0.01)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(interval):
            try:
                renewed = store.renew_client_message_claim(
                    session_id,
                    user_id,
                    message_id,
                    claim_token=claim_token,
                )
                if mutation_token is not None:
                    renewed = renewed and store.renew_session_mutation(
                        session_id,
                        owner_token=mutation_token,
                        lease_seconds=_SESSION_MUTATION_LEASE_SECONDS,
                    )
            except (OSError, sqlite3.Error):
                return
            if not renewed:
                return

    threading.Thread(
        target=heartbeat,
        name=f"writer-message-claim-{session_id}-{message_id}",
        daemon=True,
    ).start()
    request_task = asyncio.current_task()
    if request_task is not None:
        request_task.add_done_callback(lambda _task: stop.set())
    return stop


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


class TurnRollbackRequest(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)
    expected_history_revision: int = Field(ge=0)


class ProjectReviewPreviewRequest(BaseModel):
    audit_scope: Literal["outline", "script", "project"]


class ProjectReviewCreateRequest(ProjectReviewPreviewRequest):
    expected_fingerprint: str = Field(min_length=1)
    client_request_id: str = Field(min_length=1, max_length=200)
    prompt_template_key: str | None = Field(None, min_length=1, max_length=120)
    prompt_template_revision: int | None = Field(None, ge=1)
    prompt_override: dict[str, Any] = Field(default_factory=dict)


def create_provider() -> Any:
    return create_llm_provider_from_config(config, prefix="WRITER_AGENT_LLM")


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
    job_manager: Any | None = None,
) -> APIRouter:
    store = store or WriterAgentStore(
        Path(runtime_dir) / "writer_agent" / "writer_agent.db"
    )
    authority_repository = creative_repository or getattr(
        creative_application_service, "repository", None
    )
    resolved_action_service = action_service
    if resolved_action_service is None and runner is not None:
        resolved_action_service = ActionService(
            store=store,
            runner=runner,
            audit_service=audit_service,
            permission_service=permission_service,
            workflow_repository=workflow_repository,
            creative_repository=authority_repository,
        )
    router = APIRouter(prefix="/api/writer-agent", tags=["writer-agent"])
    router.creative_dependencies = SimpleNamespace(
        repository=creative_repository,
        application_service=creative_application_service,
    )
    project_review_coordinator = (
        ProjectReviewCoordinator(
            repository=authority_repository,
            store=store,
            permission_service=permission_service,
            job_manager=job_manager,
            runtime_dir=runtime_dir,
        )
        if authority_repository is not None
        else None
    )
    rollback_service = RollbackService(
        store,
        authority_repository,
        workflow_repository,
        permission_service=permission_service,
        action_runner=runner,
    )
    if runner is not None and hasattr(runner, "rollback_service"):
        runner.rollback_service = rollback_service
        orchestrator = getattr(runner, "orchestrator", None)
        if orchestrator is not None and hasattr(orchestrator, "new_project_observer"):
            orchestrator.new_project_observer = (
                rollback_service.capture_new_project_ownership
            )

    def assert_access(principal: Principal) -> None:
        if not principal.is_system_admin and "script.review.generate" not in principal.permissions:
            raise HTTPException(403, "没有使用编剧大师的权限")

    def workflow_project_id(project_id: int | str) -> str:
        scope = getattr(authority_repository, "workflow_project_id", None)
        return scope(project_id) if callable(scope) else str(project_id)

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
            "TURN_CHECKPOINT_NOT_FOUND": 404,
            "TURN_ROLLBACK_UNSUPPORTED": 409,
            "HISTORY_REVISION_MISMATCH": 409,
            "ROLLBACK_BUSY": 409,
            "ROLLBACK_CONFLICT": 409,
            "ROLLBACK_PERMISSION_DENIED": 403,
            "ROLLBACK_PARTIAL_FAILURE": 500,
            "ROLLBACK_STATE_CONFLICT": 409,
            "SESSION_MUTATION_BUSY": 409,
            "SESSION_MUTATION_LEASE_LOST": 409,
            "TURN_CHECKPOINT_INVALID": 409,
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

    async def session_mutation_lease(
        session_id: int,
        request: Request,
        principal: Principal = Depends(current_principal),
    ):
        """Serialize every side-effecting request for one Writer session."""

        assert_access(principal)
        path = request.url.path.rstrip("/")
        mutation_kind = (
            "rollback"
            if path.endswith("/rollback")
            else "resend"
            if path.endswith("/resend")
            else "message"
            if path.endswith("/messages")
            else "material"
            if path.endswith("/materials/file")
            else "project_review"
            if "/project-reviews" in path
            else "action"
        )
        owner_token = f"{mutation_kind}-{uuid4().hex}"
        leased_session = store.get(session_id, session_user_id(principal))
        if leased_session is None:
            raise HTTPException(404, "创作会话不存在")
        lease_user_id = str(leased_session["user_id"])
        deadline = time.monotonic() + _writer_message_timeout_seconds()
        while True:
            try:
                store.acquire_session_mutation(
                    session_id,
                    lease_user_id,
                    owner_token=owner_token,
                    mutation_kind=mutation_kind,
                    lease_seconds=_SESSION_MUTATION_LEASE_SECONDS,
                )
                break
            except WriterActionError as exc:
                if (
                    exc.code != "SESSION_MUTATION_BUSY"
                    or mutation_kind != "message"
                    or time.monotonic() >= deadline
                ):
                    raise action_error(exc) from exc
                await asyncio.sleep(0.05)
        heartbeat_stop, heartbeat_thread = store.start_session_mutation_heartbeat(
            session_id,
            owner_token=owner_token,
            lease_seconds=_SESSION_MUTATION_LEASE_SECONDS,
        )
        try:
            yield owner_token
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
            store.release_session_mutation(
                session_id, owner_token=owner_token
            )

    def require_action_service() -> ActionService:
        if resolved_action_service is None:
            raise HTTPException(503, "创作动作执行服务不可用")
        return resolved_action_service

    def require_project_review_coordinator() -> ProjectReviewCoordinator:
        if project_review_coordinator is None:
            raise HTTPException(503, "完整项目审核服务不可用")
        return project_review_coordinator

    def project_review_error(exc: ProjectReviewError) -> HTTPException:
        detail: Any = str(exc)
        if exc.extra is not None:
            detail = {"message": str(exc), **exc.extra}
        return HTTPException(exc.status_code, detail)

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
        mutation_token: str = Depends(session_mutation_lease),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            name, content = _decode_upload(
                request.headers.get("X-Writer-Filename", ""), await request.body()
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        session = store.append_material(
            session_id,
            owned_user_id(principal),
            content,
            name,
            mutation_token=mutation_token,
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
    async def chat(
        session_id: int,
        payload: ChatRequest,
        principal: Principal = Depends(current_principal),
        mutation_token: str = Depends(session_mutation_lease),
    ) -> dict[str, Any]:
        assert_access(principal)
        session = store.get(session_id, owned_user_id(principal))
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
        turn_hash = hashlib.sha256(
            json.dumps(
                payload.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        checkpoint: dict[str, Any] | None = None

        def ensure_checkpoint() -> dict[str, Any]:
            nonlocal checkpoint
            if checkpoint is None:
                try:
                    checkpoint = store.begin_turn(
                        session_id,
                        principal.user_id,
                        message_id,
                        original_message=payload.model_dump(mode="json"),
                        content_hash=turn_hash,
                        mutation_token=mutation_token,
                    )
                except WriterActionError as exc:
                    raise action_error(exc) from exc
            return checkpoint

        def save_completed_turn(
            messages: list[dict[str, Any]],
            report: dict[str, Any] | None,
            *,
            claim_token: str | None = None,
        ) -> dict[str, Any]:
            turn = ensure_checkpoint()
            saved = store.save_turn(
                session_id,
                owned_user_id(principal),
                messages,
                report,
                client_message_id=message_id,
                claim_token=claim_token,
                turn_checkpoint_id=turn["id"],
                mutation_token=mutation_token,
            )
            return store.get(session_id, owned_user_id(principal)) or saved
        if payload.link_project_id is not None:
            project_id = int(payload.link_project_id)
            try:
                project = authority_repository.get_project(project_id)
            except Exception as exc:
                raise HTTPException(404, "可关联项目不存在") from exc
            if permission_service is not None and not permission_service.can_read_project(
                principal, workflow_project_id(project_id)
            ):
                raise HTTPException(403, "无权关联该项目")
            materials = _session_materials(session)
            expected_context = {
                "session_revision": int(session.get("context_revision") or 0)
            }
            turn = ensure_checkpoint()
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
                client_message_id=message_id,
                turn_checkpoint_id=turn["id"],
                mutation_token=mutation_token,
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
            return save_completed_turn(history, None)
        long_review_scope = long_project_review_scope(message)
        if long_review_scope is not None:
            linked_project_id = session.get("project_id")
            if linked_project_id is None:
                raise HTTPException(409, "请先关联创作项目后再发起完整项目审核")
            if permission_service is None:
                raise HTTPException(503, "项目权限服务不可用")
            if not permission_service.can_read_project(
                principal, workflow_project_id(linked_project_id)
            ):
                raise HTTPException(403, "当前账号已无权读取关联项目")
            review_turn = ensure_checkpoint()
            history.extend([
                {
                    "role": "user", "content": message,
                    "message_id": message_id,
                    "display_user_message": payload.display_user_message,
                },
                {
                    "role": "assistant", "kind": "project_review_offer",
                    "audit_scope": long_review_scope,
                    "turn_checkpoint_id": review_turn["id"],
                    "content": "已识别为完整项目审核，请先预检并确认当前项目快照。",
                },
            ])
            return save_completed_turn(history, None)
        try:
            natural_decision = match_natural_language_decision(
                message, store.list_session_actions(session_id)
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc
        if natural_decision.action_id and not natural_decision.revision_required:
            turn = ensure_checkpoint()
            action = store.require_action(
                natural_decision.action_id, owned_user_id(principal)
            )
            action = store.assign_action_turn(action["id"], turn["id"])
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
            return save_completed_turn(history, None)
        materials = _session_materials(session)
        current_material = materials[-1]
        review_requested = is_review_request(message)
        material_catalog = [{"name": item["name"], "length": len(item["content"])} for item in materials]
        reference_context, reference_refs = resolve_references(
            payload.reference_refs, principal
        )
        linked_project_id = session.get("project_id")
        if linked_project_id is not None:
            if permission_service is None:
                raise HTTPException(503, "项目权限服务不可用")
            if not permission_service.can_read_project(
                principal, workflow_project_id(linked_project_id)
            ):
                raise HTTPException(403, "当前账号已无权读取关联项目")
        try:
            project_context = build_project_authority_context(
                authority_repository, linked_project_id
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
        claim_hash = turn_hash
        claim_token = uuid4().hex
        try:
            claim_state = store.claim_client_message(
                session_id,
                principal.user_id,
                message_id,
                claim_hash,
                claim_token=claim_token,
                lease_seconds=_MESSAGE_CLAIM_LEASE_SECONDS,
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc
        if claim_state == "completed":
            return store.get(session_id, owned_user_id(principal)) or session
        if claim_state == "in_progress":
            for _attempt in range(500):
                await asyncio.sleep(0.01)
                state = store.client_message_claim_status(
                    session_id, principal.user_id, message_id
                )
                if state == "completed":
                    return (
                        store.get(session_id, owned_user_id(principal))
                        or session
                    )
                if state is None:
                    raise HTTPException(
                        409, "消息处理已中断，请使用相同消息 ID 重试"
                    )
            raise HTTPException(409, "相同消息仍在处理中")
        if not store.renew_client_message_claim(
            session_id,
            principal.user_id,
            message_id,
            claim_token=claim_token,
        ):
            raise HTTPException(409, "消息处理租约已由其他请求接管")
        turn = ensure_checkpoint()
        heartbeat_stop = _start_message_claim_heartbeat(
            store,
            session_id,
            principal.user_id,
            message_id,
            claim_token,
            mutation_token,
        )
        try:
            raw = await asyncio.wait_for(
                create_provider().complete_json(writer_messages(
                    current_material["content"], history, message,
                    review_requested=review_requested,
                    material_name=current_material["name"],
                    material_catalog=material_catalog,
                    materials=materials,
                    project_context=project_context,
                    reference_context=reference_context,
                    direction_required=direction_required,
                )),
                timeout=_writer_message_timeout_seconds(),
            )
        except asyncio.TimeoutError as exc:
            heartbeat_stop.set()
            store.abandon_client_message(
                session_id,
                principal.user_id,
                message_id,
                claim_token=claim_token,
            )
            raise HTTPException(
                504, "编剧大师响应超时，请直接重试本条消息"
            ) from exc
        except Exception as exc:
            heartbeat_stop.set()
            store.abandon_client_message(
                session_id,
                principal.user_id,
                message_id,
                claim_token=claim_token,
            )
            raise HTTPException(503, f"编剧大师暂时无法响应：{exc}") from exc
        if not store.renew_client_message_claim(
            session_id,
            principal.user_id,
            message_id,
            claim_token=claim_token,
        ):
            heartbeat_stop.set()
            raise HTTPException(409, "消息处理租约已由其他请求接管")
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
                card_models, proposed_actions = filter_project_authority_output(
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
            if not store.renew_client_message_claim(
                session_id,
                principal.user_id,
                message_id,
                claim_token=claim_token,
            ):
                heartbeat_stop.set()
                raise HTTPException(409, "消息处理租约已由其他请求接管")
            try:
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
                    client_message_id=message_id,
                    turn_checkpoint_id=turn["id"],
                    mutation_token=mutation_token,
                )
            except Exception:
                heartbeat_stop.set()
                store.abandon_client_message(
                    session_id,
                    principal.user_id,
                    message_id,
                    claim_token=claim_token,
                )
                raise
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
        if not store.renew_client_message_claim(
            session_id,
            principal.user_id,
            message_id,
            claim_token=claim_token,
        ):
            heartbeat_stop.set()
            raise HTTPException(409, "消息处理租约已由其他请求接管")
        try:
            saved = save_completed_turn(
                history, report, claim_token=claim_token
            )
        except Exception:
            heartbeat_stop.set()
            store.abandon_client_message(
                session_id,
                principal.user_id,
                message_id,
                claim_token=claim_token,
            )
            raise
        heartbeat_stop.set()
        return saved

    @router.get(
        "/sessions/{session_id}/messages/{message_id}/rollback-preview"
    )
    async def preview_turn_rollback(
        session_id: int,
        message_id: str,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return rollback_service.preview(
                session_id,
                message_id,
                actor=principal,
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc

    @router.post(
        "/sessions/{session_id}/messages/{message_id}/rollback"
    )
    async def rollback_turn(
        session_id: int,
        message_id: str,
        payload: TurnRollbackRequest,
        principal: Principal = Depends(current_principal),
        mutation_token: str = Depends(session_mutation_lease),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return rollback_service.rollback(
                session_id,
                message_id,
                actor=principal,
                mode="rollback",
                client_request_id=payload.client_request_id,
                expected_history_revision=payload.expected_history_revision,
                mutation_token=mutation_token,
            )
        except WriterActionError as exc:
            raise action_error(exc) from exc

    @router.post("/sessions/{session_id}/messages/{message_id}/resend")
    async def resend_turn(
        session_id: int,
        message_id: str,
        payload: TurnRollbackRequest,
        principal: Principal = Depends(current_principal),
        mutation_token: str = Depends(session_mutation_lease),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            checkpoint = store.require_turn(
                session_id, message_id, owned_user_id(principal)
            )
            rollback_service.rollback(
                session_id,
                message_id,
                actor=principal,
                mode="resend",
                client_request_id=payload.client_request_id,
                expected_history_revision=payload.expected_history_revision,
                mutation_token=mutation_token,
            )
            original = dict(checkpoint.get("original_message") or {})
            candidate_message_id = "message-resend-" + hashlib.sha256(
                f"{session_id}:{payload.client_request_id}".encode("utf-8")
            ).hexdigest()[:32]
            rollback = store.record_resend_message_id(
                owned_user_id(principal),
                payload.client_request_id,
                candidate_message_id,
                mutation_token=mutation_token,
            )
            new_message_id = str(
                (rollback.get("result") or {}).get("new_message_id")
                or candidate_message_id
            )
            original.update(
                {
                    "message_id": new_message_id,
                    "client_message_id": None,
                }
            )
            resend_payload = ChatRequest.model_validate(original)
        except (ValueError, WriterActionError) as exc:
            if isinstance(exc, WriterActionError):
                raise action_error(exc) from exc
            raise HTTPException(422, "原始消息无法重新发送") from exc
        return await chat(session_id, resend_payload, principal, mutation_token)

    @router.post("/sessions/{session_id}/project-reviews/preview")
    async def preview_project_review(
        session_id: int,
        payload: ProjectReviewPreviewRequest,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return require_project_review_coordinator().preview(
                session_id, principal, payload.audit_scope
            )
        except ProjectReviewError as exc:
            raise project_review_error(exc) from exc

    @router.post("/sessions/{session_id}/project-reviews", status_code=201)
    async def create_project_review(
        session_id: int,
        payload: ProjectReviewCreateRequest,
        principal: Principal = Depends(current_principal),
        mutation_token: str = Depends(session_mutation_lease),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return require_project_review_coordinator().create(
                session_id, principal, payload.audit_scope,
                payload.expected_fingerprint, payload.client_request_id,
                mutation_token=mutation_token,
                prompt_template_key=payload.prompt_template_key,
                prompt_template_revision=payload.prompt_template_revision,
                prompt_override=payload.prompt_override,
            )
        except ProjectReviewError as exc:
            raise project_review_error(exc) from exc

    @router.get("/sessions/{session_id}/project-reviews/{review_id}")
    async def get_project_review(
        session_id: int,
        review_id: int,
        principal: Principal = Depends(current_principal),
    ) -> dict[str, Any]:
        if not principal.is_system_admin and "script.review.read" not in principal.permissions:
            raise HTTPException(403, "没有执行此操作的权限")
        try:
            return require_project_review_coordinator().get(
                session_id, principal, review_id
            )
        except ProjectReviewError as exc:
            raise project_review_error(exc) from exc

    @router.post("/sessions/{session_id}/project-reviews/{review_id}/resume")
    async def resume_project_review(
        session_id: int,
        review_id: int,
        principal: Principal = Depends(current_principal),
        mutation_token: str = Depends(session_mutation_lease),
    ) -> dict[str, Any]:
        assert_access(principal)
        try:
            return require_project_review_coordinator().resume(
                session_id, principal, review_id, mutation_token=mutation_token
            )
        except ProjectReviewError as exc:
            raise project_review_error(exc) from exc

    @router.get("/sessions/{session_id}/project-reviews/{review_id}/export")
    async def export_project_review(
        session_id: int,
        review_id: int,
        principal: Principal = Depends(current_principal),
    ) -> Response:
        if not principal.is_system_admin and "script.review.read" not in principal.permissions:
            raise HTTPException(403, "没有执行此操作的权限")
        try:
            review = require_project_review_coordinator().get(
                session_id, principal, review_id
            )
        except ProjectReviewError as exc:
            raise project_review_error(exc) from exc
        if review.get("status") != "completed":
            raise HTTPException(409, "完整项目审核尚未完成")
        from project_review.service import build_project_review_markdown
        return Response(
            content=build_project_review_markdown(dict(review.get("report") or {})),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="project-review-{review_id}.md"'
                )
            },
        )

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
        _mutation_token: str = Depends(session_mutation_lease),
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
        _mutation_token: str = Depends(session_mutation_lease),
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
    job_manager: Any | None = None,
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
            job_manager=job_manager,
        )
    )
