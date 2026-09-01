# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agents.rank_zentao_delivery import (
    RankDeliveryConfig,
    build_rank_message,
    deliver_rank_update,
    post_json,
)


def _config(tmp_path):
    return RankDeliveryConfig(
        endpoint="http://127.0.0.1:18080/zentao/im-apisendgroupmessage.json",
        token="token",
        gid="group",
        sender_name="AiWorker 榜单助手",
        state_dir=tmp_path / "state",
    )


def test_build_rank_message_summarizes_douyin_snapshot(tmp_path):
    source = tmp_path / "douyin_series_rank_20260831_101010.json"
    source.write_text(
        json.dumps(
            {
                "captured_at": "2026-08-31 10:10:10",
                "records": [
                    {"rank_category": "短剧", "tab": "热播榜", "rank": 1, "title": "甲"},
                    {"rank_category": "短剧", "tab": "热播榜", "rank": 2, "title": "乙"},
                    {"rank_category": "漫剧", "tab": "热榜", "rank": 1, "title": "丙"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    message = build_rank_message("dy", source)

    assert message.part_key == "douyin-rank"
    assert message.report_date == "2026-08-31"
    assert message.title == "抖音榜单更新（2026-08-31）"
    assert "共 3 条、2 个榜单" in message.content
    assert "短剧 / 热播榜：甲、乙" in message.content
    assert "漫剧 / 热榜：丙" in message.content


def test_build_rank_message_summarizes_hongguo_csv(tmp_path):
    source = tmp_path / "top10_rankings.csv"
    source.write_text(
        "榜单类型,排名,剧名\n红果热播榜,1,甲\n红果热播榜,2,乙\n真人剧推荐榜,1,丙\n",
        encoding="utf-8",
    )

    message = build_rank_message("hg", source, report_date="2026-08-31")

    assert message.part_key == "hongguo-rank"
    assert message.title == "红果榜单更新（2026-08-31）"
    assert "共 3 条、2 个榜单" in message.content
    assert "红果热播榜：甲、乙" in message.content
    assert "真人剧推荐榜：丙" in message.content


def test_deliver_rank_update_is_idempotent(tmp_path):
    source = tmp_path / "top10_rankings.csv"
    source.write_text("榜单类型,排名,剧名\n红果热播榜,1,甲\n", encoding="utf-8")
    calls = []

    def fake_post(endpoint, payload, timeout_seconds):
        calls.append((endpoint, payload, timeout_seconds))
        return {"status_code": 200, "body": "success"}

    first = deliver_rank_update(
        "hg",
        source,
        config=_config(tmp_path),
        report_date="2026-08-31",
        post_json=fake_post,
    )
    second = deliver_rank_update(
        "hg",
        source,
        config=_config(tmp_path),
        report_date="2026-08-31",
        post_json=fake_post,
    )

    assert first["status"] == "sent"
    assert second == {"status": "skipped", "reason": "already-sent", "part_key": "hongguo-rank"}
    assert len(calls) == 1
    assert calls[0][1]["partKey"] == "hongguo-rank"
    assert calls[0][1]["gid"] == "group"


def test_deliver_rank_update_sends_each_platform_only_once_per_day(tmp_path):
    source = tmp_path / "top10_rankings.csv"
    source.write_text("榜单类型,排名,剧名\n红果热播榜,1,甲\n", encoding="utf-8")
    calls = []

    def fake_post(endpoint, payload, timeout_seconds):
        calls.append(payload)
        return {"status_code": 200, "body": "success"}

    first = deliver_rank_update(
        "hg",
        source,
        config=_config(tmp_path),
        report_date="2026-08-31",
        post_json=fake_post,
    )
    source.write_text("榜单类型,排名,剧名\n红果热播榜,1,乙\n", encoding="utf-8")
    second = deliver_rank_update(
        "hg",
        source,
        config=_config(tmp_path),
        report_date="2026-08-31",
        post_json=fake_post,
    )

    assert first["status"] == "sent"
    assert second["status"] == "skipped"
    assert second["reason"] == "already-sent"
    assert len(calls) == 1


def test_deliver_rank_update_stays_disabled_without_endpoint(tmp_path):
    source = tmp_path / "top10_rankings.csv"
    source.write_text("榜单类型,排名,剧名\n红果热播榜,1,甲\n", encoding="utf-8")
    config = RankDeliveryConfig(
        endpoint="",
        token="",
        gid="",
        sender_name="AiWorker 榜单助手",
        state_dir=tmp_path / "state",
    )

    assert deliver_rank_update("hg", source, config=config) == {
        "status": "skipped",
        "reason": "not-configured",
        "part_key": "hongguo-rank",
    }


def test_post_json_bypasses_system_proxy_for_internal_zentao(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"success")

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    try:
        response = post_json(
            f"http://127.0.0.1:{server.server_port}/zentao/im-apisendgroupmessage.json",
            {"content": "榜单"},
            3,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert response == {"status_code": 200, "body": "success"}
    assert received == [{"content": "榜单"}]
