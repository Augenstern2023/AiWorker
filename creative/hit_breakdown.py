# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from typing import Any


_BREAKDOWN_FIELD_GUIDE: tuple[dict[str, str], ...] = (
    {
        "key": "title",
        "label": "剧名",
        "type": "文本",
        "description": "来源剧目的正式名称，用于关联榜单、转写、评论和后续创作项目。",
        "writing_prompt": "保持与来源数据一致，不添加宣传语、集数或平台名称。",
        "example": "家里家外",
        "evidence_rule": "必须来自榜单快照或剧目元数据。",
    },
    {
        "key": "one_line_selling_point",
        "label": "一句话卖点",
        "type": "文本",
        "description": "用一句话说明核心设定、主要冲突和观众能够获得的情绪回报。",
        "writing_prompt": "按“特殊设定 + 主角困境/目标 + 核心爽感或情绪价值”组织，建议 30-60 字。",
        "example": "被婆家轻视的家庭主妇重返职场，在守护孩子的同时完成身份逆袭。",
        "evidence_rule": "有剧情正文时归纳；只有榜单时必须标记为待验证判断。",
    },
    {
        "key": "category",
        "label": "题材",
        "type": "文本",
        "description": "剧目的内容题材，而不是热榜、推荐榜等榜单名称。",
        "writing_prompt": "填写 1-3 个稳定题材标签，使用“家庭 / 逆袭 / 都市”这类表达。",
        "example": "家庭 / 逆袭",
        "evidence_rule": "优先采用平台题材标签；人工判断时需结合简介或转写。",
    },
    {
        "key": "target_audience",
        "label": "目标受众",
        "type": "文本",
        "description": "最容易对人物处境、情绪议题和爽点产生代入感的用户群体。",
        "writing_prompt": "说明年龄/性别倾向、情绪需求和典型观看动机，避免只写“所有人”。",
        "example": "25-45 岁女性；关注家庭关系、婚姻成长与自我价值。",
        "evidence_rule": "优先依据评论画像和平台标签；缺失时标记为推断。",
    },
    {
        "key": "protagonist_identity",
        "label": "主角身份",
        "type": "文本",
        "description": "主角开局身份、现实处境和最具传播价值的反差标签。",
        "writing_prompt": "写清“是谁、处于什么困境、隐藏或将获得什么反差”，不要复述完整剧情。",
        "example": "被婆家轻视的全职母亲，曾是行业头部销售。",
        "evidence_rule": "身份事实必须来自简介、字幕或转写；榜单无法证明时留空或标记待补充。",
    },
    {
        "key": "resistance_source",
        "label": "阻力来源",
        "type": "文本",
        "description": "阻止主角实现目标的主要人物、关系、制度、资源或内心障碍。",
        "writing_prompt": "区分外部阻力和内部阻力，并说明阻力为什么能够持续推动剧情。",
        "example": "外部是控制欲强的婆家与职场偏见；内部是主角长期形成的自我否定。",
        "evidence_rule": "必须由可定位剧情节点支持；不能仅凭题材标签推断具体反派。",
    },
    {
        "key": "core_conflict",
        "label": "核心冲突",
        "type": "文本",
        "description": "贯穿主线的目标对抗，回答主角想要什么、谁或什么阻止她、失败代价是什么。",
        "writing_prompt": "使用“主角目标 vs 核心阻力；失败代价”结构，不把单个事件误写成核心冲突。",
        "example": "主角要取得孩子抚养与经济独立，婆家持续控制其资源；失败将失去孩子和重新开始的机会。",
        "evidence_rule": "至少需要简介、分析报告或连续剧情转写支持。",
    },
    {
        "key": "hook_points",
        "label": "前三集钩子",
        "type": "数组",
        "description": "前三集用于留住观众的异常事件、身份悬念、利益危机和结尾悬念。",
        "writing_prompt": "每项写明集数、触发事件、悬念问题和证据来源；建议 3-6 项。",
        "example": "第1集：主角在离婚现场收到神秘公司的任命短信；她为何被选中？",
        "evidence_rule": "无前三集字幕/转写时保持空数组，不得生成事实性钩子。",
    },
    {
        "key": "selling_points",
        "label": "主要爽点",
        "type": "数组",
        "description": "能够重复兑现的情绪回报，例如身份反差、公开反击、能力碾压或关系治愈。",
        "writing_prompt": "每项写“触发条件 + 兑现方式 + 观众情绪”，避免只写“爽、反转多”。",
        "example": "婆家公开贬低主角时，她用专业能力拿下关键客户，完成身份反差与当场反击。",
        "evidence_rule": "优先引用具体剧情节点和评论共鸣，无法验证时标记为推断。",
    },
    {
        "key": "emotional_curve",
        "label": "情绪曲线",
        "type": "数组",
        "description": "观众随剧情经历的主要情绪阶段及每次情绪抬升的触发点。",
        "writing_prompt": "按时间顺序写“压抑/好奇 → 期待 → 释放 → 新危机”等阶段，并关联剧情事件。",
        "example": "受辱压抑 → 身份伏笔带来期待 → 首次反击释放 → 孩子危机再次拉高焦虑。",
        "evidence_rule": "需要连续剧情或分集分析，单条榜单记录不能支撑完整曲线。",
    },
    {
        "key": "reversal_nodes",
        "label": "反转节点",
        "type": "数组",
        "description": "改变人物认知、力量关系或剧情目标的关键节点，而不是普通信息揭示。",
        "writing_prompt": "每项说明反转前认知、揭示内容、反转后影响及所在集数。",
        "example": "第3集：观众原以为男主是竞争对手，实际他是主角匿名资助人，主线由对抗转为合作。",
        "evidence_rule": "必须有明确剧情证据；无转写时保持空数组。",
    },
    {
        "key": "comment_resonance",
        "label": "评论共鸣",
        "type": "文本",
        "description": "评论区反复出现的代入点、争议点、情绪词和现实议题。",
        "writing_prompt": "概括高频观点并区分支持、反对和争议，不用点赞数替代评论正文。",
        "example": "观众集中讨论全职母亲重返职场的现实阻力，对婆家控制经济的情节产生强烈愤怒。",
        "evidence_rule": "没有评论正文时必须写“未知：未验证评论正文”。",
    },
    {
        "key": "imitable_elements",
        "label": "可借鉴元素",
        "type": "数组",
        "description": "可迁移的结构、节奏、情绪机制和人物关系功能，不是可复制的具体剧情。",
        "writing_prompt": "写清可保留机制与必须替换的表达，例如保留“连续身份反差”，替换职业、人物和事件。",
        "example": "可借鉴“低位开局—能力伏笔—公开反击”的节奏；必须替换人物身份、家庭关系和反击事件。",
        "evidence_rule": "需同时记录差异化要求，禁止复制角色名、台词和独特剧情组合。",
    },
    {
        "key": "production_difficulty",
        "label": "制作难度",
        "type": "文本",
        "description": "从场景、演员、服化道、动作、特效和调度角度判断制作复杂度。",
        "writing_prompt": "填写低/中/高并列出主要成本来源，不以主观“好拍”代替分析。",
        "example": "中：核心场景集中，但需要多名家庭角色、职场群演和两场大型宴会调度。",
        "evidence_rule": "依据已知场景和人物规模评估；资料不足时明确待人工复核。",
    },
    {
        "key": "risk_notes",
        "label": "风险说明",
        "type": "数组",
        "description": "记录证据缺口、未经验证的判断、版权相似风险和内容合规风险。",
        "writing_prompt": "逐项说明风险对象、原因、影响和建议动作，不使用笼统的“有风险”。",
        "example": "缺少前三集转写，当前钩子为空；补齐字幕后再生成，不得从同题材竞品推断。",
        "evidence_rule": "所有推断字段和缺失证据必须在此处留痕。",
    },
)


