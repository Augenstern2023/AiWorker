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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest

from identity.dependencies import current_principal
from identity.schema import Principal
from writer_agent import api as writer_agent_api
from writer_agent.action_service import ActionService
from writer_agent.schema import ActionCommand, WriterActionError
from writer_agent.store import WriterAgentStore
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


class ConcurrentCreativeProvider(CreativeProvider):
    def __init__(self, parties: int = 2) -> None:
        self.barrier = threading.Barrier(parties)
        self.calls = 0

    async def complete_json(self, messages):
        self.calls += 1
        self.barrier.wait(timeout=5)
        return await super().complete_json(messages)


class SingleCallCreativeProvider(CreativeProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    async def complete_json(self, messages):
        self.calls += 1
        self.started.set()
        assert self.release.wait(timeout=5)
        result = await super().complete_json(messages)
        result["reply"] = f"唯一模型响应 {self.calls}"
        return result


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


class MixedAuthorityProvider:
    async def complete_json(self, messages):
        return {
            "reply": "保留合法的大纲动作。",
            "cards": [
                {
                    "card_id": "invalid-brief",
                    "card_type": "brief",
                    "content": {"title": "非法覆盖"},
                    "assumptions": [],
                },
                {
                    "card_id": "valid-outline-plan",
                    "card_type": "plan",
                    "title": "生成分集大纲",
                    "summary": "沿用已锁定 Brief 继续生成大纲",
                    "steps": [{"stage": "outline"}],
                },
            ],
            "proposed_actions": [
                {
                    "action_type": "creative.run_plan",
                    "title": "非法重做 Brief",
                    "source_card_id": "invalid-brief",
                    "target_stage": "brief",
                    "payload": {},
                    "assumptions": [],
                },
                {
                    "action_type": "creative.run_plan",
                    "title": "生成分集大纲",
                    "source_card_id": "valid-outline-plan",
                    "target_stage": "outline",
                    "payload": {},
                    "assumptions": [],
                },
            ],
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
    permission_service=None,
) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal(
        user_id, permissions
    )
    app.include_router(writer_agent_api.build_writer_agent_router(
        tmp_path,
        creative_repository=creative_repository,
        permission_service=permission_service,
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


def _writer_action_api_context(tmp_path):
    store = WriterAgentStore(tmp_path / "writer-agent-actions.db")
    commands = []
    runner = MagicMock()
    runner.enqueue.side_effect = lambda action_id, _actor: store.finish_action(
        action_id, status="succeeded"
    )
    runner.cancel.side_effect = lambda action_id, _actor: store.finish_action(
        action_id, status="cancelled"
    )
    runner.retry_failed.side_effect = (
        lambda action_id, actor, *, retry_request_id: store.create_retry_action(
            store.require_retryable_action(action_id, actor.user_id),
            actor.user_id,
            retry_request_id=retry_request_id,
        )
    )
    runner.retry_failed_once.side_effect = (
        lambda action_id, actor, *, retry_request_id: store.create_retry_action_once(
            store.require_retryable_action(action_id, actor.user_id),
            actor.user_id,
            retry_request_id=retry_request_id,
        )
    )
    action_service = ActionService(store=store, runner=runner)

    def client_for(user_id: str) -> TestClient:
        app = FastAPI()
        app.dependency_overrides[current_principal] = lambda: _principal(user_id)
        app.include_router(
            writer_agent_api.build_writer_agent_router(
                tmp_path,
                store=store,
                action_service=action_service,
            )
        )
        return TestClient(app)

    def seed_pending(session_id: int, actor_user_id: str) -> dict:
        return store.create_action(
            session_id=session_id,
            actor_user_id=actor_user_id,
            action_type="creative.run_plan",
            project_id=17,
            idempotency_key=f"pending:{session_id}",
            request_hash=f"hash:{session_id}",
            payload={
                "target_stage": "script",
                "episode_start": 1,
                "episode_end": 5,
            },
            expected_context={"session_revision": 0, "project_revision": 1},
            source_snapshot_id="snapshot-1",
        )

    original_decide = action_service.decide

    def recording_decide(command):
        commands.append(command)
        return original_decide(command)

    action_service.decide = recording_decide
    return SimpleNamespace(
        store=store,
        commands=commands,
        runner=runner,
        writer=client_for("writer-1"),
        other=client_for("writer-2"),
        seed_pending=seed_pending,
    )


def test_button_and_natural_language_use_the_same_action_command(
    tmp_path, monkeypatch
) -> None:
    context = _writer_action_api_context(tmp_path)
    button_session = context.store.create("writer-1", "按钮确认", "生成第1-5集剧本")
    button_action = context.seed_pending(button_session["id"], "writer-1")

    button = context.writer.post(
        f"/api/writer-agent/sessions/{button_session['id']}/actions/{button_action['id']}/decision",
        json={
            "decision": "confirm",
            "expected_revision": 1,
            "client_request_id": "button-1",
        },
    )
    natural_session = context.store.create(
        "writer-1", "自然语言确认", "生成第1-5集剧本"
    )
    natural_action = context.seed_pending(natural_session["id"], "writer-1")
    natural = context.writer.post(
        f"/api/writer-agent/sessions/{natural_session['id']}/messages",
        json={"message": "开始生成"},
    )

    assert button.status_code == 202
    assert natural.status_code == 200
    assert all(isinstance(item, ActionCommand) for item in context.commands)
    assert [item.action_id for item in context.commands] == [
        button_action["id"],
        natural_action["id"],
    ]
    assert [item.decision for item in context.commands] == ["confirm", "confirm"]


def test_action_decision_rechecks_session_owner(tmp_path) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "私有动作", "生成剧本")
    action = context.seed_pending(session["id"], "writer-1")

    response = context.other.post(
        f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}/decision",
        json={
            "decision": "confirm",
            "expected_revision": 1,
            "client_request_id": "attack",
        },
    )

    assert response.status_code == 404


def test_repeated_decision_client_request_enqueues_once(tmp_path) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "重复确认", "生成剧本")
    action = context.seed_pending(session["id"], "writer-1")
    url = (
        f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}/decision"
    )
    body = {
        "decision": "confirm",
        "expected_revision": 1,
        "client_request_id": "same-confirm",
    }

    first = context.writer.post(url, json=body)
    second = context.writer.post(url, json=body)

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"] == action["id"]
    context.runner.enqueue.assert_called_once()


