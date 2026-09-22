# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from io import BytesIO
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader
import pytest

from creative.api import build_episode_selection_download
from creative.screenplay_export import (
    CharacterExport,
    EpisodeExport,
    ExportMetadata,
    SceneExport,
    ScriptBlockExport,
    ScriptExportDocument,
    build_export_document,
    export_filename,
    list_template_specs,
    render_export_document,
)


TEMPLATE_IDS = [
    "classic",
    "wife_revenge",
    "character_outsource",
    "scholar_beggar",
]


def sample_document() -> ScriptExportDocument:
    blocks = (
        ScriptBlockExport("action", "<script>alert(1)</script>主角推门。"),
        ScriptBlockExport("dialogue", "你终于来了。", "林忆", "压低声音"),
        ScriptBlockExport("inner_voice", "不能让他发现。", "林忆"),
        ScriptBlockExport("voiceover", "雨越下越大。"),
        ScriptBlockExport("cut", "门把手的特写。"),
    )
    return ScriptExportDocument(
        drama_name="记忆/裂缝",
        synopsis="女孩寻找被篡改的记忆。",
        character_bios=(CharacterExport("林忆", "身份：记者；目标：查明真相"),),
        series_overview=(("主线目标", "找回记忆"),),
        episodes=(
            EpisodeExport(
                episode_number=1,
                title="裂缝初现",
                version=2,
                scenes=(
                    SceneExport(
                        sequence_no=1,
                        scene_no=1,
                        location="卧室",
                        time_of_day="夜",
                        interior_exterior="内",
                        characters=("林忆",),
                        props=(),
                        blocks=blocks,
                    ),
                ),
            ),
            EpisodeExport(
                episode_number=2,
                title="追踪",
                version=1,
                scenes=(),
            ),
        ),
        metadata=ExportMetadata(
            project_id=9,
            stage="script",
            episode_numbers=(1, 2),
            versions=(2, 1),
        ),
    )


def test_template_registry_exposes_four_previewable_templates() -> None:
    templates = list_template_specs()

    assert [item.id for item in templates] == TEMPLATE_IDS
    assert [item.name for item in templates] == [
        "标准场次格式",
        "全案审阅格式",
        "紧凑编剧格式",
        "EP 分集格式",
    ]
    assert not any("《" in item.name or "》" in item.name for item in templates)
    assert all(item.previewable for item in templates)
    assert all({"md", "pdf", "docx"} <= set(item.formats) for item in templates)
    assert all(item.scene_header and item.episode_header for item in templates)
    assert all(item.block_rendering and item.page_margins_mm for item in templates)


def test_normalize_selects_current_heads_sorts_and_deduplicates() -> None:
    project = {
        "id": 7,
        "name": "自由剧",
        "workflow_mode": "free",
        "target_episode_count": 3,
        "character_batch_id": "people-current",
        "characters": [
            {
                "id": 1,
                "batch_id": "people-current",
                "version": 1,
                "content": {"name": "林忆", "identity": "记者"},
            }
        ],
        "resource_heads": [
            {"stage": "script", "episode_number": 1, "resource_ref": "101"},
            {"stage": "script", "episode_number": 2, "resource_ref": "102"},
            {"stage": "script", "episode_number": 3, "resource_ref": "103"},
        ],
        "episodes": [
            {
                "id": 103,
                "stage": "script",
                "episode_number": 3,
                "version": 2,
                "content": {"episode_title": "三", "scenes": []},
            },
            {
                "id": 101,
                "stage": "script",
                "episode_number": 1,
                "version": 1,
                "content": {
                    "episode_title": "一",
                    "scenes": [{
                        "sequence_no": 1,
                        "scene_no": 2,
                        "blocks": [
                            {"type": "action", "text": "先动作"},
                            {"type": "dialogue", "speaker": "林忆", "text": "后台词"},
                        ],
                    }],
                },
            },
            {
                "id": 102,
                "stage": "script",
                "episode_number": 2,
                "version": 1,
                "content": {"episode_title": "二"},
            },
        ],
    }

    selected = build_export_document(
        project,
        stage="script",
        episode_numbers=[3, 1, 3],
    )
    all_episodes = build_export_document(project, stage="script", episode_numbers=None)

    assert [item.episode_number for item in selected.episodes] == [1, 3]
    assert [item.episode_number for item in all_episodes.episodes] == [1, 2, 3]
    assert [item.type for item in selected.episodes[0].scenes[0].blocks] == [
        "action",
        "dialogue",
    ]
    assert all_episodes.episodes[1].scenes == ()
    assert selected.character_bios[0].name == "林忆"