def breakdown_field_guide() -> list[dict[str, str]]:
    return [dict(item) for item in _BREAKDOWN_FIELD_GUIDE]


def personalize_breakdown_field_guide(content: dict[str, Any]) -> list[dict[str, str]]:
    guide = breakdown_field_guide()
    for item in guide:
        value = content.get(item["key"])
        if isinstance(value, list):
            example = str(value[0]).strip() if value else ""
        else:
            example = str(value or "").strip()
        item["example"] = example or "当前转写证据不足，暂未生成示例"
    return guide


def build_breakdown(topic: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    evidence = topic.get("evidence") or []
    source = evidence[0] if evidence else {}
    content = {
        "title": topic.get("title", ""),
        "one_line_selling_point": topic.get("recommendation", ""),
        "category": topic.get("category", ""),
        "target_audience": "待人工判断",
        "protagonist_identity": "待结合简介或转写补充",
        "resistance_source": "待结合连续剧情补充",
        "core_conflict": "待结合分析报告或转写补充",
        "hook_points": [],
        "selling_points": [],
        "emotional_curve": [],
        "reversal_nodes": [],
        "comment_resonance": "未知：未验证评论正文",
        "imitable_elements": [],
        "production_difficulty": "中：待人工评估",
        "risk_notes": ["当前版本仅依据榜单快照，不得据此断言剧情、钩子或台词事实"],
    }
    sources = {key: source for key in ("title", "category", "one_line_selling_point")}
    risks = ["缺少分析报告、评论正文或转写时，结构性字段必须由人工补充"]
    return content, sources, risks
