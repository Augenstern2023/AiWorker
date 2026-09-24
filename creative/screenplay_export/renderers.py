# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""HTML, Markdown, DOCX, PDF and legacy TXT screenplay renderers."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from typing import Literal

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from creative.screenplay_export.model import (
    EpisodeExport,
    SceneExport,
    ScriptBlockExport,
    ScriptExportDocument,
)
from creative.screenplay_export.templates import TemplateSpec, get_template_spec


ExportFormat = Literal["html", "md", "pdf", "docx", "txt"]

_MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
}


def _chinese_number(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 0:
        return f"负{_chinese_number(-number)}"
    if number < 10:
        return digits[number]
    if number < 20:
        return "十" + (digits[number % 10] if number % 10 else "")
    if number < 100:
        return digits[number // 10] + "十" + (digits[number % 10] if number % 10 else "")
    return str(number)


def _format_values(
    document: ScriptExportDocument,
    episode: EpisodeExport | None = None,
    scene: SceneExport | None = None,
) -> dict[str, object]:
    first = document.episodes[0].episode_number if document.episodes else 0
    last = document.episodes[-1].episode_number if document.episodes else 0
    number = episode.episode_number if episode else 0
    return {
        "drama": document.drama_name,
        "number": number,
        "number_02": f"{number:02d}",
        "number_zh": _chinese_number(number),
        "title": episode.title if episode else "",
        "sequence": scene.sequence_no if scene else 0,
        "scene": scene.scene_no if scene else 0,
        "time": scene.time_of_day if scene else "",
        "interior": scene.interior_exterior if scene else "",
        "location": scene.location if scene else "",
        "characters": "、".join(scene.characters) if scene and scene.characters else "无",
        "props": "、".join(scene.props) if scene and scene.props else "无",
        "first_number": first,
        "last_number": last,
        "first_number_02": f"{first:02d}",
        "last_number_02": f"{last:02d}",
    }


def _clean_spacing(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value).strip()


def _format_episode(document: ScriptExportDocument, episode: EpisodeExport, spec: TemplateSpec) -> str:
    return _clean_spacing(spec.episode_header.format(**_format_values(document, episode)))


def _format_scene(
    document: ScriptExportDocument,
    episode: EpisodeExport,
    scene: SceneExport,
    spec: TemplateSpec,
) -> str:
    return _clean_spacing(spec.scene_header.format(**_format_values(document, episode, scene)))


def _format_metadata(
    document: ScriptExportDocument,
    episode: EpisodeExport,
    scene: SceneExport,
    spec: TemplateSpec,
) -> str:
    if not spec.metadata_line:
        return ""
    return _clean_spacing(spec.metadata_line.format(**_format_values(document, episode, scene)))


def _format_block(block: ScriptBlockExport, spec: TemplateSpec) -> str:
    rules = spec.block_rules()
    values = {
        "speaker": block.speaker,
        "modifier": block.modifier,
        "text": block.text,
    }
    if block.type == "dialogue":
        key = "dialogue_with_modifier" if block.modifier else "dialogue"
    elif block.type == "voiceover":
        key = "voiceover_with_speaker" if block.speaker else "voiceover"
    elif block.type == "cut":
        key = "cut" if block.text else "cut_empty"
    else:
        key = block.type
    pattern = rules.get(key)
    return pattern.format(**values).strip() if pattern else ""


def _render_view(document: ScriptExportDocument, spec: TemplateSpec) -> dict[str, object]:
    episodes = []
    for episode in document.episodes:
        scenes = []
        for scene in episode.scenes:
            scenes.append(
                {
                    "header": _format_scene(document, episode, scene, spec),
                    "metadata": _format_metadata(document, episode, scene, spec),
                    "blocks": [
                        {"type": block.type, "text": rendered}
                        for block in scene.blocks
                        if (rendered := _format_block(block, spec))
                    ],
                }
            )
        episodes.append(
            {
                "number": episode.episode_number,
                "header": _format_episode(document, episode, spec),
                "scenes": scenes,
            }
        )
    return {
        "title": spec.title_format.format(**_format_values(document)),
        "synopsis": document.synopsis if "synopsis" in spec.front_matter else "",
        "character_bios": document.character_bios if "character_bios" in spec.front_matter else (),
        "series_overview": document.series_overview if "series_overview" in spec.front_matter else (),
        "show_script_heading": "script_heading" in spec.front_matter,
        "appendix": (
            document.metadata.video_parameter_appendix
            if spec.include_video_appendix
            else ""
        ),
        "episodes": episodes,
    }


def _markdown_safe(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_markdown(document: ScriptExportDocument, spec: TemplateSpec) -> bytes:
    view = _render_view(document, spec)
    dialogue_color = spec.color_rules()["dialogue"]
    lines = [f"# {_markdown_safe(str(view['title']))}", ""]
    if view["synopsis"]:
        lines.extend(["## 简介", "", _markdown_safe(str(view["synopsis"])), ""])
    if view["character_bios"]:
        lines.extend(["## 人物小传", ""])
        for character in view["character_bios"]:
            detail = f"：{character.description}" if character.description else ""
            lines.extend([f"- **{_markdown_safe(character.name)}**{_markdown_safe(detail)}", ""])
    if view["series_overview"]:
        lines.extend(["## 全集概览", ""])
        for label, value in view["series_overview"]:
            lines.extend([f"- **{_markdown_safe(label)}**：{_markdown_safe(value)}", ""])
    if view["show_script_heading"]:
        lines.extend(["## 剧本", ""])
    for episode in view["episodes"]:
        lines.extend([f"## {_markdown_safe(episode['header'])}", ""])
        for scene in episode["scenes"]:
            lines.extend([f"### {_markdown_safe(scene['header'])}", ""])
            if scene["metadata"]:
                lines.extend([_markdown_safe(scene["metadata"]), ""])
            for block in scene["blocks"]:
                text = _markdown_safe(block["text"])
                if block["type"] == "dialogue":
                    text = (
                        f'<span style="color: {dialogue_color}; font-weight: 600;">'
                        f"{text}</span>"
                    )
                lines.extend([text, ""])
        if spec.page_break_policy == "between_episodes":
            lines.extend(["<!-- pagebreak -->", ""])
    if view["appendix"]:
        lines.extend(["## AI 视频规格建议", "", _markdown_safe(str(view["appendix"]).strip()), ""])
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _preview_css(spec: TemplateSpec) -> str:
    colors = spec.color_rules()
    top, right, bottom, left = spec.page_margins_mm
    return f"""
        @page {{ size: A4; margin: {top}mm {right}mm {bottom}mm {left}mm; }}
        :root {{ color-scheme: light; }}
        * {{ box-sizing: border-box; }}
        html {{ background: #eef1f5; }}
        body {{ margin: 0; color: {colors['text']}; font-family: '{spec.font_family}',
                'Noto Sans CJK SC', sans-serif; font-size: {spec.font_size_pt}pt;
                line-height: 1.72; }}
        .screenplay-page {{ width: 210mm; min-height: 297mm; margin: 18px auto;
                padding: {top}mm {right}mm {bottom}mm {left}mm; background: white;
                box-shadow: 0 2px 18px rgba(15, 23, 42, .12); }}
        h1 {{ margin: 0 0 1.8em; text-align: center; font-size: {spec.title_size_pt}pt;
              line-height: 1.35; color: {colors['accent']}; }}
        h2 {{ margin: 1.8em 0 .85em; font-size: {spec.episode_size_pt}pt;
              line-height: 1.4; color: {colors['accent']}; break-after: avoid; }}
        h3 {{ margin: 1.3em 0 .55em; font-size: {spec.scene_size_pt}pt;
              line-height: 1.45; color: {colors['accent']}; break-after: avoid; }}
        .front-heading {{ font-size: {spec.episode_size_pt - 1}pt; }}
        .metadata {{ color: {colors['muted']}; margin: 0 0 .8em; }}
        .script-block {{ margin: 0 0 .72em; white-space: pre-wrap; overflow-wrap: anywhere; }}
        .script-block.action {{ padding-left: 1em; }}
        .script-block.dialogue {{ color: {colors['dialogue']}; font-weight: 600; }}
        .character-bio {{ margin: 0 0 .72em; }}
        .episode + .episode {{
            {'break-before: page;' if spec.page_break_policy == 'between_episodes' else ''}
        }}
        .appendix {{ white-space: pre-wrap; color: {colors['muted']}; }}
        @media print {{ html {{ background: white; }} .screenplay-page {{ width: auto;
            min-height: 0; margin: 0; padding: 0; box-shadow: none; }} }}
        @media (max-width: 860px) {{ .screenplay-page {{ width: 100%; min-height: 0; margin: 0;
            padding: 24px 20px; box-shadow: none; }} }}
    """


def _render_html(document: ScriptExportDocument, spec: TemplateSpec) -> bytes:
    template_root = Path(__file__).resolve().parents[2] / "templates" / "common" / "workspace"
    environment = Environment(
        loader=FileSystemLoader(template_root),
        autoescape=select_autoescape(("html", "xml")),
        undefined=StrictUndefined,
    )
    template = environment.get_template("creative_screenplay_export_preview.html")
    body = template.render(
        template_id=spec.id,
        css=_preview_css(spec),
        **_render_view(document, spec),
    )
    return body.encode("utf-8")


def _set_run_font(run, family: str, size: float, *, bold: bool = False, color: str = "#000000") -> None:
    run.font.name = family
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), family)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color.lstrip("#"))


