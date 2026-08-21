# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
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
from unittest.mock import MagicMock


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


def _principal(
    user_id: str = "writer-1",
    permissions: frozenset[str] = frozenset({"script.review.generate"}),
) -> Principal:
    return Principal(
        user_id=user_id,
        username=user_id,
        display_name="编剧",
        role_codes=("screenwriter",),
        permissions=permissions,
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


class CapturingProvider(FakeProvider):
    def __init__(self) -> None:
        self.messages = []

    async def complete_json(self, messages):
        self.messages = messages
        return await super().complete_json(messages)


class CreativeProvider:
    async def complete_json(self, messages):
        return {
            "reply": "可以先确认大纲，再进入分集剧本。",
            "cards": [{
                "card_id": "brief-1", "card_type": "brief",
                "content": {"title": "婚礼反击"}, "assumptions": [],
            }],
            "proposed_actions": [{
                "action_type": "creative.run_plan", "title": "生成大纲",
                "source_card_id": "brief-1", "target_stage": "outline",
                "payload": {}, "assumptions": [],
            }],
        }


class UnsafeCreativeProvider:
    async def complete_json(self, messages):
        return {
            "reply": "我不能执行删除，但可以继续讨论。",
            "proposed_actions": [{
                "action_type": "project.delete", "title": "删除项目", "payload": {},
            }],
        }


class LockedBriefOverwriteProvider:
    def __init__(self) -> None:
        self.messages = []

    async def complete_json(self, messages):
        self.messages = messages
        return {
            "reply": "已尝试替换已锁定方向。",
            "cards": [{
                "card_id": "brief-replacement",
                "card_type": "brief",
                "content": {"title": "新方向"},
                "assumptions": [],
            }],
            "proposed_actions": [{
                "action_type": "creative.run_plan",
                "title": "重新生成 Brief",
                "source_card_id": "brief-replacement",
                "target_stage": "brief",
                "payload": {},
                "assumptions": [],
            }],
        }


class WrongInspirationCardProvider:
    def __init__(self) -> None:
        self.messages = []

    async def complete_json(self, messages):
        self.messages = messages
        return {
            "reply": "这是一个单一方向。",
            "cards": [{
                "card_id": "brief-too-early",
                "card_type": "brief",
                "content": {"title": "谎言听证人"},
                "assumptions": [],
            }],
            "proposed_actions": [{
                "action_type": "project.create_original",
                "title": "创建项目",
                "source_card_id": "brief-too-early",
                "payload": {},
                "assumptions": [],
            }],
        }


def _client(
    tmp_path,
    user_id: str = "writer-1",
    *,
    creative_repository=None,
    permissions: frozenset[str] = frozenset({"script.review.generate"}),
) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal(
        user_id, permissions
    )
    app.include_router(writer_agent_api.build_writer_agent_router(
        tmp_path,
        creative_repository=creative_repository,
    ))
    return TestClient(app)


def test_writer_router_retains_injected_creative_dependencies(tmp_path) -> None:
    repository = MagicMock(name="creative_repository")
    application_service = MagicMock(name="creative_application_service")

    router = writer_agent_api.build_writer_agent_router(
        tmp_path,
        creative_repository=repository,
        creative_application_service=application_service,
    )

    assert router.creative_dependencies.repository is repository
    assert router.creative_dependencies.application_service is application_service


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
    assert len(second.json()["reports"]) == 1
    assert "report_id" not in second.json()["messages"][-1]

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


def test_writer_agent_normal_chat_uses_the_latest_material_without_creating_a_report(tmp_path, monkeypatch) -> None:
    provider = CapturingProvider()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    client = _client(tmp_path)
    created = client.post("/api/writer-agent/sessions", json={"content": "旧剧本：主角在婚礼上反击。", "title": "旧剧本"})
    session_id = created.json()["id"]
    client.post(
        f"/api/writer-agent/sessions/{session_id}/materials/file",
        content="新剧本：师叔的法宝失控，主角决定坦白。".encode("utf-8"),
        headers={"X-Writer-Filename": quote("第1-30集.txt")},
    )

    response = client.post(f"/api/writer-agent/sessions/{session_id}/messages", json={"message": "这一段转折怎样更自然？"})

    assert response.status_code == 200
    assert response.json()["reports"] == []
    assert "report_id" not in response.json()["messages"][-1]
    assert '"scores"' not in provider.messages[0]["content"]
    request_payload = json.loads(provider.messages[-1]["content"])
    assert request_payload["当前材料"]["name"] == "第1-30集.txt"
    assert request_payload["当前材料"]["content"].startswith("新剧本")
    assert [item["name"] for item in request_payload["全部材料"]] == ["旧剧本", "第1-30集.txt"]
    assert request_payload["全部材料"][0]["content"].startswith("旧剧本")


def test_writer_agent_persists_valid_cards_actions_and_source_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: CreativeProvider())
    client = _client(tmp_path)
    created = client.post(
        "/api/writer-agent/sessions", json={"title": "婚礼反击", "content": "第一集：主角被退婚。"}
    )

    response = client.post(
        f"/api/writer-agent/sessions/{created.json()['id']}/messages",
        json={"message": "生成大纲"},
    )

    assert response.status_code == 200
    assistant_message = response.json()["messages"][-1]
    assert assistant_message["cards"][0]["card_id"] == "brief-1"
    assert len(assistant_message["action_ids"]) == 1
    action = response.json()["actions"][0]
    assert action["status"] == "pending"
    assert action["payload"]["source_card_id"] == "brief-1"
    snapshot = writer_agent_api.WriterAgentStore(
        tmp_path / "writer_agent" / "writer_agent.db"
    ).require_source_snapshot(action["source_snapshot_id"])
    assert snapshot["snapshot"]["materials"][0]["content"] == "第一集：主角被退婚。"


