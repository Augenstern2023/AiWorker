# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Build a writer-friendly ZIP from a validated production snapshot."""
from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO
from typing import Any

from creative.api import (
    build_breakdown_markdown,
    build_script_text,
    build_video_parameter_recommendation_text,
)


_FIELD_LABELS = {
    "title": "标题",
    "name": "姓名",
    "role": "角色定位",
    "premise": "核心创意",
    "logline": "一句话梗概",
    "genre": "题材类型",
    "theme": "主题",
    "tone": "风格基调",
    "target_audience": "目标受众",
    "world": "世界观",
    "setting": "故事背景",
    "summary": "梗概",
    "synopsis": "剧情梗概",
    "story_summary": "故事梗概",
    "episode": "集数",
    "episode_number": "集数",
    "episode_title": "本集标题",
    "core_event": "核心事件",
    "conflict": "冲突",
    "character_progress": "人物推进",
    "highlight": "爽点",
    "reversal": "反转",
    "cliffhanger": "结尾悬念",
    "estimated_seconds": "预计时长（秒）",
    "series_overview": "全剧剧情概览",
    "age": "年龄",
    "gender": "性别",
    "appearance": "外形特征",
    "personality": "性格",
    "background": "人物背景",
    "goal": "目标",
    "motivation": "动机",
    "arc": "人物弧光",
    "from": "人物一",
    "to": "人物二",
    "relationship": "关系",
    "relationship_type": "关系类型",
    "description": "说明",
    "beats": "节拍",
    "order": "节拍序号",
    "scene": "场景",
    "characters": "出场人物",
    "obstacle": "阻力",
    "emotion_change": "情绪变化",
    "state_change": "局面变化",
    "cost": "代价",
    "opposition_tactic": "对抗策略",
    "continuity_updates": "连续性变更",
    "turn": "转折",
    "hook": "结尾钩子",
}
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def build_production_archive(package: dict[str, Any]) -> bytes:
    """Return deterministic ZIP bytes for one already-validated package."""
    project = dict(package.get("project") or {})
    project_name = str(project.get("name") or "未命名项目").strip()
    provenance = dict(package.get("provenance") or {})
    episode_numbers = [int(number) for number in provenance.get("episode_numbers") or []]
    total = int(provenance.get("project_total_episodes") or len(episode_numbers) or 0)
    width = max(3, len(str(max(total, max(episode_numbers, default=0)))))

    files: dict[str, bytes] = {
        "00_导出说明.txt": _readme(package).encode("utf-8"),
        "02_Brief/创作Brief.md": _brief_document(project_name, package.get("brief")).encode("utf-8"),
        "03_人物/人物设定.md": _characters_document(project_name, package.get("characters") or []).encode("utf-8"),
        "03_人物/人物关系.md": _relationships_document(project_name, package.get("relationships") or []).encode("utf-8"),
    }
    if package.get("breakdown"):
        files["01_原剧拆解/原剧拆解.md"] = build_breakdown_markdown(
            dict(package["breakdown"])
        ).encode("utf-8")
    recommendation_text = build_video_parameter_recommendation_text(
        package.get("video_parameter_recommendation")
    )
    for item in _ordered_episode_items(package.get("outlines") or []):
        number = int(item.get("episode_number") or 0)
        files[f"04_分集大纲/第{number:0{width}d}集_分集大纲.md"] = (
            _outline_document(project_name, item).encode("utf-8")
        )
    for item in _ordered_episode_items(package.get("beats") or []):
        number = int(item.get("episode_number") or 0)
        files[f"05_Beats/第{number:0{width}d}集_Beats.md"] = _beats_document(
            project_name, item
        ).encode("utf-8")
    for item in _ordered_episode_items(package.get("scripts") or []):
        number = int(item.get("episode_number") or 0)
        script_text = build_script_text(
            project, item.get("content") or {}, episode_number=number
        )
        files[f"06_剧本/第{number:0{width}d}集_剧本.txt"] = (
            f"{recommendation_text}\n{script_text}".encode("utf-8")
        )

    files["99_数据清单/production-package.json"] = json.dumps(
        package, ensure_ascii=False, indent=2
    ).encode("utf-8")
    checksum_lines = [
        f"{hashlib.sha256(content).hexdigest()}  {path}"
        for path, content in sorted(files.items())
    ]
    files["99_数据清单/SHA256SUMS.txt"] = (
        "\n".join(checksum_lines) + "\n"
    ).encode("utf-8")

    output = BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
    ) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compresslevel=9)
    return output.getvalue()


def _readme(package: dict[str, Any]) -> str:
    project = dict(package.get("project") or {})
    provenance = dict(package.get("provenance") or {})
    numbers = [int(number) for number in provenance.get("episode_numbers") or []]
    scope = "全剧" if provenance.get("export_scope") == "full" else _episode_scope(numbers)
    return "\n".join(
        [
            f"项目：{project.get('name') or '未命名项目'}",
            f"导出范围：{scope}",
            f"项目总集数：{provenance.get('project_total_episodes') or '-'}",
            f"本次导出集数：{len(numbers)}",
            f"版本指纹：{package.get('fingerprint') or '-'}",
            "",
            "目录说明：",
            "01_原剧拆解：仿写项目的锁定原剧拆解；原创项目不生成。",
            "02_Brief：创作 Brief。",
            "03_人物：人物设定与人物关系，分别保存。",
            "04_分集大纲：每集一个 Markdown 文件。",
            "05_Beats：每集一个 Markdown 文件。",
            "06_剧本：每集一个 TXT 场次剧本，文件开头附全剧 AI 视频规格建议。",
            "99_数据清单：原始锁定快照及文件校验值，供追溯使用。",
            "",
            "本压缩包仅包含导出时通过检查的锁定版本。",
        ]
    )