def _add_page_number(paragraph) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend((begin, instruction, end))


def _add_docx_paragraph(
    document: Document,
    text: str,
    *,
    family: str,
    size: float,
    bold: bool = False,
    color: str = "#000000",
    before: float = 0,
    after: float = 6,
    keep_with_next: bool = False,
    indent_mm: float = 0,
    alignment=None,
):
    paragraph = document.add_paragraph()
    if alignment is not None:
        paragraph.alignment = alignment
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = 1.35
    paragraph.paragraph_format.keep_with_next = keep_with_next
    if indent_mm:
        paragraph.paragraph_format.left_indent = Mm(indent_mm)
    run = paragraph.add_run(text)
    _set_run_font(run, family, size, bold=bold, color=color)
    return paragraph


def _render_docx(document: ScriptExportDocument, spec: TemplateSpec) -> bytes:
    output = Document()
    section = output.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    top, right, bottom, left = spec.page_margins_mm
    section.top_margin = Mm(top)
    section.right_margin = Mm(right)
    section.bottom_margin = Mm(bottom)
    section.left_margin = Mm(left)
    colors = spec.color_rules()

    normal = output.styles["Normal"]
    normal.font.name = spec.font_family
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), spec.font_family)
    normal.font.size = Pt(spec.font_size_pt)

    values = _format_values(document)
    header_text = spec.header_text.format(**values).strip()
    if header_text:
        paragraph = section.header.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(paragraph.add_run(header_text), spec.font_family, 9, color=colors["muted"])
    footer_text = spec.footer_text.format(**values).strip()
    if footer_text:
        paragraph = section.footer.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(paragraph.add_run(f"{footer_text} · "), spec.font_family, 9, color=colors["muted"])
        _add_page_number(paragraph)

    view = _render_view(document, spec)
    _add_docx_paragraph(
        output,
        str(view["title"]),
        family=spec.font_family,
        size=spec.title_size_pt,
        bold=True,
        color=colors["accent"],
        after=24,
        keep_with_next=True,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
    )
    if view["synopsis"]:
        _add_docx_paragraph(output, "简介", family=spec.font_family, size=spec.episode_size_pt - 1,
                            bold=True, before=8, after=8, keep_with_next=True)
        _add_docx_paragraph(output, str(view["synopsis"]), family=spec.font_family,
                            size=spec.font_size_pt, after=10)
    if view["character_bios"]:
        _add_docx_paragraph(output, "人物小传", family=spec.font_family,
                            size=spec.episode_size_pt - 1, bold=True, before=8, after=8,
                            keep_with_next=True)
        for character in view["character_bios"]:
            detail = f"：{character.description}" if character.description else ""
            _add_docx_paragraph(output, f"{character.name}{detail}", family=spec.font_family,
                                size=spec.font_size_pt, after=7)
    if view["series_overview"]:
        _add_docx_paragraph(output, "全集概览", family=spec.font_family,
                            size=spec.episode_size_pt - 1, bold=True, before=8, after=8,
                            keep_with_next=True)
        for label, value in view["series_overview"]:
            _add_docx_paragraph(output, f"{label}：{value}", family=spec.font_family,
                                size=spec.font_size_pt, after=6)
    if view["show_script_heading"]:
        _add_docx_paragraph(output, "剧本", family=spec.font_family,
                            size=spec.episode_size_pt, bold=True, before=12, after=8,
                            keep_with_next=True)

    for episode_index, episode in enumerate(view["episodes"]):
        if episode_index and spec.page_break_policy == "between_episodes":
            output.add_page_break()
        _add_docx_paragraph(output, episode["header"], family=spec.font_family,
                            size=spec.episode_size_pt, bold=True, color=colors["accent"],
                            before=14, after=10, keep_with_next=True)
        for scene in episode["scenes"]:
            _add_docx_paragraph(output, scene["header"], family=spec.font_family,
                                size=spec.scene_size_pt, bold=True, color=colors["accent"],
                                before=11, after=5, keep_with_next=True)
            if scene["metadata"]:
                _add_docx_paragraph(output, scene["metadata"], family=spec.font_family,
                                    size=spec.font_size_pt - .5, color=colors["muted"], after=7,
                                    keep_with_next=bool(scene["blocks"]))
            for block in scene["blocks"]:
                _add_docx_paragraph(output, block["text"], family=spec.font_family,
                                    size=spec.font_size_pt,
                                    bold=block["type"] == "dialogue",
                                    color=(colors["dialogue"] if block["type"] == "dialogue"
                                           else "#000000"),
                                    after=7,
                                    indent_mm=5 if block["type"] == "action" else 0)
    if view["appendix"]:
        output.add_page_break()
        _add_docx_paragraph(output, "AI 视频规格建议", family=spec.font_family,
                            size=spec.episode_size_pt, bold=True, after=10,
                            keep_with_next=True)
        for line in str(view["appendix"]).strip().splitlines():
            _add_docx_paragraph(output, line, family=spec.font_family,
                                size=spec.font_size_pt - .5, color=colors["muted"], after=4)

    buffer = BytesIO()
    output.save(buffer)
    return buffer.getvalue()