def test_writer_agent_downgrades_unsafe_model_action_to_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: UnsafeCreativeProvider())
    client = _client(tmp_path)
    created = client.post("/api/writer-agent/sessions", json={"content": "第一集：主角被退婚。"})

    response = client.post(
        f"/api/writer-agent/sessions/{created.json()['id']}/messages",
        json={"message": "删除项目"},
    )

    assert response.status_code == 200
    assert response.json()["messages"][-1]["content"] == "我不能执行删除，但可以继续讨论。"
    assert response.json()["actions"] == []


def test_chat_uses_server_project_authority_and_blocks_locked_brief_overwrite(
    tmp_path, monkeypatch
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="一家用情绪考核员工的公司",
        name="情绪公司",
        target_episode_count=12,
        created_by="writer-1",
        creation_request_id="locked-context",
    )
    locked_brief = repository.create_blueprints(
        project["id"],
        [{"content": {"title": "情绪公司", "protagonist": "林岚"}}],
        direction_type="original",
    )[0]
    locked_brief = repository.lock_brief(locked_brief["id"])
    provider = LockedBriefOverwriteProvider()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    client = _client(tmp_path, creative_repository=repository)
    created = client.post(
        "/api/writer-agent/sessions",
        json={"content": "继续完善当前项目。"},
    ).json()
    writer_agent_api.WriterAgentStore(
        tmp_path / "writer_agent" / "writer_agent.db"
    ).bind_project(created["id"], project["id"], expected_context_revision=0)

    response = client.post(
        f"/api/writer-agent/sessions/{created['id']}/messages",
        json={"message": "重做一个方向"},
    )

    assert response.status_code == 200
    request_payload = json.loads(provider.messages[-1]["content"])
    authority = request_payload["项目权威上下文"]
    assert authority["revision"] == locked_brief["revision"]
    assert authority["locked_brief"] == {
        "id": locked_brief["id"],
        "version": locked_brief["version"],
        "content": locked_brief["content"],
    }
    assert response.json()["messages"][-1].get("cards", []) == []
    assert response.json()["actions"] == []


