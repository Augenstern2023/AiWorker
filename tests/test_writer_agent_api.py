# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import sqlite3
import time
from io import BytesIO
from urllib.parse import quote

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from identity.dependencies import current_principal
from identity.schema import Principal
from writer_agent import api as writer_agent_api


def _pdf_with_text(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 36 240 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _principal(user_id: str = "writer-1") -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=frozenset({"script.review.generate"}),
        data_scope="project",
        session_id=f"session-{user_id}",
    )


def _admin_principal() -> Principal:
    return Principal(
        user_id="admin-1",
        username="admin-1",
        display_name="系统管理员",
        role_codes=("system_admin",),
        permissions=frozenset({"*"}),
        data_scope="all",
        session_id="session-admin-1",
    )


class FakeProvider:
    async def complete_json(self, messages):
        assert messages[-1]["role"] == "user"
        return {
            "reply": "开场钩子已成立，建议压缩解释段落。",
            "scores": {"overall": 82, "structure": 80, "character": 84, "pacing": 78, "hook": 86},
            "summary": "冲突明确，但中段推进偏慢。",
            "strengths": ["主角目标明确"],
            "findings": [{"priority": "P1", "dimension": "节奏", "title": "中段解释过长", "evidence": "冲突后连续说明背景", "suggestion": "将背景信息转为对抗动作", "route": "优化第 2 场对抗"}],
            "next_questions": ["反派下一步会如何加压？"],
        }


def _client(tmp_path, user_id: str = "writer-1") -> TestClient:
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal(user_id)
    app.include_router(writer_agent_api.build_writer_agent_router(tmp_path))
    return TestClient(app)


def _admin_client(tmp_path) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(writer_agent_api.build_writer_agent_router(tmp_path))
    return TestClient(app)


def test_writer_agent_file_sessions_are_private_to_the_creator(tmp_path) -> None:
    owner_client = _client(tmp_path)
    created = owner_client.post(
        "/api/writer-agent/sessions/file",
        content="# 第一集\n主角在婚礼上被当众羞辱。".encode("utf-8"),
        headers={"X-Writer-Filename": "episode-1.md"},
    )

    assert created.status_code == 200
    assert created.json()["filename"] == "episode-1.md"
    assert created.json()["messages"][0]["kind"] == "material"

    other_client = _client(tmp_path, "writer-2")
    assert other_client.get(f"/api/writer-agent/sessions/{created.json()['id']}").status_code == 404

    admin_response = _admin_client(tmp_path).get(f"/api/writer-agent/sessions/{created.json()['id']}")
    assert admin_response.status_code == 200


def test_writer_agent_decodes_word_and_pdf_uploads() -> None:
    word = Document()
    word.add_paragraph("主角在婚礼上反击。")
    word_content = BytesIO()
    word.save(word_content)

    _, word_text = writer_agent_api._decode_upload("第一集.docx", word_content.getvalue())
    _, pdf_text = writer_agent_api._decode_upload("第一集.pdf", _pdf_with_text("SCRIPT HOOK"))

    assert "主角在婚礼上反击" in word_text
    assert "SCRIPT HOOK" in pdf_text


def test_writer_agent_persists_chat_report_and_exports_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: FakeProvider())
    client = _client(tmp_path)
    created = client.post("/api/writer-agent/sessions", json={"title": "婚礼反击", "content": "第一集：主角被未婚夫当众退婚。"})

    response = client.post(f"/api/writer-agent/sessions/{created.json()['id']}/messages", json={"message": "请评审这一集的节奏"})

    assert response.status_code == 200
    assert response.json()["report"]["scores"]["overall"] == 82
    assert response.json()["messages"][0]["kind"] == "material"
    assert len(response.json()["messages"]) == 3
    assert len(response.json()["reports"]) == 1

    exported = client.get(f"/api/writer-agent/sessions/{created.json()['id']}/export")
    assert exported.status_code == 200
    assert "# 编剧大师评审报告" in exported.text
    assert "中段解释过长" in exported.text

    second = client.post(f"/api/writer-agent/sessions/{created.json()['id']}/messages", json={"message": "继续分析反派动机"})
    report_id = second.json()["reports"][0]["id"]

    historical_export = client.get(f"/api/writer-agent/sessions/{created.json()['id']}/reports/{report_id}/export")
    assert historical_export.status_code == 200
    assert len(second.json()["reports"]) == 2

    missing_report = client.get(f"/api/writer-agent/sessions/{created.json()['id']}/reports/missing/export")
    assert missing_report.status_code == 404


def test_writer_agent_adds_uploaded_material_to_an_existing_session(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/writer-agent/sessions", json={"content": "第一集：主角被退婚。"})

    updated = client.post(
        f"/api/writer-agent/sessions/{created.json()['id']}/materials/file",
        content="反派掌握主角家族的秘密。".encode("utf-8"),
        headers={"X-Writer-Filename": quote("反派设定.md")},
    )

    assert updated.status_code == 200
    assert updated.json()["messages"][-1]["kind"] == "material"
    assert "补充材料" in updated.json()["content"]


def test_writer_agent_migrates_legacy_sessions_to_report_history(tmp_path) -> None:
    db_path = tmp_path / "writer_agent" / "writer_agent.db"
    db_path.parent.mkdir()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE writer_agent_sessions (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, filename TEXT NOT NULL DEFAULT '', messages_json TEXT NOT NULL DEFAULT '[]', report_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO writer_agent_sessions VALUES (1, 'writer-1', '旧会话', '材料', '', '[]', '{\"summary\": \"旧报告\"}', ?, ?)",
            (time.time(), time.time()),
        )

    store = writer_agent_api.WriterAgentStore(db_path)
    session = store.get(1, "writer-1")

    assert session is not None
    assert session["reports"][0]["id"] == "report-1"