def test_active_action_cancel_is_reachable_and_idempotent(tmp_path) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "运行中取消", "生成剧本")
    action = context.seed_pending(session["id"], "writer-1")
    queued = context.store.claim_pending_action(
        action["id"], expected_revision=1, client_request_id="initial-confirm"
    )
    url = (
        f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}/decision"
    )
    body = {
        "decision": "cancel",
        "expected_revision": queued["revision"],
        "client_request_id": "same-active-cancel",
    }

    first = context.writer.post(url, json=body)
    second = context.writer.post(url, json=body)

    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    context.runner.cancel.assert_called_once()


def test_replayed_active_cancel_reconciles_after_crash_before_runner(tmp_path) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "取消恢复", "生成剧本")
    action = context.seed_pending(session["id"], "writer-1")
    queued = context.store.claim_pending_action(action["id"], expected_revision=1)
    accepted, requested = context.store.request_action_cancellation_once(
        action["id"],
        expected_revision=queued["revision"],
        client_request_id="crash-safe-cancel",
    )
    assert requested is True
    assert accepted["phase"] == "cancelling"
    context.runner.cancel.side_effect = lambda action_id, _actor: (
        context.store.finish_action(action_id, status="cancelled")
    )

    recovered = ActionService(
        store=context.store,
        runner=context.runner,
    ).decide(
        ActionCommand(
            session_id=session["id"],
            action_id=action["id"],
            decision="cancel",
            actor=_principal(),
            expected_revision=queued["revision"],
            client_request_id="crash-safe-cancel",
        )
    )

    assert recovered["status"] == "cancelled"
    context.runner.cancel.assert_called_once()


def test_retry_client_request_is_idempotent(tmp_path) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "失败重试", "生成剧本")
    action = context.seed_pending(session["id"], "writer-1")
    context.store.claim_pending_action(
        action["id"], expected_revision=1, client_request_id="initial"
    )
    context.store.fail_action(
        action["id"], code="GENERATION_FAILED", message="失败", retryable=True
    )
    url = f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}/retry"
    body = {"client_request_id": "same-retry"}

    first = context.writer.post(url, json=body)
    second = context.writer.post(url, json=body)

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    context.runner.enqueue.assert_called_once()