def test_chat_records_an_authorized_optional_reference_in_the_action_snapshot(
    tmp_path, monkeypatch
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "dy",
        "job_id": "rank-1",
        "source_path": "rank.json",
        "captured_at": "2026-08-21T08:00:00+08:00",
        "title": "职场榜单",
        "description": "职场题材热度上升",
        "category": "职场",
        "score": 88,
        "confidence": "high",
        "score_details": {},
        "recommendation": "观察市场情绪",
        "evidence": [],
    }])[0]
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: CreativeProvider())
    client = _client(
        tmp_path,
        creative_repository=repository,
        permissions=frozenset({"script.review.generate", "topic.read"}),
    )
    created = client.post(
        "/api/writer-agent/sessions",
        json={"content": "婚礼上的职场反击。"},
    ).json()

    response = client.post(
        f"/api/writer-agent/sessions/{created['id']}/messages",
        json={
            "message": "生成大纲",
            "reference_refs": [{
                "resource_type": "topic",
                "resource_id": topic["id"],
            }],
        },
    )

    assert response.status_code == 200
    action = response.json()["actions"][0]
    snapshot = writer_agent_api.WriterAgentStore(
        tmp_path / "writer_agent" / "writer_agent.db"
    ).require_source_snapshot(action["source_snapshot_id"])["snapshot"]
    assert snapshot["references"] == [{
        "resource_type": "topic",
        "resource_id": topic["id"],
        "name": "职场榜单",
        "version": 1,
        "readable": True,
    }]


def test_chat_rejects_an_optional_reference_without_read_permission(
    tmp_path, monkeypatch
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    topic = repository.upsert_topics([{
        "platform": "dy",
        "job_id": "rank-2",
        "source_path": "rank.json",
        "captured_at": "2026-08-21T08:00:00+08:00",
        "title": "无权榜单",
        "description": "",
        "category": "职场",
        "score": 80,
        "confidence": "high",
        "score_details": {},
        "recommendation": "",
        "evidence": [],
    }])[0]
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: CreativeProvider())
    client = _client(tmp_path, creative_repository=repository)
    created = client.post(
        "/api/writer-agent/sessions", json={"content": "一句创作材料。"}
    ).json()

    response = client.post(
        f"/api/writer-agent/sessions/{created['id']}/messages",
        json={
            "message": "生成大纲",
            "reference_refs": [{
                "resource_type": "topic",
                "resource_id": topic["id"],
            }],
        },
    )

    assert response.status_code == 403


def test_one_line_inspiration_requires_a_three_to_five_direction_card(
    tmp_path, monkeypatch
) -> None:
    provider = WrongInspirationCardProvider()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    client = _client(tmp_path)
    created = client.post(
        "/api/writer-agent/sessions",
        json={"content": "一个能听见谎言的女孩被迫为权贵作证。"},
    ).json()

    response = client.post(
        f"/api/writer-agent/sessions/{created['id']}/messages",
        json={"message": "请基于这句灵感给我几个创作方向"},
    )

    assert response.status_code == 200
    request_payload = json.loads(provider.messages[-1]["content"])
    assert request_payload["输出要求"] == {
        "card_type": "directions",
        "direction_count": {"min": 3, "max": 5},
    }
    assert response.json()["messages"][-1].get("cards", []) == []
    assert response.json()["actions"] == []


def test_writer_agent_can_store_a_hidden_material_receipt_prompt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: FakeProvider())
    client = _client(tmp_path)
    created = client.post(
        "/api/writer-agent/sessions/file",
        content="第一集：主角在婚礼上被当众羞辱。".encode("utf-8"),
        headers={"X-Writer-Filename": "episode-1.md"},
    )

    response = client.post(
        f"/api/writer-agent/sessions/{created.json()['id']}/messages",
        json={
            "message": "我刚提交了创作材料。请确认已读取，并结合当前材料简要说明可以继续讨论的方向。",
            "display_user_message": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["reports"] == []
    assert response.json()["messages"][-2]["display_user_message"] is False
    assert response.json()["messages"][-1]["role"] == "assistant"


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