def _render_legacy_txt(document: ScriptExportDocument, spec: TemplateSpec) -> bytes:
    sections = []
    for episode in document.episodes:
        lines = [f"第 {episode.episode_number} 集:{episode.title}", ""]
        for scene in episode.scenes:
            lines.extend(
                [
                    _format_scene(document, episode, scene, spec),
                    _format_metadata(document, episode, scene, spec),
                    "",
                ]
            )
            for block in scene.blocks:
                rendered = _format_block(block, spec)
                if rendered:
                    lines.extend([rendered, ""])
        episode_text = "\n".join(lines).rstrip() + "\n"
        sections.append(
            "\n".join(
                [
                    f"==================== 第 {episode.episode_number} 集 ====================",
                    "",
                    episode_text.rstrip(),
                ]
            )
        )
    text = "\n\n".join(sections).rstrip() + "\n"
    if document.metadata.video_parameter_appendix:
        text = f"{document.metadata.video_parameter_appendix}\n{text}"
    return text.encode("utf-8")


def _render_pdf(document: ScriptExportDocument, spec: TemplateSpec) -> bytes:
    from playwright.sync_api import sync_playwright  # pylint: disable=import-outside-toplevel

    html = _render_html(document, spec).decode("utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            page.evaluate("document.fonts && document.fonts.ready")
            page.emulate_media(media="print")
            return page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=False,
            )
        finally:
            browser.close()