def test_natural_language_message_id_is_idempotent_but_equal_text_ids_are_distinct(
    tmp_path,
) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "消息幂等", "生成剧本")
    first_action = context.seed_pending(session["id"], "writer-1")
    url = f"/api/writer-agent/sessions/{session['id']}/messages"

    first = context.writer.post(
        url, json={"message": "开始生成", "message_id": "message-replay"}
    )
    replay = context.writer.post(
        url, json={"message": "开始生成", "message_id": "message-replay"}
    )

    second_action = context.store.create_action(
        session_id=session["id"],
        actor_user_id="writer-1",
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key="second-message-action",
        request_hash="second-message-action",
        payload={"target_stage": "script", "episode_start": 6, "episode_end": 10},
        expected_context={"session_revision": 0, "project_revision": 1},
        source_snapshot_id="snapshot-2",
    )
    second = context.writer.post(
        url, json={"message": "开始生成", "client_message_id": "message-new"}
    )

    assert first.status_code == replay.status_code == second.status_code == 200
    assert first_action["id"] in first.json()["messages"][-1]["action_ids"]
    assert second_action["id"] in second.json()["messages"][-1]["action_ids"]
    assert context.runner.enqueue.call_count == 2


def test_concurrent_same_client_message_persists_one_turn_and_one_action(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SingleCallCreativeProvider()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    claim_calls = 0
    both_claimed = threading.Event()
    original_claim = store.claim_client_message

    def record_claim(*args, **kwargs):
        nonlocal claim_calls
        result = original_claim(*args, **kwargs)
        claim_calls += 1
        if claim_calls == 2:
            both_claimed.set()
        return result

    monkeypatch.setattr(store, "claim_client_message", record_claim)
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal()
    app.include_router(writer_agent_api.build_writer_agent_router(tmp_path, store=store))
    client = TestClient(app)
    session = client.post(
        "/api/writer-agent/sessions",
        json={"content": "完整故事：主角反抗情绪工资制度"},
    ).json()
    url = f"/api/writer-agent/sessions/{session['id']}/messages"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post,
            url,
            json={
                "message": "生成大纲",
                "client_message_id": "same-concurrent-message",
            },
        )
        assert provider.started.wait(timeout=5)
        second = pool.submit(
            client.post,
            url,
            json={
                "message": "生成大纲",
                "client_message_id": "same-concurrent-message",
            },
        )
        assert both_claimed.wait(timeout=5)
        provider.release.set()
        responses = [first.result(timeout=5), second.result(timeout=5)]

    assert [response.status_code for response in responses] == [200, 200]
    persisted = client.get(
        f"/api/writer-agent/sessions/{session['id']}"
    ).json()
    assert sum(
        message.get("message_id") == "same-concurrent-message"
        for message in persisted["messages"]
    ) == 1
    assert len(persisted["actions"]) == 1
    assert provider.calls == 1
    assert responses[0].json() == responses[1].json()


def test_message_claim_heartbeat_prevents_takeover_during_slow_provider(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SingleCallCreativeProvider()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    monkeypatch.setattr(
        writer_agent_api, "_MESSAGE_CLAIM_HEARTBEAT_SECONDS", 0.01, raising=False
    )
    store = WriterAgentStore(tmp_path / "writer-agent-heartbeat.db")
    original_claim = store.claim_client_message

    def short_claim(*args, **kwargs):
        kwargs["lease_seconds"] = 0.05
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim_client_message", short_claim)
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal()
    app.include_router(writer_agent_api.build_writer_agent_router(tmp_path, store=store))
    client = TestClient(app)
    session = client.post(
        "/api/writer-agent/sessions",
        json={"content": "完整故事：主角反抗情绪工资制度"},
    ).json()
    url = f"/api/writer-agent/sessions/{session['id']}/messages"
    request = {
        "message": "生成大纲",
        "client_message_id": "slow-provider-message",
    }

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, url, json=request)
        assert provider.started.wait(timeout=5)
        time.sleep(0.12)
        second = pool.submit(client.post, url, json=request)
        time.sleep(0.12)
        provider.release.set()
        responses = [first.result(timeout=5), second.result(timeout=5)]

    assert [response.status_code for response in responses] == [200, 200]
    assert provider.calls == 1
    assert responses[0].json() == responses[1].json()


