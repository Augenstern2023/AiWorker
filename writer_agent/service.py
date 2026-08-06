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
from typing import Any


SCORE_FIELDS = ("overall", "structure", "character", "pacing", "hook")
SCORE_LABELS = {
    "overall": "总分",
    "structure": "结构",
    "character": "人物",
    "pacing": "节奏",
    "hook": "钩子",
}


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


def writer_messages(content: str, history: list[dict[str, str]], user_message: str) -> list[dict[str, str]]:
    contract = {
        "reply": "以短剧/漫剧编剧大师身份给出的本轮答复",
        "scores": {"overall": 0, "structure": 0, "character": 0, "pacing": 0, "hook": 0},
        "summary": "结构化总评",
        "strengths": ["优点"],
        "findings": [{"priority": "P1/P2", "dimension": "剧情结构/人物/节奏/爽点/钩子", "title": "问题", "evidence": "输入中的证据", "suggestion": "可执行改法", "route": "下一步处理"}],
        "next_questions": ["有助于继续创作的追问"],
    }
    return [
        {"role": "system", "content": "你是短剧/漫剧编剧大师。只依据用户提供的内容，不编造已发生剧情。每轮都给出评分、结构化诊断和可执行优化意见；保持大纲意图，除非明确建议人工改大纲。只返回 JSON：" + json.dumps(contract, ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"创作材料": content, "历史对话": history[-12:], "本轮问题": user_message}, ensure_ascii=False)},
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
