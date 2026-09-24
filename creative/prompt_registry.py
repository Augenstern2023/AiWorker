# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Built-in prompt-library definitions and deterministic prompt composition."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


PROMPT_LIBRARY_SCHEMA_VERSION = "prompt-library-v1"
PROMPT_LIBRARY_STAGES = {
    "brief",
    "characters",
    "outline",
    "beats",
    "script",
    "review",
    "optimization",
}
PROMPT_LIBRARY_MODES = {"single", "series", "repair", "rewrite"}
STYLE_DIMENSION_KEYS = (
    "format",
    "genre",
    "audience",
    "story_engine",
    "emotion",
    "pacing",
    "expression",
)


def _stage_rules(
    *,
    brief: str,
    characters: str,
    outline: str,
    beats: str,
    script: str,
    review: str,
    optimization: str,
    brief_series: str = "",
    characters_series: str = "",
    outline_series: str = "",
    beats_series: str = "",
    script_series: str = "",
    review_series: str = "",
    optimization_series: str = "",
) -> dict[str, dict[str, str]]:
    return {
        "brief": {
            "single": brief,
            "series": brief_series or brief,
            "repair": "只修复结构和缺失字段，不得改变方向的核心卖点、人物欲望或故事引擎。",
            "rewrite": "只重写用户指定的 Brief 字段，保持已确认的题材、受众、内容边界与差异化承诺不变。",
        },
        "characters": {
            "single": characters,
            "series": characters_series or characters,
            "repair": "只修复人物卡、关系结构和缺失字段，不得改变 Brief 的核心设定或人物的既定身份。",
            "rewrite": "只重写用户指定的人物或关系字段，保持角色身份、核心欲望、关系事实与成长方向不变。",
        },
        "outline": {
            "single": outline,
            "series": outline_series or outline,
            "repair": "只修复分集大纲的结构、连续性和缺失字段，不得改变已确认的 Brief、人物事实或核心剧情结果。",
            "rewrite": "只重写用户指定的大纲字段或全集概览，保持其他分集事实、人物动机、前后状态与结局方向不变。",
        },
        "beats": {
            "single": beats,
            "series": beats_series or beats,
            "repair": "只修复结构和缺失字段，不得改变既定剧情事实。",
            "rewrite": "只重写用户指定的节拍，保持上下集状态与人物动机不变。",
        },
        "script": {
            "single": script,
            "series": script_series or script,
            "repair": "只修复结构和缺失字段，不得润色、扩写或改变剧情。",
            "rewrite": "只重写用户指定的场次，保持 Beats、连续性账本和剧情结果不变。",
        },
        "review": {
            "single": review,
            "series": review_series or review,
            "repair": "只修复审核报告的 JSON 结构、缺失字段和字段类型，不得新增、删除或改变审核证据与结论。",
            "rewrite": "只重新组织审核报告的表达和汇总，保持原始证据、问题归因、严重度与修改路径不变。",
        },
        "optimization": {
            "single": optimization,
            "series": optimization_series or optimization,
            "repair": "只修复优化草稿的 JSON 结构、缺失字段和字段类型，不得改变已选问题、冻结事实或剧情结果。",
            "rewrite": "只针对已选审核问题局部重写，保持未涉及内容、冻结大纲、人物事实与连续性不变。",
        },
    }