def test_linked_chat_fails_closed_without_permission_service(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="权限服务缺失",
        name="私有项目",
        target_episode_count=1,
        created_by="writer-1",
        creation_request_id="missing-permission-service",
    )
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "继续创作", "权限缺失会话")
    store.bind_project(session["id"], project["id"], expected_context_revision=0)
    provider = MagicMock()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal("writer-1")
    app.include_router(
        writer_agent_api.build_writer_agent_router(
            tmp_path,
            store=store,
            creative_repository=repository,
        )
    )

    response = TestClient(app).post(
        f"/api/writer-agent/sessions/{session['id']}/messages",
        json={"message": "读取最新 Brief", "client_message_id": "no-permission"},
    )

    assert response.status_code == 503
    provider.complete_json.assert_not_called()


def test_concurrent_distinct_messages_merge_without_losing_either_turn(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ConcurrentCreativeProvider()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    client = _client(tmp_path)
    session = client.post(
        "/api/writer-agent/sessions",
        json={"content": "完整故事：主角反抗情绪工资制度"},
    ).json()
    url = f"/api/writer-agent/sessions/{session['id']}/messages"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda index: client.post(
                url,
                json={
                    "message": f"生成大纲方案 {index}",
                    "client_message_id": f"concurrent-message-{index}",
                },
            ),
            range(2),
        ))

    assert [response.status_code for response in responses] == [200, 200]
    persisted = client.get(
        f"/api/writer-agent/sessions/{session['id']}"
    ).json()
    ids = {message.get("message_id") for message in persisted["messages"]}
    assert {"concurrent-message-0", "concurrent-message-1"} <= ids
    assert len(persisted["actions"]) == 2


def test_admin_can_read_action_but_cannot_mutate_another_users_action(tmp_path) -> None:
    context = _writer_action_api_context(tmp_path)
    session = context.store.create("writer-1", "管理员读取", "生成剧本")
    action = context.seed_pending(session["id"], "writer-1")
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(
        writer_agent_api.build_writer_agent_router(
            tmp_path,
            store=context.store,
            action_service=ActionService(store=context.store, runner=context.runner),
        )
    )
    admin = TestClient(app)

    readable = admin.get(
        f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}"
    )
    denied = admin.post(
        f"/api/writer-agent/sessions/{session['id']}/actions/{action['id']}/decision",
        json={
            "decision": "confirm",
            "expected_revision": 1,
            "client_request_id": "admin-write",
        },
    )

    assert readable.status_code == 200
    assert denied.status_code == 404


def test_admin_read_scope_does_not_allow_cross_user_session_writes(tmp_path) -> None:
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "原始材料", "私有会话")
    app = FastAPI()
    app.dependency_overrides[current_principal] = _admin_principal
    app.include_router(
        writer_agent_api.build_writer_agent_router(tmp_path, store=store)
    )
    admin = TestClient(app)

    chat = admin.post(
        f"/api/writer-agent/sessions/{session['id']}/messages",
        json={"message": "替别人追加消息", "client_message_id": "admin-cross-write"},
    )
    material = admin.post(
        f"/api/writer-agent/sessions/{session['id']}/materials/file",
        content=b"cross-user material",
        headers={"X-Writer-Filename": "attack.md"},
    )

    assert chat.status_code == material.status_code == 404
    unchanged = store.get(session["id"], "writer-1")
    assert unchanged is not None
    assert unchanged["content"] == "原始材料"
    assert [item["kind"] for item in unchanged["messages"]] == ["material"]


class _DenyProjectWrite:
    def can_access_assigned_project(self, _actor, _project_id: str) -> bool:
        return False

    def can_access_managed_project(self, _actor, _project_id: str) -> bool:
        return False

    def can_create_project_content(
        self, _actor, _project_id: str, *, active_member: bool
    ) -> bool:
        return False


class _InactiveProjectMembership:
    def is_active_project_member(self, _project_id: str, _actor) -> bool:
        return False


class _ReadOnlyProjectAccess(_DenyProjectWrite):
    def can_read_project(self, _actor, _project_id: str) -> bool:
        return True

    def can_create_project_content(
        self, _actor, _project_id: str, *, active_member: bool
    ) -> bool:
        return active_member