def _brief_document(project_name: str, brief: Any) -> str:
    item = dict(brief or {})
    lines = [f"# {project_name} · 创作 Brief", ""]
    lines.extend(_resource_meta(item))
    lines.extend(_render_mapping(item.get("content") or {}, level=2))
    return _finish(lines)


def _characters_document(project_name: str, characters: list[dict[str, Any]]) -> str:
    lines = [f"# {project_name} · 人物设定", "", f"共 {len(characters)} 位人物。", ""]
    for index, item in enumerate(characters, start=1):
        content = dict(item.get("content") or {})
        name = str(content.get("name") or content.get("character_name") or f"人物 {index}")
        lines.extend([f"## {index}. {name}", ""])
        lines.extend(_render_mapping(content, level=3))
    if not characters:
        lines.extend(["暂无人物设定。", ""])
    return _finish(lines)


def _relationships_document(project_name: str, relationships: list[dict[str, Any]]) -> str:
    lines = [f"# {project_name} · 人物关系", ""]
    for index, item in enumerate(relationships, start=1):
        content = dict(item.get("content") or {})
        source = content.get("from") or content.get("source") or content.get("character_a")
        target = content.get("to") or content.get("target") or content.get("character_b")
        title = f"{source} → {target}" if source or target else f"关系 {index}"
        lines.extend([f"## {index}. {title}", ""])
        lines.extend(_render_mapping(content, level=3))
    if not relationships:
        lines.extend(["暂无单独的人物关系记录。", ""])
    return _finish(lines)


def _outline_document(project_name: str, item: dict[str, Any]) -> str:
    number = int(item.get("episode_number") or 0)
    lines = [f"# {project_name} · 第 {number} 集分集大纲", ""]
    lines.extend(_resource_meta(item))
    lines.extend(_render_mapping(item.get("content") or {}, level=2))
    return _finish(lines)


def _beats_document(project_name: str, item: dict[str, Any]) -> str:
    number = int(item.get("episode_number") or 0)
    content = dict(item.get("content") or {})
    beats = content.pop("beats", [])
    lines = [f"# {project_name} · 第 {number} 集 Beats", ""]
    lines.extend(_resource_meta(item))
    if content:
        lines.extend(_render_mapping(content, level=2))
    for index, beat in enumerate(beats if isinstance(beats, list) else [beats], start=1):
        order = beat.get("order") if isinstance(beat, dict) else None
        lines.extend([f"## 节拍 {order or index}", ""])
        if isinstance(beat, dict):
            lines.extend(_render_mapping(beat, level=3))
        else:
            lines.extend([str(beat), ""])
    if not beats:
        lines.extend(["暂无节拍内容。", ""])
    return _finish(lines)


def _resource_meta(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if item.get("version") not in (None, ""):
        lines.extend([f"- **版本**：v{item['version']}", ""])
    if item.get("status"):
        lines.extend([f"- **状态**：{_status_label(item['status'])}", ""])
    return lines


def _render_mapping(value: Any, *, level: int) -> list[str]:
    if not isinstance(value, dict):
        return _render_value(value, level=level)
    lines: list[str] = []
    for key, item in value.items():
        label = _FIELD_LABELS.get(str(key), str(key).replace("_", " "))
        if isinstance(item, (dict, list)):
            lines.extend([f"{'#' * min(level, 6)} {label}", ""])
            lines.extend(_render_value(item, level=level + 1))
        else:
            lines.extend([f"- **{label}**：{_scalar(item)}", ""])
    return lines


def _render_value(value: Any, *, level: int) -> list[str]:
    if isinstance(value, dict):
        return _render_mapping(value, level=level)
    if isinstance(value, list):
        lines: list[str] = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, dict):
                lines.extend([f"{'#' * min(level, 6)} {index}", ""])
                lines.extend(_render_mapping(item, level=level + 1))
            elif isinstance(item, list):
                lines.extend([f"{'#' * min(level, 6)} {index}", ""])
                lines.extend(_render_value(item, level=level + 1))
            else:
                lines.append(f"- {_scalar(item)}")
        lines.append("")
        return lines
    return [_scalar(value), ""]


def _ordered_episode_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(item) for item in items),
        key=lambda item: (
            int(item.get("episode_number") or 0),
            int(item.get("version") or 0),
            int(item.get("id") or 0),
        ),
    )


def _episode_scope(numbers: list[int]) -> str:
    if not numbers:
        return "部分集"
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return f"第{'、'.join(ranges)}集"


def _status_label(value: Any) -> str:
    return {
        "draft": "草稿",
        "locked": "已锁定",
        "brief_locked": "Brief 已锁定",
        "approved": "已批准",
    }.get(str(value or ""), str(value or "-"))


def _scalar(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _finish(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"
