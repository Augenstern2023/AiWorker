# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_writer_agent_service.py
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

import json

from writer_agent.action_service import parse_creative_cards, parse_proposed_actions
from writer_agent.service import build_writer_report_markdown, normalize_writer_report, writer_messages


def test_writer_agent_normalizes_a_structured_writer_report():
    report = normalize_writer_report({
        "reply": "这一集的钩子已经成立。",
        "scores": {"overall": 82, "structure": 80, "character": 84, "pacing": 78, "hook": 86},
        "summary": "开局有效，第二幕需要加压。",
        "strengths": ["主角目标明确"],
        "findings": [{"priority": "P1", "dimension": "节奏", "title": "中段阻力不足", "evidence": "冲突很快结束", "suggestion": "加入时间限制", "route": "优化 Beats"}],
        "next_questions": ["反派想获得什么？"],
    })

    assert report["scores"]["overall"] == 82
    assert report["findings"][0]["priority"] == "P1"
    assert report["reply"] == "这一集的钩子已经成立。"


def test_writer_agent_exports_a_human_readable_markdown_report():
    markdown = build_writer_report_markdown({
        "title": "第 1 集诊断",
        "content": "主角在婚礼上被退婚。",
        "report": {
            "reply": "开场冲突清楚。",
            "scores": {"overall": 80, "structure": 78, "character": 82, "pacing": 76, "hook": 84},
            "summary": "前半集有效。",
            "strengths": ["冲突前置"],
            "findings": [{"priority": "P1", "dimension": "节奏", "title": "中段偏慢", "evidence": "解释太多", "suggestion": "用动作带出信息", "route": "调整第二场"}],
            "next_questions": ["反派如何阻拦？"],
        },
    })

    assert "# 编剧大师评审报告" in markdown
    assert "## 评分" in markdown
    assert "中段偏慢" in markdown


def test_writer_agent_tolerates_malformed_model_fields():
    report = normalize_writer_report({
        "scores": {"overall": "无法评分"},
        "strengths": "不应拆成单字",
        "findings": ["不是对象"],
    })

    assert report["scores"]["overall"] == 0
    assert report["strengths"] == []
    assert report["findings"][0]["priority"] == "P2"


def test_writer_agent_conversation_prompt_does_not_request_a_review_or_scores():
    messages = writer_messages(
        "第 30 集：主角在法宝失控后选择向师叔坦白。",
        [{"role": "user", "content": "我想保留师徒关系的轻松感。"}],
        "这一段怎样让转折更自然？",
        review_requested=False,
        material_name="第1-30集场次剧本.txt",
        material_catalog=[{"name": "第1-10集.docx", "length": 1000}, {"name": "第1-30集场次剧本.txt", "length": 3000}],
    )

    assert '"scores"' not in messages[0]["content"]
    payload = json.loads(messages[-1]["content"])
    assert payload["当前材料"]["name"] == "第1-30集场次剧本.txt"
    assert payload["当前材料"]["content"].startswith("第 30 集")
    assert payload["材料目录"][-1]["name"] == "第1-30集场次剧本.txt"


def test_non_review_prompt_contains_all_materials_and_project_authority():
    messages = writer_messages(
        materials=[
            {"name": "旧剧本.md", "content": "第一版"},
            {"name": "人物补充.txt", "content": "主角怕水"},
        ],
        history=[],
        user_message="生成第1集剧本",
        review_requested=False,
        project_context={"locked_brief": {"id": 9, "version": 2}},
    )

    payload = json.loads(messages[-1]["content"])
    assert [item["name"] for item in payload["全部材料"]] == ["旧剧本.md", "人物补充.txt"]
    assert payload["项目权威上下文"]["locked_brief"]["id"] == 9


def test_unlinked_conversation_requests_project_creation_before_generation():
    """Catch unbound run plans that can only fail with PROJECT_ACCESS_DENIED."""

    messages = writer_messages(
        materials=[{"name": "故事大纲.md", "content": "顾则远在伤痕中学会自救。"}],
        history=[],
        user_message="根据大纲生成第1-5章正文",
        review_requested=False,
        project_context={},
    )

    contract = json.loads(messages[0]["content"].split("只返回 JSON：", 1)[1])
    actions = parse_proposed_actions(contract)

    assert len(actions) == 1
    assert actions[0].action_type == "project.create_original"
    assert actions[0].target_stage == "script"
    assert actions[0].episode_start == 1
    assert actions[0].episode_end == 5
    assert {
        "seed_text",
        "name",
        "genre",
        "target_platform",
        "target_audience",
        "target_episode_count",
        "episode_duration_seconds",
        "cost_level",
        "content_boundaries",
        "reference_refs",
    } <= actions[0].payload.keys()


def test_linked_conversation_requests_generation_for_current_project():
    messages = writer_messages(
        materials=[{"name": "故事大纲.md", "content": "顾则远在伤痕中学会自救。"}],
        history=[],
        user_message="根据大纲生成第1-5章正文",
        review_requested=False,
        project_context={"project_id": 17},
    )

    contract = json.loads(messages[0]["content"].split("只返回 JSON：", 1)[1])
    actions = parse_proposed_actions(contract)

    assert len(actions) == 1
    assert actions[0].action_type == "creative.run_plan"


def test_one_line_inspiration_prompt_contract_contains_valid_distinct_directions():
    messages = writer_messages(
        materials=[{"name": "灵感.txt", "content": "一个能听见谎言的女孩。"}],
        history=[],
        user_message="给我几个创作方向",
        review_requested=False,
        direction_required=True,
    )

    contract = json.loads(messages[0]["content"].split("只返回 JSON：", 1)[1])
    cards = parse_creative_cards(contract)

    assert len(cards) == 1
    assert len(cards[0].items) == 3