def test_project_link_confirmation_requires_read_not_content_write(tmp_path) -> None:
    store = WriterAgentStore(tmp_path / "readable-project-link.db")
    actor = _principal(permissions=frozenset({"script.review.generate"}))
    session = store.create(actor.user_id, "关联只读项目", "关联项目")
    action = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="project.link",
        project_id=17,
        idempotency_key="readable-project-link",
        request_hash="readable-project-link",
        payload={},
        expected_context={"session_revision": 0},
        source_snapshot_id="snapshot",
    )
    runner = MagicMock()
    service = ActionService(
        store=store,
        runner=runner,
        permission_service=_ReadOnlyProjectAccess(),
        workflow_repository=_InactiveProjectMembership(),
    )

    service.decide(
        ActionCommand(
            session_id=session["id"],
            action_id=action["id"],
            decision="confirm",
            actor=actor,
            expected_revision=1,
            client_request_id="confirm-readable-link",
        )
    )

    runner.enqueue.assert_called_once_with(action["id"], actor)


def test_action_service_rechecks_revoked_project_write_before_claim_or_retry(
    tmp_path,
) -> None:
    store = WriterAgentStore(tmp_path / "revoked-writer-actions.db")
    actor = _principal(permissions=frozenset({"script.review.generate"}))
    session = store.create(actor.user_id, "撤权", "生成剧本")
    pending = store.create_action(
        session_id=session["id"],
        actor_user_id=actor.user_id,
        action_type="creative.run_plan",
        project_id=17,
        idempotency_key="revoked-pending",
        request_hash="revoked-pending",
        payload={"target_stage": "script"},
        expected_context={"project_revision": 1},
        source_snapshot_id="snapshot",
    )
    runner = MagicMock()
    service = ActionService(
        store=store,
        runner=runner,
        permission_service=_DenyProjectWrite(),
        workflow_repository=_InactiveProjectMembership(),
    )

    with pytest.raises(WriterActionError, match="PROJECT_ACCESS_DENIED"):
        service.decide(
            ActionCommand(
                session_id=session["id"], action_id=pending["id"], decision="confirm",
                actor=actor, expected_revision=1, client_request_id="revoked-confirm",
            )
        )

    assert store.require_action(pending["id"], actor.user_id)["status"] == "pending"
    runner.enqueue.assert_not_called()

    store.claim_pending_action(pending["id"], expected_revision=1)
    store.fail_action(
        pending["id"], code="GENERATION_FAILED", message="失败", retryable=True
    )
    with pytest.raises(WriterActionError, match="PROJECT_ACCESS_DENIED"):
        service.retry(
            session_id=session["id"], action_id=pending["id"], actor=actor,
            client_request_id="revoked-retry",
        )

    assert len(store.list_session_actions(session["id"])) == 1


def test_original_project_confirmation_rechecks_project_create_permission(tmp_path) -> None:
    store = WriterAgentStore(tmp_path / "original-project-permission.db")
    actor = _principal(permissions=frozenset({"script.review.generate"}))
    session = store.create(actor.user_id, "原创项目", "生成项目")
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="project.create_original", project_id=None,
        idempotency_key="original-project", request_hash="original-project",
        payload={"source_card_id": "card-1"}, expected_context={},
        source_snapshot_id="snapshot",
    )
    runner = MagicMock()
    service = ActionService(store=store, runner=runner)

    with pytest.raises(WriterActionError, match="PROJECT_CREATE_DENIED"):
        service.decide(
            ActionCommand(
                session_id=session["id"], action_id=action["id"], decision="confirm",
                actor=actor, expected_revision=1, client_request_id="original-create",
            )
        )

    assert store.require_action(action["id"], actor.user_id)["status"] == "pending"
    runner.enqueue.assert_not_called()


def test_system_admin_can_confirm_own_original_project_action(tmp_path) -> None:
    store = WriterAgentStore(tmp_path / "admin-original-project.db")
    actor = _admin_principal()
    session = store.create(actor.user_id, "原创项目", "管理员创建项目")
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="project.create_original", project_id=None,
        idempotency_key="admin-original-project",
        request_hash="admin-original-project",
        payload={"source_card_id": "card-1"}, expected_context={},
        source_snapshot_id="snapshot",
    )
    runner = MagicMock()
    service = ActionService(store=store, runner=runner)

    confirmed = service.decide(
        ActionCommand(
            session_id=session["id"], action_id=action["id"],
            decision="confirm", actor=actor, expected_revision=1,
            client_request_id="admin-original-create",
        )
    )

    assert confirmed["status"] == "queued"
    runner.enqueue.assert_called_once_with(action["id"], actor)