def test_normalize_rejects_an_episode_without_a_current_downloadable_head() -> None:
    project = {
        "id": 1,
        "name": "空项目",
        "workflow_mode": "free",
        "target_episode_count": 2,
        "episodes": [],
        "resource_heads": [],
    }

    with pytest.raises(ValueError, match="以下集数没有当前可下载内容：2"):
        build_export_document(project, stage="script", episode_numbers=[2])


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_markdown_and_html_share_order_and_escape_active_content(template_id: str) -> None:
    document = sample_document()
    markdown = render_export_document(document, template_id=template_id, format="md").decode()
    html = render_export_document(document, template_id=template_id, format="html").decode()

    assert "裂缝初现" in markdown
    assert "林忆" in markdown
    assert markdown.index("主角推门") < markdown.index("你终于来了")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "{'" not in markdown


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_docx_is_a_readable_template_specific_document(template_id: str) -> None:
    body = render_export_document(sample_document(), template_id=template_id, format="docx")

    with ZipFile(BytesIO(body)) as archive:
        assert "word/document.xml" in archive.namelist()
    reopened = Document(BytesIO(body))
    text = "\n".join(paragraph.text for paragraph in reopened.paragraphs)
    assert "裂缝初现" in text
    assert "主角推门" in text


def test_pdf_is_readable_and_uses_a4_pages() -> None:
    body = render_export_document(sample_document(), template_id="classic", format="pdf")

    assert body.startswith(b"%PDF")
    reader = PdfReader(BytesIO(body))
    assert len(reader.pages) >= 1


@pytest.mark.parametrize("template_id", TEMPLATE_IDS)
def test_export_filename_is_safe_and_keeps_original_drama_scope(
    template_id: str,
) -> None:
    filename = export_filename(
        sample_document(), template_id=template_id, format="docx"
    )

    assert filename == "记忆_裂缝第1集-第2集.docx"
    assert "模板" not in filename
    assert not any(character in filename for character in '\\/:*?"<>|')


def test_legacy_txt_download_uses_the_ast_without_changing_its_contract() -> None:
    project = {
        "id": 8,
        "name": "兼容剧",
        "workflow_mode": "free",
        "target_episode_count": 1,
        "resource_heads": [
            {"stage": "script", "episode_number": 1, "resource_ref": "201"}
        ],
        "episodes": [{
            "id": 201,
            "stage": "script",
            "episode_number": 1,
            "version": 1,
            "content": {
                "episode_title": "开局",
                "scenes": [{
                    "sequence_no": 1,
                    "scene_no": 1,
                    "time_of_day": "日",
                    "interior_exterior": "内",
                    "location": "客厅",
                    "props": [],
                    "characters": ["主角"],
                    "blocks": [{"type": "action", "text": "主角进门。"}],
                }],
            },
        }],
    }

    filename, body, media_type = build_episode_selection_download(
        project, "script", [1], "txt"
    )

    assert filename == "兼容剧第1集场次剧本.txt"
    assert media_type == "text/plain; charset=utf-8"
    assert body.decode() == (
        "==================== 第 1 集 ====================\n\n"
        "第 1 集:开局\n\n"
        "[1]-[1] [日] [内] [客厅]\n"
        "道具:无 出场人物:主角\n\n"
        "△ 主角进门。\n"
    )
