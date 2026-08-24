# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/writer_agent\service.py
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
from typing import Any


SCORE_FIELDS = ("overall", "structure", "character", "pacing", "hook")
SCORE_LABELS = {
    "overall": "总分",
    "structure": "结构",
    "character": "人物",
    "pacing": "节奏",
    "hook": "钩子",
}
_REVIEW_REQUEST_PATTERN = re.compile(r"评审|审核|评分|打分|评估|诊断|审稿|review", re.IGNORECASE)


def _score(value: Any) -> int:
    try:
        return max(0, min(100, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def normalize_writer_report(payload: dict[str, Any]) -> dict[str, Any]:
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else {}
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    return {
        "reply": str(payload.get("reply") or "我已完成本轮诊断。").strip(),
        "scores": {key: _score(scores.get(key)) for key in SCORE_FIELDS},
        "summary": str(payload.get("summary") or "").strip(),
        "strengths": _string_list(payload.get("strengths"), 6),
        "findings": [
            {
                "priority": str(item.get("priority") or "P2").upper() if isinstance(item, dict) else "P2",
                "dimension": str(item.get("dimension") or "综合") if isinstance(item, dict) else "综合",
                "title": str(item.get("title") or "待优化问题") if isinstance(item, dict) else "待优化问题",
                "evidence": str(item.get("evidence") or "证据不足") if isinstance(item, dict) else "证据不足",
                "suggestion": str(item.get("suggestion") or "补充可执行的剧情信息") if isinstance(item, dict) else "补充可执行的剧情信息",
                "route": str(item.get("route") or "继续对话确认") if isinstance(item, dict) else "继续对话确认",
            }
            for item in findings[:8]
        ],
        "next_questions": _string_list(payload.get("next_questions"), 3),
    }


def is_review_request(user_message: str) -> bool:
    return bool(_REVIEW_REQUEST_PATTERN.search(user_message or ""))


def writer_messages(
    content: str | None = None,
    history: list[dict[str, Any]] | None = None,
    user_message: str = "",
    *,
    review_requested: bool = True,
    material_name: str = "当前创作材料",
    material_catalog: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    project_context: dict[str, Any] | None = None,
    reference_context: list[dict[str, Any]] | None = None,
    direction_required: bool = False,
) -> list[dict[str, str]]:
    review_contract = {
        "reply": "以短剧/漫剧编剧大师身份给出的本轮答复",
        "scores": {"overall": 0, "structure": 0, "character": 0, "pacing": 0, "hook": 0},
        "summary": "结构化总评",
        "strengths": ["优点"],
        "findings": [{"priority": "P1/P2", "dimension": "剧情结构/人物/节奏/爽点/钩子", "title": "问题", "evidence": "输入中的证据", "suggestion": "可执行改法", "route": "下一步处理"}],
        "next_questions": ["有助于继续创作的追问"],
    }
    creative_contract: dict[str, Any] = {
        "reply": "直接答复",
        "cards": [{
            "card_id": "card-1", "card_type": "brief",
            "content": {"title": "可执行创作简介"}, "assumptions": [],
        }],
        "proposed_actions": [{
            "action_type": "creative.run_plan", "title": "生成第1-5集剧本",
            "target_stage": "script", "episode_start": 1, "episode_end": 5,
            "payload": {}, "assumptions": [],
        }],
    }
    if direction_required:
        creative_contract["cards"] = [{
            "card_id": "card-directions-1",
            "card_type": "directions",
            "items": [{
                "title": "谎言听证人",
                "protagonist": "失职律师",
                "background": "超能法庭",
                "core_conflict": "真话会伤害无辜者",
                "relationship_engine": "证人与律师互相试探",
                "emotional_payoff": "主角重新定义正义",
                "difference_dimensions": ["protagonist", "background", "conflict"],
            }, {
                "title": "宫廷静默令",
                "protagonist": "小宫女",
                "background": "架空王朝",
                "core_conflict": "揭穿谎言就会暴露自己",
                "relationship_engine": "宫女与摄政王相互制衡",
                "emotional_payoff": "弱者以真话夺回话语权",
                "difference_dimensions": ["protagonist", "background", "relationship"],
            }, {
                "title": "直播间里的真心话",
                "protagonist": "过气主播",
                "background": "近未来直播城",
                "core_conflict": "平台利用她操纵公众情绪",
                "relationship_engine": "主播与宿敌被迫结盟",
                "emotional_payoff": "被消费的人联手反向审判平台",
                "difference_dimensions": ["protagonist", "background", "emotional_payoff"],
            }],
        }]
        creative_contract["proposed_actions"] = []
    contract = review_contract if review_requested else creative_contract
    mode_instruction = (
        "用户已明确要求评审；给出评分、结构化诊断和可执行优化意见。"
        if review_requested else
        "用户未要求评审。全部材料均可作为剧情事实；项目权威上下文中的已锁定成果优先。"
        + (
            "当前是一句话灵感起点，必须先返回唯一 directions 卡片，"
            "其中包含 3–5 个实质不同的创作方向。"
            if direction_required else ""
        )
        + "只直接回答本轮问题，不输出评分、总评、评审报告或固定诊断栏目。"
    )
    history = history or []
    conversation_history = [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in history[-12:]
        if item.get("kind") != "material"
    ]
    current_content = content or ""
    all_materials = materials or [{"name": material_name, "content": current_content}]
    user_payload: dict[str, Any] = {
        "当前材料": {"name": material_name, "content": current_content},
        "材料目录": material_catalog or [],
        "历史对话": conversation_history,
        "本轮问题": user_message,
    }
    if not review_requested:
        user_payload["全部材料"] = all_materials
        user_payload["项目权威上下文"] = project_context or {}
        user_payload["可选参考"] = reference_context or []
        if direction_required:
            user_payload["输出要求"] = {
                "card_type": "directions",
                "direction_count": {"min": 3, "max": 5},
            }
    return [
        {"role": "system", "content": "你是短剧/漫剧编剧大师。"
         + ("当前材料是剧情事实的唯一来源，材料目录只用于辨认文件，不得据此推断剧情、集数或版本。历史对话中的助手说法不是事实证据；与当前材料冲突时，以当前材料为准。"
            if review_requested else
            "全部材料与项目权威上下文是事实来源；历史对话中的助手说法不是事实证据。")
         + "不得编造未提供的剧情、人物或集数。" + mode_instruction
         + "只返回 JSON：" + json.dumps(contract, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def build_writer_report_markdown(session: dict[str, Any]) -> str:
    report = normalize_writer_report(session.get("report") if isinstance(session.get("report"), dict) else {})
    title = str(session.get("title") or "未命名创作会话").strip()
    lines = [
        "# 编剧大师评审报告",
        "",
        f"**材料：** {title}",
        "",
        "## 评分",
        "",
        " | ".join(f"{SCORE_LABELS[key]}：{report['scores'][key]}" for key in SCORE_FIELDS),
        "",
        "## 本轮答复",
        "",
        report["reply"],
    ]
    if report["summary"]:
        lines.extend(["", "## 总评", "", report["summary"]])
    if report["strengths"]:
        lines.extend(["", "## 优点", "", *(f"- {item}" for item in report["strengths"])])
    if report["findings"]:
        lines.extend(["", "## 优化建议", ""])
        for index, finding in enumerate(report["findings"], start=1):
            lines.extend([
                f"### {index}. [{finding['priority']}] {finding['dimension']}：{finding['title']}",
                "",
                f"- 证据：{finding['evidence']}",
                f"- 建议：{finding['suggestion']}",
                f"- 处理路径：{finding['route']}",
                "",
            ])
    if report["next_questions"]:
        lines.extend(["## 建议继续确认", "", *(f"- {item}" for item in report["next_questions"])])
    return "\n".join(lines).rstrip() + "\n"