class _ConcurrentRetryRunner:
    def __init__(self, store: WriterAgentStore) -> None:
        self.store = store
        self.barrier = threading.Barrier(2)
        self.enqueued: list[str] = []
        self.lock = threading.Lock()

    def retry_failed(self, action_id, actor, *, retry_request_id):
        self.barrier.wait(timeout=5)
        source = self.store.require_retryable_action(action_id, actor.user_id)
        return self.store.create_retry_action(
            source, actor.user_id, retry_request_id=retry_request_id
        )

    def retry_failed_once(self, action_id, actor, *, retry_request_id):
        source = self.store.require_retryable_action(action_id, actor.user_id)
        return self.store.create_retry_action_once(
            source, actor.user_id, retry_request_id=retry_request_id
        )

    def enqueue(self, action_id, _actor) -> None:
        with self.lock:
            self.enqueued.append(action_id)


def test_concurrent_retry_request_creates_and_enqueues_exactly_once(tmp_path) -> None:
    store = WriterAgentStore(tmp_path / "concurrent-retry.db")
    actor = _principal()
    session = store.create(actor.user_id, "并发重试", "生成剧本")
    source = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="creative.run_plan", project_id=17,
        idempotency_key="concurrent-source", request_hash="concurrent-source",
        payload={"target_stage": "script"}, expected_context={"project_revision": 1},
        source_snapshot_id="snapshot",
    )
    store.claim_pending_action(source["id"], expected_revision=1)
    store.fail_action(source["id"], code="GENERATION_FAILED", message="失败", retryable=True)
    runner = _ConcurrentRetryRunner(store)
    service = ActionService(store=store, runner=runner)
    errors: list[BaseException] = []

    def retry() -> None:
        try:
            service.retry(
                session_id=session["id"], action_id=source["id"], actor=actor,
                client_request_id="same-concurrent-retry",
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=retry) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(runner.enqueued) == 1
    assert len(store.list_session_actions(session["id"])) == 2


def test_cancel_audit_records_the_actual_cancelled_result(tmp_path) -> None:
    store = WriterAgentStore(tmp_path / "cancel-audit.db")
    actor = _principal()
    session = store.create(actor.user_id, "取消审计", "生成剧本")
    action = store.create_action(
        session_id=session["id"], actor_user_id=actor.user_id,
        action_type="creative.run_plan", project_id=17,
        idempotency_key="cancel-audit", request_hash="cancel-audit",
        payload={"target_stage": "script"}, expected_context={"project_revision": 1},
        source_snapshot_id="snapshot",
    )
    runner = MagicMock()
    runner.cancel.side_effect = lambda action_id, _actor: store.finish_action(
        action_id, status="cancelled"
    )
    records = []
    audit = MagicMock()
    audit.record.side_effect = lambda **kwargs: records.append(kwargs)
    service = ActionService(store=store, runner=runner, audit_service=audit)

    service.decide(
        ActionCommand(
            session_id=session["id"], action_id=action["id"], decision="cancel",
            actor=actor, expected_revision=1, client_request_id="cancel-audit-request",
        )
    )

    assert records[-1]["action"] == "writer.action.cancel"
    assert records[-1]["after"]["status"] == "cancelled"


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
    assert action["expected_context"]["session_revision"] == 0
    assert action["payload"]["confirmed_brief_payload"] == assistant_message[
        "cards"
    ][0]["content"]
    assert action["payload"]["step_payloads"]["brief.persist"] == assistant_message[
        "cards"
    ][0]["content"]
    snapshot = writer_agent_api.WriterAgentStore(
        tmp_path / "writer_agent" / "writer_agent.db"
    ).require_source_snapshot(action["source_snapshot_id"])
    assert snapshot["snapshot"]["materials"][0]["content"] == "第一集：主角被退婚。"


