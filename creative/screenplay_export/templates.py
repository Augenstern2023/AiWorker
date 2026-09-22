# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Screenplay template registry and stable public identifiers."""

from __future__ import annotations

from dataclasses import asdict, dataclass


_DIALOGUE_COLOR = "#1D4ED8"


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """Semantic and page-layout rules consumed by every renderer."""

    id: str
    name: str
    short_name: str
    description: str
    previewable: bool
    formats: tuple[str, ...]
    title_format: str
    episode_header: str
    scene_header: str
    block_rendering: tuple[tuple[str, str], ...]
    front_matter: tuple[str, ...]
    page_break_policy: str
    metadata_line: str
    font_family: str
    font_size_pt: float
    title_size_pt: float
    episode_size_pt: float
    scene_size_pt: float
    page_margins_mm: tuple[float, float, float, float]
    header_text: str
    footer_text: str
    colors: tuple[tuple[str, str], ...]
    include_video_appendix: bool = False

    def block_rules(self) -> dict[str, str]:
        return dict(self.block_rendering)

    def color_rules(self) -> dict[str, str]:
        return dict(self.colors)

    def public_dict(self) -> dict[str, object]:
        """Return only stable metadata needed by the template picker."""

        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "description": self.description,
            "previewable": self.previewable,
            "formats": list(self.formats),
        }

    def layout_dict(self) -> dict[str, object]:
        """Expose the full declarative spec for tests and future adapters."""

        return asdict(self)


_COMMON_FULLWIDTH_BLOCKS = (
    ("action", "△ {text}"),
    ("dialogue", "{speaker}：{text}"),
    ("dialogue_with_modifier", "{speaker}（{modifier}）：{text}"),
    ("inner_voice", "{speaker}（内心OS）：{text}"),
    ("voiceover", "画外音：{text}"),
    ("voiceover_with_speaker", "画外音（{speaker}）：{text}"),
    ("cut", "【切镜：{text}】"),
    ("cut_empty", "【切镜】"),
)


_TEMPLATES = (
    TemplateSpec(
        id="classic",
        name="标准场次格式",
        short_name="标准场次",
        description="沿用现有场次、道具、人物和剧本标记，适合制作交付与旧版兼容。",
        previewable=True,
        formats=("md", "pdf", "docx"),
        title_format="《{drama}》场次剧本",
        episode_header="第 {number} 集：{title}",
        scene_header="[{sequence}]-[{scene}] [{time}] [{interior}] [{location}]",
        block_rendering=(
            ("action", "△ {text}"),
            ("dialogue", "{speaker}:{text}"),
            ("dialogue_with_modifier", "{speaker}({modifier}):{text}"),
            ("inner_voice", "{speaker}(内心OS):{text}"),
            ("voiceover", "画外音：{text}"),
            ("voiceover_with_speaker", "画外音（{speaker}）：{text}"),
            ("cut", "【切镜：{text}】"),
            ("cut_empty", "【切镜】"),
        ),
        front_matter=(),
        page_break_policy="section",
        metadata_line="道具:{props} 出场人物:{characters}",
        font_family="Microsoft YaHei",
        font_size_pt=11,
        title_size_pt=20,
        episode_size_pt=16,
        scene_size_pt=12,
        page_margins_mm=(22, 20, 22, 20),
        header_text="{drama}",
        footer_text="场次剧本",
        colors=(
            ("text", "#1f2937"),
            ("muted", "#6b7280"),
            ("accent", "#334155"),
            ("dialogue", _DIALOGUE_COLOR),
        ),
        include_video_appendix=True,
    ),
    TemplateSpec(
        id="wife_revenge",
        name="全案审阅格式",
        short_name="全案审阅",
        description="突出剧名、简介、人物小传和分集层级，适合阅读与审稿。",
        previewable=True,
        formats=("md", "pdf", "docx"),
        title_format="{drama}",
        episode_header="第{number_zh}集：{title}",
        scene_header="{sequence}-{scene} {location} {time} {interior}",
        block_rendering=_COMMON_FULLWIDTH_BLOCKS,
        front_matter=("synopsis", "character_bios", "series_overview", "script_heading"),
        page_break_policy="between_episodes",
        metadata_line="人物：{characters}",
        font_family="SimSun",
        font_size_pt=12,
        title_size_pt=22,
        episode_size_pt=16,
        scene_size_pt=12,
        page_margins_mm=(24, 22, 24, 22),
        header_text="",
        footer_text="{drama}",
        colors=(
            ("text", "#111111"),
            ("muted", "#555555"),
            ("accent", "#111111"),
            ("dialogue", _DIALOGUE_COLOR),
        ),
    ),
    TemplateSpec(
        id="character_outsource",
        name="紧凑编剧格式",
        short_name="紧凑编剧",
        description="动作与台词连续排布，场次结构紧凑，适合编剧流畅阅读。",
        previewable=True,
        formats=("md", "pdf", "docx"),
        title_format="《{drama}》剧本",
        episode_header="第 {number} 集：《{title}》",
        scene_header="场{sequence}-{scene}：{location} - {time} - {interior}",
        block_rendering=_COMMON_FULLWIDTH_BLOCKS,
        front_matter=(),
        page_break_policy="between_episodes",
        metadata_line="",
        font_family="Microsoft YaHei",
        font_size_pt=11.5,
        title_size_pt=21,
        episode_size_pt=15,
        scene_size_pt=12,
        page_margins_mm=(23, 21, 23, 21),
        header_text="《{drama}》剧本",
        footer_text="",
        colors=(
            ("text", "#161616"),
            ("muted", "#606060"),
            ("accent", "#202020"),
            ("dialogue", _DIALOGUE_COLOR),
        ),
    ),
    TemplateSpec(
        id="scholar_beggar",
        name="EP 分集格式",
        short_name="EP分集",
        description="采用 EP 集标识和中文全角场次标点，适合明确分集分页的交付稿。",
        previewable=True,
        formats=("md", "pdf", "docx"),
        title_format="《{drama}》",
        episode_header="EP{number_02} {title}",
        scene_header="场{sequence}-{scene}：{location}，{time}，{interior}",
        block_rendering=_COMMON_FULLWIDTH_BLOCKS,
        front_matter=(),
        page_break_policy="between_episodes",
        metadata_line="",
        font_family="SimSun",
        font_size_pt=12,
        title_size_pt=22,
        episode_size_pt=17,
        scene_size_pt=12,
        page_margins_mm=(25, 22, 25, 22),
        header_text="",
        footer_text="EP{first_number_02}-{last_number_02}",
        colors=(
            ("text", "#101010"),
            ("muted", "#505050"),
            ("accent", "#101010"),
            ("dialogue", _DIALOGUE_COLOR),
        ),
    ),
)

_TEMPLATE_BY_ID = {item.id: item for item in _TEMPLATES}


def list_template_specs() -> tuple[TemplateSpec, ...]:
    """Return templates in their stable product order."""

    return _TEMPLATES


def get_template_spec(template_id: str) -> TemplateSpec:
    """Resolve one public template ID or raise a stable validation error."""

    normalized = str(template_id or "").strip()
    try:
        return _TEMPLATE_BY_ID[normalized]
    except KeyError as exc:
        raise ValueError(f"未注册的剧本模板：{normalized or '-'}") from exc