_BUILTIN_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "key": "classic-compatible",
        "title": "经典兼容模板",
        "description": "保持旧项目原有提示词语义，不额外叠加题材、风格或结构规则。",
        "catalog_visible": False,
        "dimensions": {
            "format": "竖屏短剧",
            "genre": "通用",
            "audience": "通用",
            "story_engine": "冲突推进",
            "emotion": "强情绪",
            "pacing": "快节奏",
            "expression": "可拍摄",
        },
        "shared_dna": "沿用当前阶段既有系统提示词，不追加作品风格规则。",
        "parameters": {},
        "stage_rules": _stage_rules(
            brief="沿用 Brief 阶段既有系统提示词，不追加规则。",
            characters="沿用人物阶段既有系统提示词，不追加规则。",
            outline="沿用大纲阶段既有系统提示词，不追加规则。",
            beats="沿用 Beats 阶段既有系统提示词，不追加规则。",
            script="沿用剧本阶段既有系统提示词，不追加规则。",
            review="沿用审核阶段既有系统提示词，不追加规则。",
            optimization="沿用优化阶段既有系统提示词，不追加规则。",
        ),
        "examples": [],
    },
    {
        "key": "generic-vertical-short",
        "title": "通用竖屏短剧",
        "description": "适合尚未确定细分风格、但需要强冲突和集尾钩子的短剧项目。",
        "catalog_visible": True,
        "dimensions": {
            "format": "竖屏短剧",
            "genre": "通用",
            "audience": "通用",
            "story_engine": "目标—阻力—转折",
            "emotion": "强情绪",
            "pacing": "高密度",
            "expression": "口语化可拍摄",
        },
        "shared_dna": (
            "每集围绕一个清晰的戏剧动作推进。冲突必须造成状态变化，主角要付出代价，"
            "结尾留下具体、可兑现的下一集钩子。"
        ),
        "parameters": {"max_beats": 3, "strong_ending_hook": True},
        "stage_rules": _stage_rules(
            brief="把一句话卖点、主角欲望、核心阻力、情绪承诺和前三集钩子写成同一条可持续故事引擎。",
            characters="每个核心人物都要有外部行动目标、最怕失去之物、行为矛盾、压力反应和不可逾越的底线；人物关系必须能因具体选择而升级或转向。",
            outline="用目标升级、阻力升级、关系变化和信息揭示组成长线引擎；每集只承担一个核心推进职责，并以新的行动压力衔接下一集。",
            beats="按目标、阻力、升级、转折、代价和钩子组织节拍，避免同类冲突重复解决。",
            script="场景尽快进入冲突；动作、台词和场面调度都要能够直接拍摄。",
            review="优先识别冲突进入过慢、状态没有变化、情绪回报不足、重复拖沓和集尾钩子不可兑现的问题，并引用具体场次证据。",
            optimization="用更快进入冲突、更明确的行动与代价、更密集的状态变化修复已选问题，保留未被审核指出的有效内容。",
            brief_series="多个候选方向必须在主角身份、核心阻力或情绪回报上明显不同，并分别说明目标受众与制作成本。",
            beats_series="批次内各集必须承接上一集状态变化，并让冲突逐集升级而非重置。",
            script_series="保持人物语言、关键道具和时空连续性，不能在批次边界重置人物状态。",
            review_series="除逐集问题外，重点检查冲突是否逐集升级、人物与道具状态是否承接，以及钩子是否在后续得到兑现。",
            optimization_series="结构优化必须让各集承接上一集的状态变化并逐级升级，不得用重复冲突或重置人物状态制造节奏。",
        ),
        "examples": [],
    },
    {
        "key": "urban-identity-romcom",
        "title": "都市身份错位甜宠轻喜剧",
        "description": "以观众知情、角色不知情的信息差驱动反差喜剧和感情推进。",
        "catalog_visible": True,
        "dimensions": {
            "format": "竖屏短剧",
            "genre": "都市甜宠轻喜",
            "audience": "女频青年",
            "story_engine": "身份错位",
            "emotion": "甜宠、轻喜、释然",
            "pacing": "单集一梗一推进",
            "expression": "网感互怼",
        },
        "shared_dna": (
            "观众知道核心身份真相，主角之一暂时不知道。笑点来自身份信息差、地位反差和态度转弯；"
            "单元问题最终由人物的真诚选择解决，感情只推进一格，不突然表白。"
        ),
        "parameters": {"max_beats": 3, "strong_ending_hook": True},
        "stage_rules": _stage_rules(
            brief="明确谁掌握真实身份、谁被蒙在鼓里，以及身份错位如何持续制造笑点、关系推进和暴露压力。",
            characters="围绕身份信息差设计人物认知边界、表里反差和互补欲望；台词风格必须彼此可辨，关系弧线每次只推进一格。",
            outline="沿身份暴露风险、关系升温和误解升级三条线交替推进；每集兑现一个信息差笑点，同时只让感情与真相各推进一格。",
            beats="每集安排一个身份错位笑点、一次关系推进和一次差点暴露身份的压力。",
            script="对白口语化并允许内心独白；高位角色的台词短、冷、克制，喜剧不损害人物底色。",
            review="重点检查身份信息差是否清楚、反差笑点是否来自人物认知、感情是否只推进一格，以及角色语言是否可辨。",
            optimization="通过身份信息差、态度转弯和可演的互怼修复已选问题；不得用突然表白、角色降智或泄露全部真相强行推进。",
            review_series="检查身份暴露风险、误解升级与关系升温是否交替推进，后续是否兑现前集的信息差承诺。",
            optimization_series="结构调整应同时维护身份暴露压力和关系递进，每集只推进一格真相与感情。",
        ),
        "examples": [
            {
                "label": "合格特征",
                "text": "观众能看懂真实身份，另一角色只把反常能力理解成临时演员发挥超常。",
                "include_in_context": False,
            }
        ],
    },
    {
        "key": "barrage-revenge",
        "title": "弹幕预警型女性复仇爽剧",
        "description": "通过弹幕提供信息，由女主完成取证、反制和逐级清算。",
        "catalog_visible": True,
        "dimensions": {
            "format": "竖屏短剧",
            "genre": "都市女性复仇",
            "audience": "女频",
            "story_engine": "弹幕预警与证据反杀",
            "emotion": "爽感、解气",
            "pacing": "高频打脸升级",
            "expression": "毒舌金句与专业证据",
        },
        "shared_dna": (
            "弹幕只能提供预警、揭密或陪看情绪，不能替女主行动。女主必须把信息转化为证据和布局；"
            "反派逐级升级，反制同时占据事实与法理高地，不复合、不降智。"
        ),
        "parameters": {"max_beats": 3, "strong_ending_hook": True},
        "stage_rules": _stage_rules(
            brief="明确弹幕的信息边界、女主的主动取证能力、逐级清算对象和不可退让的女性成长底线。",
            characters="女主必须具备把预警转化为证据和布局的主动能力；反派要有可信利益、软肋与失败代价，盟友关系不得替女主完成关键行动。",
            outline="按预警、验证、取证、布局、反制和反派升级组织长线清算；每集都让女主主动获得一项新筹码，并让更高层阻力浮出水面。",
            beats="优先使用使坏、弹幕提示、女主验证、证据反制、局面反转、反派升级的因果链。",
            script="弹幕使用方括号且数量克制；女主台词简短有力，专业判断由证据支撑。",
            review="重点检查弹幕是否越俎代庖、女主是否主动验证与布局、反制是否有事实证据，以及爽点是否伴随更高层阻力。",
            optimization="把弹幕信息转化为女主可执行的验证、取证和反制动作；不得让弹幕直接公布真相或替女主获胜。",
            review_series="检查女主筹码是否逐集增加、反派是否逐级升级，并识别重复打脸、证据跳跃和主角被动获胜。",
            optimization_series="结构优化按预警、验证、取证、布局、反制和升级形成因果链，同时守住女主主动性。",
        ),
        "examples": [
            {
                "label": "错误示例",
                "text": "弹幕直接替女主公布全部真相，女主没有验证便获胜。",
                "include_in_context": False,
            }
        ],
    },
    {
        "key": "oriental-rule-mystery",
        "title": "规则驱动型古装奇幻悬疑",
        "description": "依靠明确规则、对等代价、道具伏笔和信息留白推进的电影化正剧。",
        "catalog_visible": True,
        "dimensions": {
            "format": "横屏剧集",
            "genre": "古装奇幻悬疑",
            "audience": "悬疑与东方幻想受众",
            "story_engine": "规则解谜",
            "emotion": "压迫、好奇、余韵",
            "pacing": "慢悬疑高信息密度",
            "expression": "克制留白、电影化",
        },
        "shared_dna": (
            "奇幻能力必须同时具备限制和代价，既定规则不得临时打补丁。道具承担信息与伏笔，"
            "人物通过动作泄露秘密；表任务、身世谜和规则阴谋并行。"
        ),
        "parameters": {"max_beats": 3, "strong_ending_hook": True},
        "stage_rules": _stage_rules(
            brief="先定义奇幻规则、限制与对等代价，再建立表任务、身世谜和规则阴谋三条可交叉验证的悬疑线。",
            characters="人物秘密必须与既定规则、限制或代价相连，并通过动作和选择泄露；关系中要存在相互隐瞒、试探以及可兑现的信任成本。",
            outline="让表任务、身世谜和规则阴谋分阶段交叉验证；每次反转必须由既定规则、道具或先前选择导出，并同步记录使用能力的代价。",
            beats="每场只推进一个关键信息点，转折由既有规则或道具导出，结尾引入明确外部压力。",
            script="避免现代网感词和解释性台词；场头明确时间、内外景、地点、道具和人物，情绪藏在动作中。",
            review="重点检查规则、限制与代价是否一致，反转是否有既定伏笔，人物秘密是否通过动作泄露，以及台词是否过度解释。",
            optimization="使用既定规则、道具和人物选择修复问题，把解释改成可拍摄动作与信息留白；不得临时新增规则。",
            review_series="跨集核对能力代价、道具去向、人物知情边界和伏笔兑现，识别规则漂移与无依据反转。",
            optimization_series="结构优化必须维持表任务、身世谜和规则阴谋的交叉验证，并延续已发生的能力代价。",
        ),
        "examples": [],
    },
)