def test_structured_project_link_intent_creates_pending_link_without_llm(
    tmp_path, monkeypatch
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="情绪决定工资",
        name="情绪公司",
        target_episode_count=12,
        created_by="writer-1",
        creation_request_id="structured-link",
    )
    monkeypatch.setattr(
        writer_agent_api,
        "create_provider",
        lambda: (_ for _ in ()).throw(AssertionError("结构化关联不应调用 LLM")),
    )
    readable_permissions = MagicMock()
    readable_permissions.can_read_project.return_value = True
    client = _client(
        tmp_path,
        creative_repository=repository,
        permission_service=readable_permissions,
    )
    session = client.post(
        "/api/writer-agent/sessions",
        json={"content": "继续创作", "title": "关联项目"},
    ).json()

    response = client.post(
        f"/api/writer-agent/sessions/{session['id']}/messages",
        json={
            "message": "关联已有项目",
            "message_id": "link-project-23",
            "link_project_id": project["id"],
        },
    )

    assert response.status_code == 200
    action = response.json()["actions"][-1]
    assert action["action_type"] == "project.link"
    assert action["project_id"] == project["id"]
    assert action["status"] == "pending"
    assert action["expected_context"]["session_revision"] == 0
    assert response.json()["messages"][-1]["action_ids"] == [action["id"]]


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
    readable_permissions = MagicMock()
    readable_permissions.can_read_project.return_value = True
    client = _client(
        tmp_path,
        creative_repository=repository,
        permission_service=readable_permissions,
    )
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


def test_chat_keeps_unrelated_valid_output_when_one_action_conflicts_with_authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="保留合法模型输出",
        name="混合动作项目",
        target_episode_count=12,
        created_by="writer-1",
        creation_request_id="mixed-authority-output",
    )
    brief = repository.create_blueprints(
        project["id"],
        [{"content": {"title": "已锁定方向"}}],
        direction_type="original",
    )[0]
    repository.lock_brief(brief["id"])
    monkeypatch.setattr(
        writer_agent_api, "create_provider", lambda: MixedAuthorityProvider()
    )
    readable_permissions = MagicMock()
    readable_permissions.can_read_project.return_value = True
    client = _client(
        tmp_path,
        creative_repository=repository,
        permission_service=readable_permissions,
    )
    session = client.post(
        "/api/writer-agent/sessions",
        json={"content": "继续创作当前项目。"},
    ).json()
    writer_agent_api.WriterAgentStore(
        tmp_path / "writer_agent" / "writer_agent.db"
    ).bind_project(session["id"], project["id"], expected_context_revision=0)

    response = client.post(
        f"/api/writer-agent/sessions/{session['id']}/messages",
        json={
            "message": "沿用 Brief 生成大纲",
            "client_message_id": "mixed-authority-message",
        },
    )

    assert response.status_code == 200
    assert [
        card["card_id"] for card in response.json()["messages"][-1]["cards"]
    ] == ["valid-outline-plan"]
    assert len(response.json()["actions"]) == 1
    assert response.json()["actions"][0]["payload"]["target_stage"] == "outline"


def test_chat_rechecks_linked_project_read_access_before_calling_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = writer_agent_api.CreativeRepository(tmp_path / "creative.db")
    project = repository.create_original_project(
        seed_text="撤权后的项目内容",
        name="私有项目",
        target_episode_count=1,
        created_by="writer-1",
        creation_request_id="revoked-chat-read",
    )
    store = WriterAgentStore(tmp_path / "writer-agent.db")
    session = store.create("writer-1", "继续创作", "撤权会话")
    store.bind_project(session["id"], project["id"], expected_context_revision=0)
    permission_service = MagicMock()
    permission_service.can_read_project.return_value = False
    provider = MagicMock()
    monkeypatch.setattr(writer_agent_api, "create_provider", lambda: provider)
    app = FastAPI()
    app.dependency_overrides[current_principal] = lambda: _principal("writer-1")
    app.include_router(
        writer_agent_api.build_writer_agent_router(
            tmp_path,
            store=store,
            creative_repository=repository,
            permission_service=permission_service,
        )
    )

    response = TestClient(app).post(
        f"/api/writer-agent/sessions/{session['id']}/messages",
        json={"message": "把最新大纲发给我", "client_message_id": "revoked-read"},
    )

    assert response.status_code == 403
    provider.complete_json.assert_not_called()


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
