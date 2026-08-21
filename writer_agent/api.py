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
from typing import Any
from urllib.parse import unquote

import config
from analysis.llm import create_llm_provider_from_config
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from creative.application_service import CreativeApplicationService
from creative.repository import CreativeRepository
from identity.dependencies import current_principal
from identity.schema import Principal
from pydantic import BaseModel

from .action_service import parse_creative_cards, parse_proposed_actions, persist_proposed_actions
from .schema import WriterActionError
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


class ChatRequest(BaseModel):
    message: str
    display_user_message: bool = True


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
    return materials or [{"name": first_name, "content": content}]


def build_writer_agent_router(
    runtime_dir: str | Path = ".runtime",
    *,
    creative_repository: CreativeRepository | None = None,
    creative_application_service: CreativeApplicationService | None = None,
) -> APIRouter:
    store = WriterAgentStore(Path(runtime_dir) / "writer_agent" / "writer_agent.db")
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
        materials = _session_materials(session)
        current_material = materials[-1]
        review_requested = is_review_request(message)
        material_catalog = [{"name": item["name"], "length": len(item["content"])} for item in materials]
        project_context = ({"project_id": session["project_id"]}
                           if session.get("project_id") is not None else {})
        try:
            raw = await create_provider().complete_json(writer_messages(
                current_material["content"], history, message,
                review_requested=review_requested,
                material_name=current_material["name"],
                material_catalog=material_catalog,
                materials=materials,
                project_context=project_context,
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
                cards = [card.model_dump(mode="json") for card in parse_creative_cards(payload_data)]
            except WriterActionError:
                cards = []
            card_ids = {card["card_id"] for card in cards}
            proposed_actions = [
                action for action in parse_proposed_actions(payload_data)
                if action.source_card_id is None or action.source_card_id in card_ids
            ]
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
            )
        history.extend([
            {
                "role": "user",
                "content": message,
                "display_user_message": payload.display_user_message,
            },
            {
                "role": "assistant", "content": reply,
                **({"cards": cards} if cards else {}),
                **({"action_ids": [action["id"] for action in actions]} if actions else {}),
            },
        ])
        return store.save_turn(session_id, session_user_id(principal), history, report)

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
) -> None:
    app.include_router(
        build_writer_agent_router(
            runtime_dir,
            creative_repository=creative_repository,
            creative_application_service=creative_application_service,
        )
    )