def builtin_templates(*, include_hidden: bool = True) -> list[dict[str, Any]]:
    """Return defensive copies of the built-in template definitions."""

    rows = _BUILTIN_TEMPLATES
    if not include_hidden:
        rows = tuple(item for item in rows if item.get("catalog_visible", True))
    return deepcopy(list(rows))


def builtin_template(template_key: str) -> dict[str, Any]:
    normalized = str(template_key or "").strip()
    for item in _BUILTIN_TEMPLATES:
        if item["key"] == normalized:
            return deepcopy(item)
    raise KeyError(normalized)


def normalize_template_content(content: dict[str, Any]) -> dict[str, Any]:
    """Validate the model-independent creative portion of a prompt template."""

    if not isinstance(content, dict):
        raise ValueError("模板内容必须是对象")
    dimensions = dict(content.get("dimensions") or {})
    missing_dimensions = [key for key in STYLE_DIMENSION_KEYS if not str(dimensions.get(key) or "").strip()]
    if missing_dimensions:
        raise ValueError(f"模板缺少风格维度：{', '.join(missing_dimensions)}")
    shared_dna = str(content.get("shared_dna") or "").strip()
    if not shared_dna:
        raise ValueError("模板必须包含共享作品 DNA")
    if len(shared_dna) > 12000:
        raise ValueError("共享作品 DNA 不能超过 12000 字符")
    stage_rules = content.get("stage_rules")
    if not isinstance(stage_rules, dict):
        raise ValueError("模板必须包含阶段规则")
    normalized_rules: dict[str, dict[str, str]] = {}
    for stage in PROMPT_LIBRARY_STAGES:
        rules = stage_rules.get(stage)
        if not isinstance(rules, dict):
            raise ValueError(f"模板缺少 {stage} 阶段规则")
        normalized_rules[stage] = {}
        for mode in PROMPT_LIBRARY_MODES:
            instruction = str(rules.get(mode) or "").strip()
            if not instruction:
                raise ValueError(f"模板缺少 {stage}.{mode} 规则")
            if len(instruction) > 12000:
                raise ValueError(f"{stage}.{mode} 规则不能超过 12000 字符")
            normalized_rules[stage][mode] = instruction
    examples = content.get("examples") or []
    if not isinstance(examples, list):
        raise ValueError("模板 examples 必须是数组")
    if len(examples) > 10:
        raise ValueError("模板示例不能超过 10 条")
    normalized_examples: list[dict[str, Any]] = []
    for index, example in enumerate(examples, start=1):
        if not isinstance(example, dict):
            raise ValueError(f"模板示例 {index} 必须是对象")
        label = str(example.get("label") or "").strip()
        text = str(example.get("text") or "").strip()
        if not text:
            raise ValueError(f"模板示例 {index} 不能为空")
        if len(label) > 120 or len(text) > 4000:
            raise ValueError(f"模板示例 {index} 超出长度限制")
        normalized_examples.append(
            {
                "label": label,
                "text": text,
                "include_in_context": bool(example.get("include_in_context")),
            }
        )
    parameters = content.get("parameters") or {}
    if not isinstance(parameters, dict) or len(parameters) > 50:
        raise ValueError("模板参数必须是至多 50 项的对象")
    max_beats = parameters.get("max_beats")
    if max_beats is not None and (
        isinstance(max_beats, bool) or not isinstance(max_beats, int) or not 1 <= max_beats <= 3
    ):
        raise ValueError("模板 max_beats 必须为 1-3")
    try:
        serialized_parameters = json.dumps(
            parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("模板参数必须可序列化为 JSON") from exc
    if len(serialized_parameters) > 4000:
        raise ValueError("模板参数不能超过 4000 字符")
    return {
        "dimensions": {
            key: str(dimensions[key]).strip()[:120] for key in STYLE_DIMENSION_KEYS
        },
        "shared_dna": shared_dna,
        "parameters": deepcopy(parameters),
        "stage_rules": normalized_rules,
        "examples": normalized_examples,
    }


def content_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compose_prompt_snapshot(
    *,
    template_id: str,
    template_key: str,
    title: str,
    revision: int,
    content: dict[str, Any],
    stage: str,
    mode: str,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one immutable, auditable creative-instruction snapshot."""

    normalized_stage = str(stage or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()
    if normalized_stage not in PROMPT_LIBRARY_STAGES:
        raise ValueError(
            "模板阶段只支持 brief、characters、outline、beats、script、review 或 optimization"
        )
    if normalized_mode not in PROMPT_LIBRARY_MODES:
        raise ValueError("不支持的模板执行模式")
    normalized = normalize_template_content(content)
    applied_override = dict(override or {})
    unknown_override_keys = set(applied_override) - {"custom_requirements", "parameters"}
    if unknown_override_keys:
        raise ValueError(
            f"不支持的模板覆盖字段：{', '.join(sorted(unknown_override_keys))}"
        )
    # The compatibility template is an audit/versioning wrapper around the legacy
    # hard-coded prompts. Re-appending equivalent rules here subtly changes old
    # projects, so only an explicit per-run/project override may add instructions.
    parts = [] if template_key == "classic-compatible" else [
        normalized["shared_dna"],
        normalized["stage_rules"][normalized_stage][normalized_mode],
    ]
    custom = str(applied_override.get("custom_requirements") or "").strip()
    if len(custom) > 4000:
        raise ValueError("单次自定义创作要求不能超过 4000 字符")
    override_parameters = applied_override.get("parameters") or {}
    if not isinstance(override_parameters, dict) or len(override_parameters) > 20:
        raise ValueError("模板参数覆盖必须是至多 20 项的对象")
    parameters = {**normalized["parameters"], **override_parameters}
    max_beats = parameters.get("max_beats")
    if max_beats is not None and (
        isinstance(max_beats, bool) or not isinstance(max_beats, int) or not 1 <= max_beats <= 3
    ):
        raise ValueError("模板 max_beats 必须为 1-3")
    try:
        serialized_parameters = json.dumps(
            parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("模板参数覆盖必须可序列化为 JSON") from exc
    if len(serialized_parameters) > 4000:
        raise ValueError("模板参数覆盖后不能超过 4000 字符")
    if parameters:
        parts.append(f"模板参数（JSON）：{serialized_parameters}")
    for example in normalized["examples"]:
        if not example["include_in_context"]:
            continue
        label = f"（{example['label']}）" if example["label"] else ""
        parts.append(f"模板示例{label}：{example['text']}")
    if custom:
        parts.append(custom)
    effective = "\n".join(f"{index}. {part}" for index, part in enumerate(parts, start=1))
    snapshot = {
        "schema_version": PROMPT_LIBRARY_SCHEMA_VERSION,
        "template_id": str(template_id),
        "template_key": str(template_key),
        "title": str(title),
        "revision": int(revision),
        "stage": normalized_stage,
        "mode": normalized_mode,
        "dimensions": normalized["dimensions"],
        "parameters": parameters,
        "effective_instruction": effective,
        "override": applied_override,
    }
    snapshot["content_hash"] = content_hash(snapshot)
    return snapshot