def render_export_document(
    document: ScriptExportDocument,
    *,
    template_id: str,
    format: ExportFormat,
) -> bytes:
    """Render one AST through one registered template into the requested format."""

    spec = get_template_spec(template_id)
    if format not in {"html", "md", "pdf", "docx", "txt"}:
        raise ValueError(f"不支持的剧本导出格式：{format}")
    if format == "txt" and template_id != "classic":
        raise ValueError("TXT 兼容导出仅支持经典模板")
    if format == "html":
        return _render_html(document, spec)
    if format == "md":
        return _render_markdown(document, spec)
    if format == "docx":
        return _render_docx(document, spec)
    if format == "pdf":
        return _render_pdf(document, spec)
    return _render_legacy_txt(document, spec)


def export_media_type(format: str) -> str:
    try:
        return _MEDIA_TYPES[format]
    except KeyError as exc:
        raise ValueError(f"不支持的剧本导出格式：{format}") from exc


def _safe_filename_part(value: str, *, limit: int = 80) -> str:
    normalized = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(value or ""))
    normalized = re.sub(r"\s+", " ", normalized).strip(" .") or "剧本"
    return normalized[:limit].rstrip(" .") or "剧本"


def export_filename(
    document: ScriptExportDocument,
    *,
    template_id: str,
    format: str,
) -> str:
    """Build a Windows-safe filename without exposing the layout template."""

    get_template_spec(template_id)
    numbers = list(document.metadata.episode_numbers)
    if len(numbers) == 1:
        scope = f"第{numbers[0]}集"
    elif numbers and numbers == list(range(numbers[0], numbers[-1] + 1)):
        scope = f"第{numbers[0]}集-第{numbers[-1]}集"
    elif numbers:
        scope = "第" + "、".join(map(str, numbers)) + "集"
    else:
        scope = "全集"
    if len(scope) > 64 and numbers:
        scope = f"第{numbers[0]}集等{len(numbers)}集"
    suffix = scope
    name = _safe_filename_part(
        document.drama_name,
        limit=max(20, 180 - len(suffix)),
    )
    extension = re.sub(r"[^a-z0-9]", "", format.lower()) or "bin"
    return f"{name}{suffix}.{extension}"
